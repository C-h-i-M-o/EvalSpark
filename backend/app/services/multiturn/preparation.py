"""报告准备调用独立入账；只保存固定原文的提取结果，不允许自动重试。"""
import json
from dataclasses import asdict
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ModelClient, ModelReply
from app.models.conversation import ConversationAssessment, ConversationJudgeRun, ConversationUsage
from app.models.user import User
from app.services.multiturn.judge import JudgeSource, JudgePacket
from app.services.multiturn.opportunities import OpportunityResult, extract_opportunities
from app.services.multiturn.opportunity_packets import build_opportunity_packets
from app.services.multiturn.assessment_batches import validate_batches
from app.services.multiturn.store import ConversationError, ConversationStore
from app.services.token_quota_service import token_quota_service


class PreparationStore:
    """以报告作业锁串行登记提取，准备结果与正式 JudgeRun 分开存储。"""

    async def _job(self, db: AsyncSession, job_id: int, owner: int) -> ConversationAssessment:
        """校验作者、运行状态和正式报告范围，并锁住状态转换边界。"""
        job = await db.scalar(select(ConversationAssessment).where(ConversationAssessment.id == job_id)
            .with_for_update().execution_options(populate_existing=True))
        if job is None:
            raise ConversationError("assessment_not_found", "评分作业不存在", 404)
        await ConversationStore().get(db, job.conversation_id, owner, owner_only=True)
        if (job.status != "running" or not job.formal or job.input_json["packet"]["scope"] != "session"
                or "report" not in job.input_json or job.input_json.get("preparationFrozen")):
            raise ConversationError("preparation_state_conflict", "报告不处于可准备状态", 409)
        started = await db.scalar(select(ConversationJudgeRun.id).where(
            ConversationJudgeRun.assessment_id == job_id).limit(1))
        if started is not None:
            raise ConversationError("preparation_state_conflict", "正式评审已经开始，不能改动准备结果", 409)
        return job

    async def _usage(self, db: AsyncSession, job: ConversationAssessment, owner: int, index: int) -> ConversationUsage | None:
        """只读取当前报告作者对应的准备流水，避免混入其他阶段。"""
        if type(index) is not int or index < 1:
            raise ConversationError("preparation_invalid", "准备调用序号必须为正整数", 422)
        return await db.scalar(select(ConversationUsage).where(
            ConversationUsage.conversation_id == job.conversation_id, ConversationUsage.user_id == owner,
            ConversationUsage.stage == "opportunity",
            ConversationUsage.operation_key == f"assessment:{job.id}:opportunity:{index}")
            .with_for_update().execution_options(populate_existing=True))

    async def begin(self, db: AsyncSession, job_id: int, owner: int, index: int,
                    sources: tuple[JudgeSource, ...]) -> bool:
        """登记单次收费调用；重复输入返回 False，冲突输入拒绝，均不得再次调用。"""
        job = await self._job(db, job_id, owner)
        material = [source.model_dump(mode="json") for source in sources]
        frozen = {source["id"]: source for source in job.input_json["packet"]["sources"]}
        if (not material or len({source.id for source in sources}) != len(sources)
                or any(frozen.get(source["id"]) != source for source in material)):
            raise ConversationError("preparation_invalid", "提取来源与报告原文快照不一致", 422)
        digest = sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        usage = await self._usage(db, job, owner, index)
        if usage is not None:
            if usage.detail_json.get("snapshotHash") != digest:
                raise ConversationError("preparation_key_conflict", "准备序号已用于其他原文", 409)
            await db.commit()
            return False
        user = await db.get(User, owner, populate_existing=True)
        if user is None or user.status != "active":
            raise ConversationError("assessment_user_disabled", "用户不存在或已停用", 403)
        await token_quota_service.ensure_can_start(db, user)
        db.add(ConversationUsage(conversation_id=job.conversation_id, user_id=owner,
            operation_key=f"assessment:{job.id}:opportunity:{index}", stage="opportunity", status="pending",
            currency=job.input_json["currency"], accounted=False, detail_json={"assessmentId": job.id,
                "preparationIndex": index, "sourceIds": [source.id for source in sources], "snapshotHash": digest}))
        await db.commit()
        return True

    async def save_usage(self, db: AsyncSession, job_id: int, owner: int, index: int,
                         reply: ModelReply | None, client: ModelClient) -> None:
        """解析前结算实际用量，重复回调不会把已知用量覆盖成未知。"""
        job = await self._job(db, job_id, owner)
        usage = await self._usage(db, job, owner, index)
        if usage is None:
            raise ConversationError("preparation_state_conflict", "准备调用尚未登记", 409)
        if usage.status != "pending":
            await db.commit()
            return
        known = reply is not None and reply.usage_known
        usage.status = "completed" if known else "unknown"
        usage.total_tokens = reply.usage.total_tokens if known else None
        usage.cost = client.estimate_cost(reply.usage) if known else None
        usage.detail_json = {**usage.detail_json, "usage": asdict(reply.usage) if known else None}
        await db.flush()
        await token_quota_service.record_conversation_usage(db, usage_id=usage.id, user_id=owner)
        await db.commit()

    async def save_result(self, db: AsyncSession, job_id: int, owner: int, index: int,
                          result: OpportunityResult) -> None:
        """单独保存校验后的结果；用量未结算或已保存的冲突结果不允许覆盖。"""
        job = await self._job(db, job_id, owner)
        usage = await self._usage(db, job, owner, index)
        if usage is None or usage.status == "pending":
            raise ConversationError("preparation_state_conflict", "必须先结束准备调用用量", 409)
        value = {"status": result.status, "unresolved": list(result.unresolved), "errorCode": result.error_code,
            "opportunities": [{"id": item.id, "dimension": item.dimension, "description": item.description,
                "trigger": item.trigger.model_dump(mode="json"), "target": item.target.model_dump(mode="json"),
                "supporting": [reference.model_dump(mode="json") for reference in item.supporting]}
                for item in result.opportunities]}
        previous = usage.detail_json.get("result")
        if previous is not None and previous != value:
            raise ConversationError("preparation_state_conflict", "已保存的准备结果不能被改写", 409)
        usage.detail_json = {**usage.detail_json, "result": value}
        await db.commit()

    async def freeze(self, db: AsyncSession, job_id: int, owner: int, packet: JudgePacket,
                     batches: tuple[JudgePacket, ...]) -> None:
        """核对持久化机会及原有要求后，一次事务固定正式输入并保存原始计划。"""
        job = await self._job(db, job_id, owner)
        snapshot = job.input_json
        original = JudgePacket.model_validate_json(json.dumps(snapshot["packet"]))
        if (packet.sources != original.sources or packet.through_turn != original.through_turn
                or packet.answer != original.answer):
            raise ConversationError("preparation_invalid", "正式输入不得更改报告原文和范围", 422)
        validate_batches(packet, batches, snapshot["inputBudget"])
        usages = (await db.scalars(select(ConversationUsage).where(
            ConversationUsage.conversation_id == job.conversation_id, ConversationUsage.user_id == owner,
            ConversationUsage.stage == "opportunity",
            ConversationUsage.operation_key.like(f"assessment:{job_id}:opportunity:%"))
            .order_by(ConversationUsage.id).with_for_update())).all()
        if not usages or any(usage.status == "pending" or "result" not in usage.detail_json for usage in usages):
            raise ConversationError("preparation_state_conflict", "准备结果尚未完整保存", 409)
        protected = {check.id: check for check in original.checks if not check.id.startswith("window:")}
        expected = dict(protected)
        canonical: dict[str, JudgePacket] = {}
        limitations = list(snapshot["report"].get("limitations", []))
        from app.services.multiturn.semantic_report import read_discovery, scoped_discoveries
        discoveries = tuple(read_discovery(usage.detail_json["result"]) for usage in usages)
        version = snapshot["report"].get("semanticPreparation")
        if version in (1, 2, 3):
            from app.services.multiturn.assessment_batches import snapshot_batches
            discoveries = scoped_discoveries(snapshot_batches(snapshot), discoveries, snapshot["inputBudget"], version)
        if version == 3:
            from app.services.multiturn.report_relations import consolidate_discoveries
            discoveries = (consolidate_discoveries(discoveries),)
        for discovery in discoveries:
            if discovery.status == "failed":
                raise ConversationError("preparation_state_conflict", "机会提取失败，不能固定正式评分输入", 409)
            plan = build_opportunity_packets(original.sources, discovery,
                through_turn=original.through_turn, input_budget=snapshot["inputBudget"])
            limitations.extend(plan.limitations)
            for batch in plan.batches:
                check = batch.checks[0]
                fixed = batch.model_copy(update={"answer": original.answer})
                if check.id in protected or (check.id in canonical and canonical[check.id] != fixed):
                    raise ConversationError("preparation_invalid", "不同准备结果的机会标识冲突", 422)
                expected[check.id], canonical[check.id] = check, fixed
        if version in (1, 2, 3):
            from app.services.multiturn.assessment_batches import snapshot_batches
            from app.services.multiturn.semantic_report import build_semantic_plan, discovery_inputs
            old_batches = snapshot_batches(snapshot)
            windows = discovery_inputs(old_batches, snapshot["inputBudget"], version)
            if len(windows) != len(usages) or any(usage.detail_json["sourceIds"] != [source.id for source in window.sources]
                    for usage, window in zip(usages, windows)):
                raise ConversationError("preparation_invalid", "准备调用没有完整覆盖固定窗口", 422)
            planned = build_semantic_plan(original, old_batches,
                tuple(read_discovery(usage.detail_json["result"]) for usage in usages), snapshot["inputBudget"], version=version)
            if packet != planned.packet or batches != planned.batches:
                raise ConversationError("preparation_invalid", "正式计划与固定窗口提取结果不一致", 422)
            expected = {check.id: check for check in planned.packet.checks}
            limitations.extend(planned.limitations)
        if {check.id: check for check in packet.checks} != expected:
            raise ConversationError("preparation_invalid", "正式检查项与准备机会或原有要求不一致", 422)
        for batch in batches:
            for check in batch.checks:
                if check.id in canonical and batch != canonical[check.id]:
                    raise ConversationError("preparation_invalid", "机会必须独立评审完整原文区间", 422)
        # 所有校验先完成，事务一次替换，三次正式评审读取同一份最终分段。
        previous = {"packet": snapshot["packet"]}
        if "batches" in snapshot:
            previous["batches"] = snapshot["batches"]
        job.input_json = {**snapshot, "originalReportInput": previous, "preparationFrozen": True,
            "packet": packet.model_dump(mode="json"), "batches": [batch.model_dump(mode="json") for batch in batches],
            "report": {**snapshot["report"], "limitations": list(dict.fromkeys(limitations)),
                "preparationUsageIds": [usage.id for usage in usages], "semanticOpportunityCount": len(canonical)}}
        await db.commit()


async def execute_preparation(sessions: async_sessionmaker[AsyncSession], job_id: int, owner: int,
                              index: int, sources: tuple[JudgeSource, ...], client: ModelClient, *,
                              through_turn: int, input_budget: int, output_tokens: int) -> OpportunityResult:
    """调用提取器并逐次持久化；供应商请求期间不持有数据库事务。"""
    store = PreparationStore()

    async def before() -> None:
        """收费前核对固定预算及截止轮次，并登记唯一调用流水。"""
        async with sessions() as db:
            job = await store._job(db, job_id, owner)
            if through_turn != job.through_turn or input_budget != job.input_json["inputBudget"]:
                raise ConversationError("preparation_invalid", "准备范围或预算与报告快照不一致", 422)
            if not await store.begin(db, job_id, owner, index, sources):
                raise ConversationError("preparation_already_started", "该提取调用已登记，不能自动重发", 409)

    async def after(reply: ModelReply | None) -> None:
        """模型返回或取消后先记录实际/未知用量，再进行结果解析。"""
        async with sessions() as db:
            await store.save_usage(db, job_id, owner, index, reply, client)

    result = await extract_opportunities(client, sources, through_turn=through_turn,
        input_budget=input_budget, output_tokens=output_tokens, before_call=before, after_call=after)
    async with sessions() as db:
        await store.save_result(db, job_id, owner, index, result)
    return result

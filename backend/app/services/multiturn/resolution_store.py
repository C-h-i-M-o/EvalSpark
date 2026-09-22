"""问题状态调用使用独立流水，固定历史失败材料并防止重复付费。"""
import json
from dataclasses import asdict
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ModelClient, ModelReply
from app.models.conversation import ConversationAssessment, ConversationJudgeRun, ConversationUsage
from app.models.user import User
from app.schemas.multiturn import CheckItem
from app.services.multiturn.assessment_batches import batch_count
from app.services.multiturn.issue_resolution import ResolutionPacket, ResolutionRun, build_resolution_packet, resolution_messages, parse_resolution, evaluate_resolution
from app.services.multiturn.judge import JudgePacket
from app.services.multiturn.store import ConversationError, ConversationStore
from app.services.token_quota_service import token_quota_service


class ResolutionStore:
    """在报告锁下核对失败共识，供应商请求始终在事务外执行。"""

    async def _job(self, db: AsyncSession, job_id: int, owner: int, *, running: bool = True) -> ConversationAssessment:
        """只有作者的正式会话报告可写状态，费用回调允许在中断后收尾。"""
        job = await db.scalar(select(ConversationAssessment).where(ConversationAssessment.id == job_id)
            .with_for_update().execution_options(populate_existing=True))
        if job is None:
            raise ConversationError("assessment_not_found", "评分作业不存在", 404)
        await ConversationStore().get(db, job.conversation_id, owner, owner_only=True)
        if not job.formal or job.input_json["packet"]["scope"] != "session" or "report" not in job.input_json:
            raise ConversationError("resolution_invalid", "问题状态只能附属于正式会话报告", 422)
        if running and job.status != "running":
            raise ConversationError("resolution_state_conflict", "报告不处于状态评审阶段", 409)
        return job

    async def packet(self, db: AsyncSession, job_id: int, owner: int, issue_id: str) -> ResolutionPacket:
        """从完整三组评审及固定原文推导材料，分歧或缺少失败锚点时拒绝调用。"""
        job = await self._job(db, job_id, owner)
        size = batch_count(job.input_json)
        runs = (await db.scalars(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job_id)
            .order_by(ConversationJudgeRun.run_index))).all()
        if {run.run_index for run in runs} != set(range(1, 3 * size + 1)) or any(run.status == "pending" for run in runs):
            raise ConversationError("resolution_state_conflict", "三组历史评审尚未完整保存", 409)
        failures: list[CheckItem] = []
        for index in range(3):
            group = [run for run in runs if index * size < run.run_index <= (index + 1) * size]
            if not all(run.status == "provisional" for run in group):
                continue
            found = [CheckItem.model_validate(item) for run in group for item in (run.result_json or {}).get("items", [])
                     if item["id"] == issue_id]
            if (len(found) != 1 or found[0].applicability != "applicable"
                    or not ((found[0].rating is not None and found[0].rating < 3) or found[0].passed is False)):
                raise ConversationError("resolution_failure_disputed", "有效历史评审未一致确认该问题失败", 409)
            failures.extend(found)
        if len(failures) < 2:
            raise ConversationError("resolution_failure_disputed", "历史失败缺少至少两组有效依据", 409)
        original = JudgePacket.model_validate_json(json.dumps(job.input_json["packet"]))
        check = next((check for check in original.checks if check.id == issue_id), None)
        if check is None or check.coverage_only or not check.required_source_ids:
            raise ConversationError("resolution_anchor_missing", "该失败项缺少固定原文锚点", 409)
        description = check.description + "\n历史失败判定：" + "；".join(dict.fromkeys(item.reason for item in failures))
        packet = build_resolution_packet(issue_id, check.required_source_ids, original.sources,
            original.through_turn, description=description)
        resolution_messages(packet, job.input_json["inputBudget"])
        return packet

    def _key(self, job_id: int, issue_id: str, index: int) -> str:
        """按固定问题与组号生成有界幂等键，不在键中存放正文。"""
        if type(index) is not int or index not in (1, 2, 3):
            raise ValueError("状态评审组号必须为1至3")
        digest = sha256(issue_id.encode("utf-8")).hexdigest()[:32]
        return f"assessment:{job_id}:resolution:{digest}:{index}"

    async def _usage(self, db: AsyncSession, job: ConversationAssessment, owner: int,
                     issue_id: str, index: int) -> ConversationUsage | None:
        """读取当前作者和问题对应的唯一状态流水。"""
        return await db.scalar(select(ConversationUsage).where(ConversationUsage.conversation_id == job.conversation_id,
            ConversationUsage.user_id == owner, ConversationUsage.stage == "resolution",
            ConversationUsage.operation_key == self._key(job.id, issue_id, index))
            .with_for_update().execution_options(populate_existing=True))

    async def begin(self, db: AsyncSession, job_id: int, owner: int, packet: ResolutionPacket, index: int) -> None:
        """核对重建材料与额度，提交唯一调用占位后才允许请求供应商。"""
        canonical = await self.packet(db, job_id, owner, packet.issue_id)
        if packet != canonical:
            raise ConversationError("resolution_snapshot_conflict", "状态材料与固定历史失败不一致", 409)
        job = await self._job(db, job_id, owner)
        if await self._usage(db, job, owner, packet.issue_id, index) is not None:
            raise ConversationError("resolution_already_started", "该状态评审已登记，不能重复请求", 409)
        user = await db.get(User, owner, populate_existing=True)
        if user is None or user.status != "active":
            raise ConversationError("assessment_user_disabled", "用户不存在或已停用", 403)
        await token_quota_service.ensure_can_start(db, user)
        db.add(ConversationUsage(conversation_id=job.conversation_id, user_id=owner,
            operation_key=self._key(job_id, packet.issue_id, index), stage="resolution", status="pending",
            currency=job.input_json["currency"], accounted=False,
            detail_json={"assessmentId": job_id, "issueId": packet.issue_id, "reviewIndex": index,
                         "packet": {**asdict(packet), "sources": [source.model_dump(mode="json") for source in packet.sources]}}))
        await db.commit()

    async def save_usage(self, db: AsyncSession, job_id: int, owner: int, issue_id: str, index: int,
                         reply: ModelReply | None, client: ModelClient) -> None:
        """记录已发生费用，重复结算不覆盖已知用量，中断后允许流水收尾。"""
        job = await self._job(db, job_id, owner, running=False)
        usage = await self._usage(db, job, owner, issue_id, index)
        if usage is None:
            raise ConversationError("resolution_state_conflict", "状态调用尚未登记", 409)
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

    async def save_result(self, db: AsyncSession, job_id: int, owner: int, issue_id: str, index: int,
                          result: ResolutionRun) -> None:
        """结算后保存独立结论，无效状态保留错误码，不改动历史评审。"""
        job = await self._job(db, job_id, owner)
        usage = await self._usage(db, job, owner, issue_id, index)
        if usage is None or usage.status == "pending":
            raise ConversationError("resolution_state_conflict", "状态调用尚未完成用量结算", 409)
        if result.result is not None and (result.result.issue_id != issue_id or result.result.through_turn != job.through_turn):
            raise ConversationError("resolution_snapshot_conflict", "状态结果不属于当前问题和截止范围", 409)
        if result.result is not None:
            from app.services.multiturn.judge import JudgeSource
            saved = usage.detail_json["packet"]
            packet = build_resolution_packet(saved["issue_id"], tuple(saved["failure_source_ids"]),
                tuple(JudgeSource.model_validate(source) for source in saved["sources"]), saved["through_turn"],
                description=saved["description"])
            if result.reply is None or parse_resolution(result.reply.answer, packet) != result.result:
                raise ConversationError("resolution_snapshot_conflict", "状态结论与实际回复和固定原文不一致", 409)
        value = {"status": result.result.status if result.result else "unknown", "valid": result.result is not None,
            "reason": result.result.reason if result.result else "状态评审未获得有效结论", "errorCode": result.error_code,
            "evidence": [ref.model_dump(mode="json") for ref in result.result.evidence] if result.result else []}
        previous = usage.detail_json.get("result")
        if previous is not None and previous != value:
            raise ConversationError("resolution_state_conflict", "已保存的状态结论不能覆盖", 409)
        usage.detail_json = {**usage.detail_json, "result": value}
        await db.commit()


async def execute_resolution(sessions: async_sessionmaker[AsyncSession], job_id: int, owner: int,
                             issue_id: str, index: int, client: ModelClient, *, output_tokens: int) -> ResolutionRun:
    """使用真实数据库回调执行一组状态判断，供应商请求不持有事务。"""
    store = ResolutionStore()
    async with sessions() as db:
        packet = await store.packet(db, job_id, owner, issue_id)
        job = await store._job(db, job_id, owner)
        budget = job.input_json["inputBudget"]

    async def before() -> None:
        """收费前再次重建固定材料，防止准备期间状态变化。"""
        async with sessions() as db:
            await store.begin(db, job_id, owner, packet, index)

    async def after(reply: ModelReply | None) -> None:
        """无论结果有效与否先将实际或未知用量写入独立流水。"""
        async with sessions() as db:
            await store.save_usage(db, job_id, owner, issue_id, index, reply, client)

    result = await evaluate_resolution(client, packet, input_budget=budget, output_tokens=output_tokens,
        before_call=before, after_call=after)
    async with sessions() as db:
        await store.save_result(db, job_id, owner, issue_id, index, result)
    return result

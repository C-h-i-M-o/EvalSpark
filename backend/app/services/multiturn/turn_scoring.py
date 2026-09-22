"""将生成前要求提取和生成后评分任务连接到会话流水线。"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from app.adapters.base import ChatMessage, ModelClient, ModelReply
from app.models.conversation import Conversation, ConversationTurn, ConversationUsage
from app.models.response import ModelResponse
from app.services.model_config_service import RuntimeModelConfig
from app.services.multiturn.assessments import AssessmentStore
from app.services.multiturn.dispatch import publish_assessment
from app.services.multiturn.history import load_branch_history
from app.services.multiturn.judge import JudgeCheck, JudgePacket, JudgeSource
from app.services.multiturn.requirements import active_requirements, extract_requirements
from app.services.multiturn.requirement_store import load_requirements, save_requirements
from app.services.multiturn.summary_usage import SummaryUsageRecorder
from app.services.multiturn.rag_assessment import load_rag_material
from app.services.rule_evaluator import rule_evaluator
from app.services.multiturn.assessment_batches import build_dialogue_batches
from app.services.multiturn.context import estimate_tokens
from app.services.multiturn.judge import build_judge_messages

logger = logging.getLogger(__name__)


async def prepare_turn_requirements(sessions: async_sessionmaker[AsyncSession], *, conversation_id: int,
                                    owner: int, turn_id: int, branch_id: int, model: RuntimeModelConfig,
                                    client: ModelClient, budget: int) -> tuple[ChatMessage, ...]:
    """共享明确要求只提取一次并在候选回答前保存，同时记录调用费用。"""
    async with sessions() as db:
        turn = await db.get(ConversationTurn, turn_id)
        if turn is None or turn.conversation_id != conversation_id:
            raise ValueError("要求提取目标轮次不存在")
        turn_index, prompt = turn.turn_index, turn.prompt
        previous = await load_requirements(db, conversation_id, owner, through_turn=turn_index - 1)
    recorder = SummaryUsageRecorder(sessions, conversation_id=conversation_id, owner=owner,
        turn_id=turn_id, branch_id=branch_id, attempt=1, model=model, stage="requirements")

    async def before() -> None:
        """在共享要求提取前检查额度并登记唯一调用。"""
        await recorder.before_call(1)

    async def after(reply: ModelReply | None) -> None:
        """要求提取解析失败也保留实际用量。"""
        await recorder.after_call(1, reply)

    records = await extract_requirements(client, source_id=f"turn:{turn_id}:user", turn=turn_index,
        prompt=prompt, previous=previous, input_budget=budget, output_tokens=model.max_tokens,
        before_call=before, after_call=after)
    async with sessions() as db:
        await save_requirements(db, conversation_id, owner, turn_id=turn_id, records=records)
        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.conversation_id == conversation_id, ConversationUsage.user_id == owner,
            ConversationUsage.operation_key == f"requirements:{turn_id}:{branch_id}:1:1").with_for_update())
        if usage is None:
            raise ValueError("共享要求调用记录缺失")
        usage.detail_json = {**usage.detail_json, "requirementsSaved": True}
        await db.commit()
    active = active_requirements(records, turn_index)
    if not active:
        return ()
    return (ChatMessage("user", json.dumps({"用户要求记录": [item.model_dump(mode="json") for item in active]},
                                          ensure_ascii=False)),)


async def load_turn_memory(sessions: async_sessionmaker[AsyncSession], conversation_id: int,
                           owner: int, turn_index: int) -> tuple[ChatMessage, ...]:
    """重试仅复用已保存共享要求，不再付费提取或改变其他分支的条件。"""
    async with sessions() as db:
        active = active_requirements(await load_requirements(db, conversation_id, owner, through_turn=turn_index), turn_index)
    return (ChatMessage("user", json.dumps({"用户要求记录": [item.model_dump(mode="json") for item in active]},
        ensure_ascii=False)),) if active else ()


async def submit_turn_assessments(sessions: async_sessionmaker[AsyncSession], *, conversation_id: int,
                                  owner: int, turn_id: int, judge: RuntimeModelConfig,
                                  budget: int, only_response_id: int | None = None) -> list[int]:
    """基于原始历史和固定检查项提交各成功回答的轻评，不等待 Judge 完成。"""
    jobs: list[int] = []
    async with sessions() as db:
        turn = await db.get(ConversationTurn, turn_id)
        if turn is None or turn.conversation_id != conversation_id:
            raise ValueError("评分目标轮次不存在")
        turn_index, task_id, prompt = turn.turn_index, turn.task_id, turn.prompt
        conversation = await db.get(Conversation, conversation_id)
        is_rag = conversation is not None and conversation.mode == "rag"
        requirements = active_requirements(await load_requirements(db, conversation_id, owner,
                                                                  through_turn=turn_index), turn_index)
        responses = list((await db.scalars(select(ModelResponse).where(ModelResponse.task_id == task_id,
                                                                       ModelResponse.status == "success"))).all())
        # 后续 enqueue 会提交事务，提前复制所需标量以免 ORM 过期读触发额外 I/O。
        candidates = [(response.id, response.model_config_id, response.answer_text) for response in responses
            if only_response_id is None or response.id == only_response_id]
    for response_id, model_id, answer in candidates:
        async with sessions() as db:
            history = await load_branch_history(db, conversation_id, owner, model_id, before_turn=turn_index)
            sources = {}
            for item in history:
                text = rule_evaluator._strip_think_content(item.message.content).strip() if item.message.role == "assistant" else item.message.content
                if text:
                    sources[item.message_id] = JudgeSource(id=item.message_id, turn=item.turn, text=text)
            sources[f"turn:{turn_id}:user"] = JudgeSource(id=f"turn:{turn_id}:user", turn=turn_index, text=prompt)
            checks = [JudgeCheck(id=name, dimension=name, description=description) for name, description in (
                ("solution", "是否解决本轮问题并提供充分结论"),
                ("context", "是否理解历史指代和当前有效条件；没有历史依赖可不适用"),
                ("instruction", "是否遵循明确用户要求，未知指代不得武断判断"),
                ("expression", "表达是否清晰且没有无效重复"))]
            for requirement in requirements:
                if requirement.source_id not in sources:
                    sources[requirement.source_id] = JudgeSource(id=requirement.source_id,
                        turn=requirement.source_turn, text=requirement.quote)
                checks.append(JudgeCheck(id=f"requirement:{requirement.id}", dimension="instruction",
                    requirement_id=requirement.id, critical=requirement.critical,
                    required_source_ids=(requirement.source_id,),
                    ambiguous_source_id=requirement.source_id if requirement.ambiguous else None,
                    description=("先仅依据本分支历史解析用户指代；无法唯一确定用unknown。给出适用结论必须解释具体指代并同时引用要求原文和被指向的更早回答。检查要求："
                                 if requirement.ambiguous else "检查有效要求：") + requirement.text))
            rag = await load_rag_material(db, response_id, answer) if is_rag else None
            final_answer = rag.answer if rag else rule_evaluator._strip_think_content(answer).strip()
            if not final_answer:
                continue
            packet = JudgePacket(scope="dialogue", through_turn=turn_index, answer=final_answer,
                                 sources=tuple(sources.values()), checks=tuple(checks), rag=rag)
            batches = build_dialogue_batches(packet, budget) if estimate_tokens(build_judge_messages(packet)) > budget else None
            job = await AssessmentStore().enqueue(db, conversation_id, owner, operation_key=f"response:{response_id}:initial",
                model_config_id=model_id, packet=packet, formal=False, input_budget=budget,
                currency=judge.currency, response_id=response_id, batches=batches)
            jobs.append(job.id)
        try:
            await run_in_threadpool(publish_assessment, job.id)
        except Exception:
            logger.warning("评分即时投递失败，已保存作业等待恢复扫描")
    return jobs

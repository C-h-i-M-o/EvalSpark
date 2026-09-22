"""按当前知识权限读取本模型分支真正引用过的历史资料快照。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationTurn
from app.models.response import ModelResponse
from app.models.rag import RagResponseDetail
from app.schemas.rag import RagEvidence
from app.services.multiturn.catalog import knowledge_snapshot
from app.services.multiturn.rag_references import HistoricalReference, parse_references
from app.services.multiturn.store import ConversationError, ConversationStore
from app.services.rule_evaluator import rule_evaluator


async def load_historical_references(db: AsyncSession, conversation_id: int, owner: int,
                                     model_id: int, *, before_turn: int, prompt: str) -> tuple[HistoricalReference, ...]:
    """只读取作者当前可用知识库中的本分支历史，未引用和失效版本不能回溯。"""
    try:
        pointers = parse_references(prompt, before_turn=before_turn)
    except ValueError as error:
        raise ConversationError("rag_reference_invalid", str(error), 422) from error
    if not pointers:
        return ()
    conversation = await ConversationStore().get(db, conversation_id, owner, owner_only=True)
    config = conversation.config_json or {}
    knowledge_id = config.get("knowledgeBaseId")
    if (conversation.mode != "rag" or model_id not in config.get("modelIds", [])
            or type(knowledge_id) is not int or before_turn > conversation.current_turn + 1):
        raise ConversationError("rag_reference_invalid", "历史引用不属于当前 RAG 分支", 422)
    snapshot = await knowledge_snapshot(db, knowledge_id, owner)
    if snapshot != conversation.knowledge_snapshot_json:
        raise ConversationError("knowledge_base_changed", "知识版本已变化，请创建新会话", 409)
    versions = {tuple(pair) for pair in snapshot["documents"]}
    references: list[HistoricalReference] = []
    for pointer in pointers:
        query = select(ConversationTurn, ModelResponse).join(ModelResponse, ModelResponse.task_id == ConversationTurn.task_id).where(
            ConversationTurn.conversation_id == conversation_id, ConversationTurn.turn_index < before_turn,
            ConversationTurn.generation_status == "completed", ModelResponse.model_config_id == model_id,
            ModelResponse.status == "success")
        if pointer.turn is not None:
            query = query.where(ConversationTurn.turn_index == pointer.turn)
        rows = (await db.execute(query.order_by(ConversationTurn.turn_index.desc()).limit(2))).all()
        if not rows:
            raise ConversationError("rag_reference_not_found", "历史引用对应的成功回答不存在", 422)
        turn, response = rows[0]
        if len(rows) > 1 and rows[1][0].turn_index == turn.turn_index:
            raise ConversationError("rag_reference_conflict", "历史分支存在重复成功回答", 409)
        answer = rule_evaluator._strip_think_content(response.answer_text)
        if f"[{pointer.label}]" not in answer:
            raise ConversationError("rag_reference_not_cited", "指定标签未被该分支最终回答引用", 422)
        detail = await db.get(RagResponseDetail, response.id)
        if (detail is None or detail.failure_stage is not None or detail.knowledge_base_id != knowledge_id
                or detail.content_revision != snapshot["contentRevision"]):
            raise ConversationError("rag_reference_changed", "历史资料与当前知识版本不一致", 409)
        evidence = [RagEvidence.model_validate(item) for item in detail.evidence_json]
        selected = [item for item in evidence if item.label == pointer.label]
        if len(selected) != 1 or (selected[0].document_id, selected[0].index_revision) not in versions:
            raise ConversationError("rag_reference_not_found", "历史引用资料不存在或版本已失效", 422)
        reference = HistoricalReference(turn=turn.turn_index, response_id=response.id, label=pointer.label, evidence=selected[0])
        if reference not in references:
            references.append(reference)
    return tuple(references)

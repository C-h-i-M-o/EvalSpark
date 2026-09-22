"""从已授权回答的持久化证据重建 RAG 评分材料。"""
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationContext, ConversationTurn
from app.models.response import ModelResponse
from app.models.rag import RagResponseDetail
from app.schemas.rag import RagEvidence
from app.services.multiturn.rag_judge import RagJudgeEvidence, RagJudgeMaterial, RagJudgeReference, build_rag_material
from app.services.multiturn.rag_references import evidence_key
from app.services.multiturn.store import ConversationError
from app.services.rule_evaluator import rule_evaluator


def _reference_mapping(snapshot: dict[str, object], evidence: list[RagEvidence]) -> dict[str, tuple[RagJudgeReference, ...]]:
    """从实际回答消息核对历史资料身份，保留去重后的全部旧标签对应关系。"""
    try:
        data = json.loads(snapshot["rag_requests"]["answer"]["messages"][-1]["content"])
        references = data.get("historicalReferences", [])
        if not isinstance(references, list):
            raise ValueError("历史引用映射必须为数组")
        by_label = {item.label: item for item in evidence}
        mapping: dict[str, list[RagJudgeReference]] = {}
        for item in references:
            original = RagEvidence.model_validate(item["evidence"])
            current = by_label[item["currentLabel"]]
            reference = RagJudgeReference(reference=item["reference"], source_id=item["sourceId"])
            if (evidence_key(original) != evidence_key(current) or original.text != current.text
                    or original.source != current.source or item["originalLabel"] != original.label
                    or reference.reference != f"T{item['turn']}:{original.label}"
                    or reference.source_id != f"response:{item['responseId']}:{original.label}"):
                raise ValueError("历史引用与当前固定资料不一致")
            values = mapping.setdefault(current.label, [])
            if reference not in values:
                values.append(reference)
        return {label: tuple(values) for label, values in mapping.items()}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ConversationError("assessment_evidence_invalid", "评分历史引用快照无效", 422) from exc


async def load_rag_material(db: AsyncSession, response_id: int, answer: str) -> RagJudgeMaterial:
    """仅使用该回答实际固定资料；调用方先校验作者、轮次和模型归属。"""
    detail = await db.get(RagResponseDetail, response_id, populate_existing=True)
    if detail is None or detail.failure_stage is not None or not detail.evidence_json:
        raise ConversationError("assessment_evidence_invalid", "评分回答缺少已固定资料", 422)
    evidence = [RagEvidence.model_validate(item) for item in detail.evidence_json]
    context = await db.scalar(select(ConversationContext).join(ConversationTurn,
        ConversationTurn.id == ConversationContext.turn_id).join(ModelResponse,
        ModelResponse.task_id == ConversationTurn.task_id).where(ModelResponse.id == response_id,
        ConversationContext.conversation_id == ConversationTurn.conversation_id,
        ConversationContext.model_config_id == ModelResponse.model_config_id,
        ConversationContext.attempt == 1))
    references = _reference_mapping(context.snapshot_json, evidence) if context is not None else {}
    return build_rag_material(rule_evaluator._strip_think_content(answer).strip(), tuple(
        RagJudgeEvidence(id=f"response:{response_id}:{item.label}", label=item.label, text=item.text,
                         historical_references=references.get(item.label, ()))
        for item in evidence
    ))

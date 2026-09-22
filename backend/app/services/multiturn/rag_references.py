"""解析明确的历史引用，并将原始资料重编号为当前回答的证据。"""
import re
from dataclasses import dataclass

from app.schemas.rag import RagEvidence

_REFERENCE = re.compile(
    r"\[T(?P<turn>\d+):(?P<label>S\d+)\]"
    r"|第\s*(?P<chinese_turn>\d+)\s*轮\s*(?:的\s*)?\[(?P<chinese_label>S\d+)\]"
    r"|(?P<relative>上一轮|上轮|刚才)\s*(?:的\s*)?\[(?P<relative_label>S\d+)\]"
)


@dataclass(frozen=True)
class ReferencePointer:
    """指定轮次或最近成功回答中的原标签，不能指定其他候选模型。"""
    turn: int | None
    label: str


@dataclass(frozen=True)
class HistoricalReference:
    """已验证的分支原始资料；来源回答与原标签构成稳定身份。"""
    turn: int
    response_id: int
    label: str
    evidence: RagEvidence

    def as_input(self, current_evidence: list[RagEvidence] | None = None) -> dict[str, object]:
        """返回当前问题可用的原引用和本轮标签映射，原文作为不可信数据。"""
        current = next((item.label for item in current_evidence or [] if evidence_key(item) == evidence_key(self.evidence)), None)
        return {"reference": f"T{self.turn}:{self.label}", "sourceId": f"response:{self.response_id}:{self.label}",
            "turn": self.turn, "responseId": self.response_id, "originalLabel": self.label,
            "currentLabel": current, "evidence": self.evidence.model_dump(mode="json", by_alias=True)}


def parse_references(prompt: str, *, before_turn: int) -> tuple[ReferencePointer, ...]:
    """只解析显式回溯写法，拒绝未来轮次及过多资料，不推断裸标签。"""
    if type(before_turn) is not int or before_turn < 1:
        raise ValueError("引用截止轮次无效")
    result: list[ReferencePointer] = []
    for match in _REFERENCE.finditer(prompt):
        turn_text = match.group("turn") or match.group("chinese_turn")
        turn = int(turn_text) if turn_text else (None if match.group("relative") == "刚才" else before_turn - 1)
        label = match.group("label") or match.group("chinese_label") or match.group("relative_label")
        if label not in {"S1", "S2", "S3", "S4", "S5"} or (turn is not None and not 1 <= turn < before_turn):
            raise ValueError("历史引用必须使用已完成轮次的 S1 至 S5 标签")
        pointer = ReferencePointer(turn, label)
        if pointer not in result:
            result.append(pointer)
        if len(result) > 5:
            raise ValueError("一次最多回溯五个历史引用")
    return tuple(result)


def evidence_key(evidence: RagEvidence) -> tuple[int, int, str]:
    """同文档、版本和片段才可去重，不按相似文本混淆来源。"""
    return evidence.document_id, evidence.index_revision, evidence.chunk_id


def merge_reference_evidence(references: tuple[HistoricalReference, ...], fresh: list[RagEvidence]) -> list[RagEvidence]:
    """优先保留明确历史请求，再补充新检索结果，统一限制五份证据。"""
    if len(references) > 5:
        raise ValueError("一次最多回溯五个历史引用")
    unique: dict[tuple[int, int, str], RagEvidence] = {}
    for item in [*(reference.evidence for reference in references), *fresh]:
        key = evidence_key(item)
        if key in unique:
            if unique[key].text != item.text or unique[key].source != item.source:
                raise ValueError("同一资料版本存在不一致原文或位置")
        elif len(unique) < 5:
            unique[key] = item
    return [item.model_copy(update={"label": f"S{index}"}) for index, item in enumerate(unique.values(), 1)]

"""冻结 RAG 回答片段和资料关系，校验模型逐项提供的证据判定。"""
import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.multiturn import RAGAssertion

_CITATION = re.compile(r"\[(S\d+)\]")
_SEGMENT = re.compile(r".+?(?:[。！？!?](?:[ \t]*\[S\d+\])*|\.(?=[ \t\n]|$)|\n|$)", re.DOTALL)


class RagJudgeReference(BaseModel):
    """保存旧轮次标签到当前资料的对应关系，不替代当前资料来源。"""
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    reference: str = Field(pattern=r"^T[1-9]\d*:S[1-5]$")
    source_id: str = Field(pattern=r"^response:[1-9]\d*:S[1-5]$")


class RagJudgeEvidence(BaseModel):
    """来源标识全局稳定，显示标签仅在该回答的证据快照内有效。"""
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    label: str = Field(pattern=r"^S\d+$")
    text: str = Field(min_length=1)
    historical_references: tuple[RagJudgeReference, ...] = ()


class RagJudgeSegment(BaseModel):
    """服务端固定的回答原文范围与实际引用，不由 Judge 生成。"""
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)
    citations: tuple[str, ...]


class RagJudgeMaterial(BaseModel):
    """复评复用的完整证据材料，重新构建后必须与固定片段一致。"""
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    answer: str = Field(min_length=1)
    evidence: tuple[RagJudgeEvidence, ...]
    segments: tuple[RagJudgeSegment, ...]

    def validate_material(self) -> None:
        """拒绝被裁切、改写或伪造关系的片段快照。"""
        if self != build_rag_material(self.answer, self.evidence):
            raise ValueError("RAG 评分片段必须完整匹配回答原文")


def build_rag_material(answer: str, evidence: tuple[RagJudgeEvidence, ...]) -> RagJudgeMaterial:
    """按原文位置固定所有非空句段，并保留句末引用、去除重复片段。"""
    if len({item.id for item in evidence}) != len(evidence) or len({item.label for item in evidence}) != len(evidence):
        raise ValueError("RAG 资料标识和标签不能重复")
    if not answer.strip() or any(not item.text.strip() or not item.id.strip() for item in evidence):
        raise ValueError("RAG 回答和资料内容不能为空")
    segments: list[RagJudgeSegment] = []
    seen: set[str] = set()
    for match in _SEGMENT.finditer(answer):
        raw = match.group()
        text = raw.strip()
        if not text or text in seen:
            continue
        start = match.start() + len(raw) - len(raw.lstrip())
        segments.append(RagJudgeSegment(id=f"segment:{start}", start=start, end=start + len(text),
            text=text, citations=tuple(dict.fromkeys(_CITATION.findall(text)))))
        seen.add(text)
    return RagJudgeMaterial(answer=answer, evidence=evidence, segments=tuple(segments))


class _Support(BaseModel):
    """JSON 数字只接受三档支持程度，布尔值不能冒充数字。"""
    model_config = ConfigDict(strict=True, extra="forbid")
    support: float | None

    @field_validator("support", mode="before")
    @classmethod
    def validate_support(cls, value: object) -> object:
        """拒绝字符串、布尔值、非有限数和非三档数值。"""
        if value is not None and (type(value) not in (int, float) or value not in (0, 0.5, 1)):
            raise ValueError("支持程度必须为 0、0.5、1 或 null")
        return value


class _Quote(BaseModel):
    """总体支持证据指向当前已固定资料中的逐字片段。"""
    model_config = ConfigDict(strict=True, extra="forbid")
    source_id: str
    quote: str = Field(min_length=1)


class _Relation(_Support):
    """每个已知引用独立给出支持判断及对应资料引文。"""
    label: str
    quote: str | None


class _Verdict(_Support):
    """每个固定片段的事实判断，不能覆盖原文和实际引用集合。"""
    id: str
    needs_citation: bool
    citation_support: float | None
    reason: str = Field(min_length=1)
    evidence: list[_Quote]
    citations: list[_Relation]

    @field_validator("citation_support", mode="before")
    @classmethod
    def validate_coverage(cls, value: object) -> object:
        """联合引用覆盖使用与总体支持相同的三档数值。"""
        return cls.validate_support(value)


def _decimal(value: float | None) -> Decimal | None:
    """通过十进制字符串转换三档值，避免二进制浮点计算。"""
    return Decimal(str(value)) if value is not None else None


def parse_rag_verdicts(data: object, material: RagJudgeMaterial) -> tuple[RAGAssertion, ...]:
    """核验全量固定片段、资料引文和逐引用判定后交给确定性评分器。"""
    material.validate_material()
    if not isinstance(data, list):
        raise ValueError("RAG 评审必须返回片段判定数组")
    verdicts = [_Verdict.model_validate(item) for item in data]
    by_id = {item.id: item for item in verdicts}
    if len(by_id) != len(verdicts) or set(by_id) != {item.id for item in material.segments}:
        raise ValueError("RAG 评审必须完整且仅返回固定片段")
    by_label = {item.label: item for item in material.evidence}
    by_source = {item.id: item.text for item in material.evidence}
    assertions: list[RAGAssertion] = []
    for segment in material.segments:
        verdict = by_id[segment.id]
        known = [label for label in segment.citations if label in by_label]
        invalid = [label for label in segment.citations if label not in by_label]
        relations = {item.label: item for item in verdict.citations}
        if len(relations) != len(verdict.citations) or set(relations) != set(known):
            raise ValueError("RAG 评审引用关系必须与原回答的已知引用一致")
        for quote in verdict.evidence:
            if not quote.quote.strip() or quote.quote not in by_source.get(quote.source_id, ""):
                raise ValueError("RAG 评审引文不属于固定资料")
        if verdict.support is not None and verdict.support > 0 and not verdict.evidence:
            raise ValueError("正向资料支持必须提供原文引文")
        if not verdict.needs_citation and (verdict.support is not None or verdict.citation_support is not None):
            raise ValueError("无事实片段不得携带事实支持或覆盖分")
        if not known and verdict.citation_support is not None:
            raise ValueError("无已知引用不得提供引用覆盖分")
        for relation in verdict.citations:
            if relation.quote is not None and (not relation.quote.strip() or relation.quote not in by_label[relation.label].text):
                raise ValueError("引用支持必须使用对应资料原文")
            if relation.support is not None and relation.support > 0 and relation.quote is None:
                raise ValueError("正向引用支持必须提供对应资料引文")
            if verdict.citation_support is not None and relation.support is not None and relation.support > verdict.citation_support:
                raise ValueError("联合引用覆盖不能低于单个引用支持")
        if verdict.citation_support is not None and verdict.citation_support > 0 and not any(
            item.quote is not None for item in verdict.citations
        ):
            raise ValueError("正向引用覆盖必须有对应资料引文")
        if verdict.support is not None and any(value is not None and value > verdict.support
            for value in [verdict.citation_support, *(item.support for item in verdict.citations)]):
            raise ValueError("引用支持不能高于总体资料支持")
        assertions.append(RAGAssertion(id=segment.id, answer_text=segment.text,
            needs_citation=verdict.needs_citation, support=_decimal(verdict.support),
            citation_support=_decimal(verdict.citation_support),
            citation_supports={label: _decimal(relations[label].support) for label in known},
            cited_evidence_ids=known, invalid_cited_evidence_ids=invalid, reason=verdict.reason,
            evidence=list(dict.fromkeys([
                *(f"{item.source_id}: {item.quote}" for item in verdict.evidence),
                *(f"{by_label[item.label].id}: {item.quote}" for item in verdict.citations if item.quote is not None),
            ]))))
    return tuple(assertions)

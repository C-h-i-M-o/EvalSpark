from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Applicability = Literal["applicable", "not_applicable", "unknown"]


class EvidenceReference(BaseModel):
    """由服务端固定快照确定轮次的原文引用，不接受评审模型自报位置。"""
    model_config = ConfigDict(strict=True, extra="forbid")
    source_id: str = Field(min_length=1)
    turn: int = Field(ge=1)
    quote: str = Field(min_length=1)


class CheckItem(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str
    dimension: str
    requirement_id: str | None = None
    applicability: Applicability = "applicable"
    rating: int | None = Field(default=None, ge=0, le=4)
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    critical: bool = False
    passed: bool | None = None

    @field_validator("rating")
    @classmethod
    def strict_rating(cls, value: int | None) -> int | None:
        """评分只允许整数档位，不能把布尔值当作分数。"""
        if value is not None and type(value) is not int:
            raise ValueError("rating 必须是严格整数")
        return value


class DimensionWeight(BaseModel):
    model_config = ConfigDict(strict=True)
    dimension: str
    weight: Decimal = Field(gt=0)


class RAGAssertion(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str
    needs_citation: bool = True
    support: Decimal | None = Field(default=None, ge=0, le=1)
    citation_support: Decimal | None = Field(default=None, ge=0, le=1)
    citation_supports: dict[str, Decimal | None] = Field(default_factory=dict)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    invalid_cited_evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    answer_text: str = ""
    reason: str = ""

    @field_validator("support", "citation_support")
    @classmethod
    def strict_support(cls, value: Decimal | None) -> Decimal | None:
        """支持程度只使用完整、部分和不支持三档。"""
        if value is not None and value not in {Decimal("0"), Decimal("0.5"), Decimal("1")}:
            raise ValueError("support 只能为 0、0.5、1 或 unknown")
        return value

    @field_validator("citation_supports")
    @classmethod
    def strict_relations(cls, value: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
        """校验各断言与引用关系的支持值。"""
        for support in value.values():
            if support is not None and support not in {Decimal("0"), Decimal("0.5"), Decimal("1")}:
                raise ValueError("引用支持值只能为 0、0.5、1 或 unknown")
        return value


class SessionOpportunity(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str
    dimension: str
    applicability: Applicability = "applicable"
    rating: int | None = Field(default=None, ge=0, le=4)
    evidence: list[str] = Field(default_factory=list)


class Reassessment(BaseModel):
    model_config = ConfigDict(strict=True)
    valid: bool
    run_index: int = Field(ge=1, le=3)
    items: list[CheckItem]

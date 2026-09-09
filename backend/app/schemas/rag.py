from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, JsonValue, StrictBool, StrictInt, model_validator

from app.schemas.knowledge_base import KnowledgeSchema, SourceLocation
from app.core.config import Settings


class RagEvidence(KnowledgeSchema):
    label: str = Field(pattern=r"^S[1-5]$")
    document_id: StrictInt = Field(gt=0)
    document_name: str
    chunk_id: str
    index_revision: StrictInt = Field(gt=0)
    text: str
    similarity: float = Field(allow_inf_nan=False)
    source: SourceLocation


RagFailureStage = Literal["rewrite", "embed", "retrieve", "snapshot", "generate", "judge"]
RagStage = Literal["rewriting", "retrieving", "answering", "judging"]
UsageStage = Literal["rewrite", "embed", "generate", "judge"]


class RagModelSnapshot(KnowledgeSchema):
    model_config_id: StrictInt = Field(gt=0)
    provider_name: str
    display_name: str
    model_name: str
    max_tokens: StrictInt = Field(gt=0)
    temperature: float
    timeout_seconds: StrictInt = Field(gt=0)
    currency: Literal["CNY", "USD"]
    price_input: Decimal = Field(ge=0, allow_inf_nan=False)
    price_output: Decimal = Field(ge=0, allow_inf_nan=False)
    price_cache_hit: Decimal = Field(ge=0, allow_inf_nan=False)
    price_cache_creation: Decimal = Field(ge=0, allow_inf_nan=False)


class RagStageUsage(KnowledgeSchema):
    external_embedding: bool = False
    stage: UsageStage
    run_index: StrictInt = Field(default=1, ge=1, le=3)
    status: Literal["pending", "known", "unknown"]
    model: RagModelSnapshot | None = None
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    cache_hit_tokens: StrictInt | None = Field(default=None, ge=0)
    cache_creation_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)
    latency_ms: StrictInt | None = Field(default=None, ge=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def consistent_usage(self) -> "RagStageUsage":
        if self.stage != "judge" and self.run_index != 1:
            raise ValueError("只有 Judge 支持多轮用量")
        if (self.stage == "embed") != (self.model is None):
            raise ValueError("外部模型用量必须包含配置快照")
        if self.stage == "embed" and self.estimated_cost is not None:
            raise ValueError("本地 Embedding 不记录外部费用")
        if self.status == "known":
            parts = (self.input_tokens, self.output_tokens, self.cache_hit_tokens, self.cache_creation_tokens)
            if any(value is None for value in parts) or sum(value for value in parts if value is not None) != self.total_tokens:
                raise ValueError("已知用量的 Token 分项不完整或不一致")
            if self.stage != "embed" and self.estimated_cost is None:
                raise ValueError("已知外部用量必须记录估算费用")
        elif self.total_tokens is not None or self.estimated_cost is not None:
            raise ValueError("未知或待返回用量不能伪造总数与费用")
        return self


class RagUsageSummary(KnowledgeSchema):
    # 仅为已知部分小计；hasUnknownUsage 为真时不是整条链路精确总量。
    external_total_tokens: StrictInt = 0
    cost_by_currency: dict[str, Decimal] = Field(default_factory=dict)
    has_unknown_usage: bool = False


class RagTaskContext(KnowledgeSchema):
    # 仅服务端本次任务使用，序列化响应和历史快照不得包含凭据。
    embedding_runtime: Settings | None = Field(default=None, exclude=True, repr=False)
    task_id: StrictInt = Field(gt=0)
    user_id: StrictInt = Field(gt=0)
    knowledge_base_id: StrictInt = Field(gt=0)
    content_revision: StrictInt = Field(gt=0)
    document_versions: list[tuple[int, int]]
    prompt: str
    enable_thinking: bool = True


class PreparedRagResponse(KnowledgeSchema):
    response_id: StrictInt = Field(gt=0)
    model_config_id: StrictInt = Field(gt=0)
    rewritten_query: str | None = None
    evidence: list[RagEvidence] = Field(default_factory=list, max_length=5)
    failure_stage: RagFailureStage | None = None
    error_code: str | None = None


class RagStageEvent(KnowledgeSchema):
    type: Literal["rag_stage"] = "rag_stage"
    model_config_id: int
    stage: RagStage


class RagRetrievalEvent(KnowledgeSchema):
    type: Literal["rag_retrieval"] = "rag_retrieval"
    model_config_id: int
    rewritten_query: str
    evidence: list[RagEvidence]


class RagDeltaEvent(KnowledgeSchema):
    type: Literal["model_delta"] = "model_delta"
    model_config_id: int
    delta: str


class RagAnswerCompletedEvent(KnowledgeSchema):
    type: Literal["model_answer_completed"] = "model_answer_completed"
    model_config_id: int


class RagAnswerReadyEvent(KnowledgeSchema):
    # 仅供后续评分编排消费，不能直接充当已持久化评分的 model_response。
    type: Literal["rag_answer_ready"] = "rag_answer_ready"
    model_config_id: int
    response_id: int


RagStreamEvent = Annotated[
    RagStageEvent | RagRetrievalEvent | RagDeltaEvent | RagAnswerCompletedEvent | RagAnswerReadyEvent,
    Field(discriminator="type"),
]


def _judge_number(value: object) -> Decimal:
    # 数据库 JSON 中 Decimal 按字符串保存；上游原始输入的数字类型在解析入口另行校验。
    if type(value) not in (int, float, Decimal, str):
        raise ValueError("评审分数必须是数字")
    return Decimal(str(value))


JudgeDimension = Annotated[Decimal, BeforeValidator(_judge_number), Field(ge=0, le=10, allow_inf_nan=False)]


class RagClaim(KnowledgeSchema):
    claim: str = Field(min_length=1)
    evidence_labels: list[str]
    invalid_citation_labels: list[str]
    supported: StrictBool
    needs_citation: StrictBool
    citation_supported: StrictBool
    reason: str = Field(min_length=1)


class RagJudgeResult(KnowledgeSchema):
    answer_quality: JudgeDimension
    faithfulness: JudgeDimension
    citation_correctness: JudgeDimension
    citation_completeness: JudgeDimension
    claims: list[RagClaim] = Field(min_length=1)


class RagJudgeRun(KnowledgeSchema):
    run_index: StrictInt = Field(ge=1, le=3)
    prompt_code: str
    result: RagJudgeResult | None = None
    raw_result: dict[str, JsonValue] | None = None
    error_code: str | None = None


class RagJudgeAggregate(KnowledgeSchema):
    score_status: Literal["scored", "judge_failed", "judge_unstable"]
    valid_run_count: StrictInt
    answer_quality: Decimal | None = None
    faithfulness: Decimal | None = None
    citation_correctness: Decimal | None = None
    citation_completeness: Decimal | None = None
    ranges: dict[str, Decimal] = Field(default_factory=dict)


class RagDetailRead(KnowledgeSchema):
    knowledge_base_id: int
    knowledge_base_name: str
    content_revision: int
    embedding_revision: str
    chunk_size: int
    chunk_overlap: int
    document_versions: list[dict[str, int]]
    rewritten_query: str | None = None
    evidence: list[RagEvidence]
    stage_usage: list[RagStageUsage]
    external_total_tokens: int
    cost_by_currency: dict[str, Decimal]
    has_unknown_usage: bool
    judge_runs: list[RagJudgeRun]
    judge_aggregate: RagJudgeAggregate | None = None
    faithfulness: Decimal | None = None
    citation_correctness: Decimal | None = None
    citation_completeness: Decimal | None = None
    rag_final: Decimal | None = None
    base_final: Decimal | None = None
    score_version: Literal["rag-v1"] = "rag-v1"
    failure_stage: RagFailureStage | None = None
    error_code: str | None = None

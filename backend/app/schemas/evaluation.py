from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class EvaluationTaskCreate(BaseModel):
    task_type: Literal["chat"] = Field(default="chat", alias="taskType")
    conversation_id: int | None = Field(default=None, alias="conversationId")
    prompt: str
    model_ids: list[int] = Field(default_factory=list, alias="modelIds")
    enable_judge: bool = Field(default=True, alias="enableJudge")
    judge_model_id: int | None = Field(default=None, alias="judgeModelId")
    enable_thinking: bool = Field(default=False, alias="enableThinking")
    visibility: Literal["public", "private"] = "public"

    @model_validator(mode="after")
    def require_judge_model(self) -> "EvaluationTaskCreate":
        if self.enable_judge and self.judge_model_id in self.model_ids:
            raise ValueError("LLM 评审模型不能同时作为被测模型")
        return self


ScoreStatus = Literal["scored", "judge_failed", "judge_unstable", "judge_disabled", "model_failed"]


class JudgeRunRead(BaseModel):
    run_index: int = Field(alias="runIndex")
    prompt_code: str = Field(alias="promptCode")
    score: float | None = None
    confidence: float | None = None
    comment: str | None = None
    error: str | None = None


class EvaluationScoreRead(BaseModel):
    relevance: float
    completeness: float
    clarity: float
    format: float
    safety: float
    final: float | None
    details: dict[str, list[str]] = Field(default_factory=dict)
    rule_final: float | None = Field(default=None, alias="ruleFinal")
    judge_final: float | None = Field(default=None, alias="judgeFinal")
    base_final: float | None = Field(default=None, alias="baseFinal")
    feedback_score: float | None = Field(default=None, alias="feedbackScore")
    judge_comment: str | None = Field(default=None, alias="judgeComment")
    judge_details: dict[str, list[str]] = Field(default_factory=dict, alias="judgeDetails")
    score_status: ScoreStatus = Field(default="scored", alias="scoreStatus")
    excluded_from_stats: bool = Field(default=False, alias="excludedFromStats")
    judge_runs: list[JudgeRunRead] = Field(default_factory=list, alias="judgeRuns")
    judge_score_range: float | None = Field(default=None, alias="judgeScoreRange")
    rule_dictionary_version: str | None = Field(default=None, alias="ruleDictionaryVersion")

    @model_validator(mode="after")
    def default_rule_final(self) -> "EvaluationScoreRead":
        if self.rule_final is None:
            self.rule_final = self.final
        if self.base_final is None:
            if self.final is None or self.rule_final is None:
                self.base_final = None
            elif self.judge_final is None:
                self.base_final = self.rule_final
            else:
                self.base_final = round(self.rule_final * 0.30 + self.judge_final * 0.70, 2)
        return self


class EvaluationFeedbackRead(BaseModel):
    liked: bool = False
    like_count: int = Field(default=0, alias="likeCount")
    disliked: bool = False
    dislike_count: int = Field(default=0, alias="dislikeCount")


class ModelCostDetailsRead(BaseModel):
    input_cost: float = Field(default=0, alias="inputCost")
    output_cost: float = Field(default=0, alias="outputCost")
    cache_hit_cost: float = Field(default=0, alias="cacheHitCost")
    cache_creation_cost: float = Field(default=0, alias="cacheCreationCost")


class ModelResponseRead(BaseModel):
    id: int
    model_config_id: int | None = Field(default=None, alias="modelConfigId")
    model_name: str = Field(alias="modelName")
    provider: str
    answer: str
    latency_ms: int = Field(alias="latencyMs")
    input_tokens: int = Field(default=0, alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    cache_hit_tokens: int = Field(default=0, alias="cacheHitTokens")
    cache_creation_tokens: int = Field(default=0, alias="cacheCreationTokens")
    total_tokens: int = Field(default=0, alias="totalTokens")
    estimated_cost: float = Field(alias="estimatedCost")
    currency: Literal["CNY", "USD"] = "CNY"
    cost_details: ModelCostDetailsRead = Field(default_factory=ModelCostDetailsRead, alias="costDetails")
    config_snapshot: dict[str, object] = Field(default_factory=dict, alias="configSnapshot", exclude=True)
    status: str
    score: EvaluationScoreRead
    feedback: EvaluationFeedbackRead = Field(default_factory=EvaluationFeedbackRead)


class EvaluationTaskRead(BaseModel):
    task_id: int = Field(alias="taskId")
    status: str
    prompt: str
    created_at: datetime | None = Field(default=None, alias="createdAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    owner_id: int | None = Field(default=None, alias="ownerId")
    owner_username: str = Field(default="anonymous", alias="ownerUsername")
    visibility: Literal["public", "private"] = "public"
    responses: list[ModelResponseRead]


class EvaluationTaskListItemRead(BaseModel):
    task_id: int = Field(alias="taskId")
    status: str
    prompt: str
    created_at: datetime = Field(alias="createdAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    response_count: int = Field(alias="responseCount")
    owner_id: int | None = Field(default=None, alias="ownerId")
    owner_username: str = Field(default="anonymous", alias="ownerUsername")
    visibility: Literal["public", "private"] = "public"


class EvaluationTaskListRead(BaseModel):
    items: list[EvaluationTaskListItemRead]
    total: int
    page: int
    page_size: int = Field(alias="pageSize")


class FeedbackCreate(BaseModel):
    feedback_type: Literal["like", "dislike"] = Field(alias="feedbackType")


class FeedbackToggleRead(BaseModel):
    response_id: int = Field(alias="responseId")
    feedback_type: Literal["like", "dislike"] = Field(alias="feedbackType")
    active: bool
    feedback: EvaluationFeedbackRead
    score: EvaluationScoreRead


class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1000)

    @field_validator("content", mode="before")
    @classmethod
    def strip_content(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CommentRead(BaseModel):
    id: int
    response_id: int = Field(alias="responseId")
    user_id: int = Field(alias="userId")
    username: str
    content: str
    created_at: datetime = Field(alias="createdAt")
    can_delete: bool = Field(alias="canDelete")


class CommentListRead(BaseModel):
    items: list[CommentRead]
    total: int
    page: int
    page_size: int = Field(alias="pageSize")

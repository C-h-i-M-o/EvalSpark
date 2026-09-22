"""多轮会话接口；客户端不能提交历史、模型回答或系统提示词。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PositiveId = Annotated[int, Field(strict=True, gt=0)]


class ConversationCreate(BaseModel):
    """会话创建时冻结配置，后续轮次仅提交问题。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    mode: Literal["chat", "rag"] = "chat"
    title: str = Field(default="新多轮会话", min_length=1, max_length=120)
    model_ids: list[PositiveId] = Field(alias="modelIds", min_length=1, max_length=8)
    judge_model_id: PositiveId | None = Field(default=None, alias="judgeModelId")
    summary_model_id: PositiveId | None = Field(default=None, alias="summaryModelId")
    enable_thinking: bool = Field(default=False, alias="enableThinking", strict=True)
    visibility: Literal["public", "private"] = "private"
    knowledge_base_id: PositiveId | None = Field(default=None, alias="knowledgeBaseId")
    input_budget: int = Field(default=8192, alias="inputBudget", ge=1024, le=262144, strict=True)

    @model_validator(mode="after")
    def validate_mode(self) -> "ConversationCreate":
        """校验模式、模型集合和 RAG 前置条件。"""
        if not self.title.strip() or len(set(self.model_ids)) != len(self.model_ids):
            raise ValueError("会话标题不能为空，候选模型不能重复")
        if self.judge_model_id in self.model_ids:
            raise ValueError("评审模型不能同时作为候选模型")
        if self.mode == "rag":
            if self.knowledge_base_id is None or self.judge_model_id is None:
                raise ValueError("RAG 会话必须选择知识库和独立评审模型")
            if "enable_thinking" not in self.model_fields_set:
                self.enable_thinking = True
        elif self.knowledge_base_id is not None:
            raise ValueError("普通会话不能绑定知识库")
        return self


class TurnCreate(BaseModel):
    """问题使用原文持久化，幂等键与预期轮次防止重复生成。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    prompt: str = Field(min_length=1, max_length=200000)
    expected_turn: int = Field(alias="expectedTurn", ge=0, strict=True)
    request_key: str = Field(alias="requestKey", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("prompt")
    @classmethod
    def nonempty_prompt(cls, value: str) -> str:
        """拒绝全空白问题但不修改用户输入内容。"""
        if not value.strip():
            raise ValueError("问题不能为空")
        if len(value.encode("utf-8")) > 65535:
            raise ValueError("单条问题不能超过 65535 UTF-8 字节，请拆分为多轮输入")
        return value


class BranchActionCreate(BaseModel):
    """失败分支恢复请求，只允许引用服务端已有轮次和冻结模型。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    turn_id: PositiveId = Field(alias="turnId")
    model_config_id: PositiveId = Field(alias="modelConfigId")
    request_key: str = Field(alias="requestKey", min_length=1, max_length=64,
                             pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["retry", "skip"]


class ConversationRead(BaseModel):
    """公开读取不包含供应商凭据或私有上下文快照。"""

    model_config = ConfigDict(populate_by_name=True)
    id: int
    title: str
    mode: Literal["chat", "rag"]
    owner_id: int = Field(alias="ownerId")
    can_continue: bool = Field(alias="canContinue")
    visibility: Literal["public", "private"]
    current_turn: int = Field(alias="currentTurn")
    generation_status: str = Field(alias="generationStatus")
    configuration: dict[str, object]
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime | None = Field(alias="updatedAt")


class ConversationListRead(BaseModel):
    """会话列表使用固定分页上限，避免加载无限历史。"""

    model_config = ConfigDict(populate_by_name=True)
    items: list[ConversationRead]
    total: int
    page: int
    page_size: int = Field(alias="pageSize")


class ContextStatusRead(BaseModel):
    """公开轮次只显示压缩元数据，不泄漏内部提示词与摘要正文。"""
    model_config = ConfigDict(populate_by_name=True)
    model_config_id: int = Field(alias="modelConfigId")
    phase: Literal["chat", "rewrite", "answer"]
    compressed: bool | None = None
    estimated_tokens: int | None = Field(default=None, alias="estimatedTokens")
    history_through_turn: int | None = Field(default=None, alias="historyThroughTurn")


class TurnRead(BaseModel):
    """轮次摘要通过 taskId 复用已有回答详情接口。"""

    model_config = ConfigDict(populate_by_name=True)
    id: int
    task_id: int = Field(alias="taskId")
    turn_index: int = Field(alias="turnIndex")
    prompt: str
    generation_status: str = Field(alias="generationStatus")
    error_code: str | None = Field(alias="errorCode")
    created_at: datetime = Field(alias="createdAt")
    contexts: list[ContextStatusRead] = Field(default_factory=list)


class TurnListRead(BaseModel):
    """轮次分页读取不改变模型上下文的服务端来源。"""

    model_config = ConfigDict(populate_by_name=True)
    items: list[TurnRead]
    total: int
    page: int
    page_size: int = Field(alias="pageSize")


class AssessmentRunRead(BaseModel):
    """评审执行结果只公开分项证据及稳定错误码。"""

    model_config = ConfigDict(populate_by_name=True)
    run_index: int = Field(alias="runIndex")
    status: str
    result: dict[str, object] | None
    error_code: str | None = Field(alias="errorCode")


class AssessmentCreate(BaseModel):
    """正式复评只引用服务端暂定输入，不接受自行编造的评分材料。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    source_assessment_id: PositiveId = Field(alias="sourceAssessmentId")


class ReportCreate(BaseModel):
    """会话报告只接收固定模型分支与截止轮次，不接受客户端评分材料。"""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model_config_id: PositiveId = Field(alias="modelConfigId")
    through_turn: PositiveId = Field(alias="throughTurn")


class AssessmentRead(BaseModel):
    """评分公开投影不包含完整评审输入或内部幂等键。"""

    model_config = ConfigDict(populate_by_name=True)
    id: int
    response_id: int | None = Field(alias="responseId")
    model_config_id: int = Field(alias="modelConfigId")
    through_turn: int = Field(alias="throughTurn")
    score_version: str = Field(alias="scoreVersion")
    status: str
    formal: bool
    result: dict[str, object] | None
    created_at: datetime = Field(alias="createdAt")
    completed_at: datetime | None = Field(alias="completedAt")


class AssessmentDetailRead(AssessmentRead):
    """详情附带按执行顺序排列的独立评审结果。"""

    runs: list[AssessmentRunRead]


class AssessmentListRead(BaseModel):
    """评分列表不加载逐次执行材料，支持按回答筛选。"""

    model_config = ConfigDict(populate_by_name=True)
    items: list[AssessmentRead]
    total: int
    page: int
    page_size: int = Field(alias="pageSize")

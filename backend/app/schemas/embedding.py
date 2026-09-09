from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr, StrictBool, StrictInt, model_validator

from app.schemas.knowledge_base import KnowledgeSchema


class EmbeddingConfigPayload(KnowledgeSchema):
    version: StrictInt = Field(ge=0)
    base_url: AnyHttpUrl
    api_key: SecretStr | None = None
    clear_api_key: StrictBool = False
    model_name: str = Field(min_length=1, max_length=120)
    dimensions: StrictInt = Field(ge=1, le=65536)
    query_prefix: str = Field(default="", max_length=2000)
    timeout_seconds: StrictInt = Field(default=60, ge=1, le=300)
    batch_size: StrictInt = Field(default=16, ge=1, le=16)
    max_input_characters: StrictInt = Field(default=2048, ge=2048, le=32768)
    enabled: StrictBool = True

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        if self.base_url.username or self.base_url.password or self.base_url.query or self.base_url.fragment:
            raise ValueError("Base URL 不能包含用户名、密码、查询参数或片段")
        if not self.model_name.strip():
            raise ValueError("模型名称不能为空")
        if self.clear_api_key and self.api_key and self.api_key.get_secret_value().strip():
            raise ValueError("不能同时清除和设置 API Key")
        return self


class EmbeddingConfigRead(KnowledgeSchema):
    version: int
    configured: bool
    base_url: str
    has_api_key: bool
    model_name: str
    dimensions: int
    query_prefix: str
    timeout_seconds: int
    batch_size: int
    max_input_characters: int
    enabled: bool

from typing import Literal

from pydantic import BaseModel, Field


class AvailableModelRead(BaseModel):
    context_window: int | None = Field(default=None, alias="contextWindow")
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    id: int
    provider_name: str = Field(alias="providerName")
    display_name: str = Field(alias="displayName")
    model_name: str = Field(alias="modelName")


class ModelConfigRead(BaseModel):
    context_window: int | None = Field(default=None, alias="contextWindow")
    id: int
    provider_name: str = Field(alias="providerName")
    display_name: str = Field(alias="displayName")
    model_name: str = Field(alias="modelName")
    base_url: str = Field(alias="baseUrl")
    enabled: bool
    has_api_key: bool = Field(alias="hasApiKey")
    masked_api_key: str = Field(alias="maskedApiKey")
    max_tokens: int = Field(alias="maxTokens")
    temperature: float
    timeout_seconds: int = Field(alias="timeoutSeconds")
    notes: str = ""
    currency: Literal["CNY", "USD"]
    price_input: float = Field(alias="priceInput")
    price_output: float = Field(alias="priceOutput")
    price_cache_hit: float = Field(alias="priceCacheHit")
    price_cache_creation: float = Field(alias="priceCacheCreation")


class ModelConfigCreate(BaseModel):
    context_window: int | None = Field(default=None, alias="contextWindow", strict=True, ge=2, le=2147483647)
    provider_name: str = Field(alias="providerName")
    display_name: str = Field(alias="displayName")
    model_name: str = Field(alias="modelName")
    base_url: str = Field(alias="baseUrl")
    api_key: str | None = Field(default=None, alias="apiKey")
    enabled: bool = True
    max_tokens: int = Field(default=1024, alias="maxTokens", ge=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    timeout_seconds: int = Field(default=60, alias="timeoutSeconds", ge=1, le=600)
    notes: str = Field(default="", max_length=2000)
    currency: Literal["CNY", "USD"] = "CNY"
    price_input: float = Field(default=0, alias="priceInput", ge=0)
    price_output: float = Field(default=0, alias="priceOutput", ge=0)
    price_cache_hit: float = Field(default=0, alias="priceCacheHit", ge=0)
    price_cache_creation: float = Field(default=0, alias="priceCacheCreation", ge=0)


class ModelConfigUpdate(BaseModel):
    context_window: int | None = Field(default=None, alias="contextWindow", strict=True, ge=2, le=2147483647)
    provider_name: str | None = Field(default=None, alias="providerName")
    display_name: str | None = Field(default=None, alias="displayName")
    model_name: str | None = Field(default=None, alias="modelName")
    base_url: str | None = Field(default=None, alias="baseUrl")
    api_key: str | None = Field(default=None, alias="apiKey")
    enabled: bool | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens", ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", ge=1, le=600)
    notes: str | None = Field(default=None, max_length=2000)
    currency: Literal["CNY", "USD"] | None = None
    price_input: float | None = Field(default=None, alias="priceInput", ge=0)
    price_output: float | None = Field(default=None, alias="priceOutput", ge=0)
    price_cache_hit: float | None = Field(default=None, alias="priceCacheHit", ge=0)
    price_cache_creation: float | None = Field(default=None, alias="priceCacheCreation", ge=0)


class ModelConfigTestRequest(BaseModel):
    model_config_id: int | None = Field(default=None, alias="modelConfigId")
    provider_name: str | None = Field(default=None, alias="providerName")
    model_name: str | None = Field(default=None, alias="modelName")
    base_url: str | None = Field(default=None, alias="baseUrl")
    api_key: str | None = Field(default=None, alias="apiKey")
    max_tokens: int | None = Field(default=None, alias="maxTokens", ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", ge=1, le=600)


class ModelConfigTestResult(BaseModel):
    success: bool
    message: str
    latency_ms: int = Field(default=0, alias="latencyMs")

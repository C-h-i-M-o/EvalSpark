import asyncio
from dataclasses import dataclass
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.base import ModelRequest
from app.adapters.openai_compatible import OpenAICompatibleClient
from app.core.api_keys import has_stored_api_key, load_api_key, mask_stored_api_key, store_api_key
from app.models.model_config import ModelConfig, ModelProvider
from app.schemas.model_config import (
    AvailableModelRead,
    ModelConfigCreate,
    ModelConfigRead,
    ModelConfigTestRequest,
    ModelConfigTestResult,
    ModelConfigUpdate,
)

@dataclass(frozen=True)
class RuntimeModelConfig:
    id: int
    provider_name: str
    display_name: str
    model_name: str
    base_url: str
    api_key: str
    input_price: Decimal
    output_price: Decimal
    cache_hit_price: Decimal
    cache_creation_price: Decimal
    currency: str
    max_tokens: int
    temperature: float
    timeout_seconds: int
    notes: str
    extra_body: dict[str, object]
    context_window: int | None = None


class ModelConfigServiceError(Exception):
    pass


class ModelConfigNotFoundError(ModelConfigServiceError):
    pass


class ModelConfigService:
    @staticmethod
    def validate_capacity(max_tokens: int, context_window: int | None) -> None:
        """已知总容量须留有输入空间，禁止最大输出占满或超出总上下文。"""
        if context_window is not None and max_tokens >= context_window:
            raise ModelConfigServiceError("最大输出 Token 必须小于总上下文容量")

    async def list_available_configs(self, db: AsyncSession) -> list[AvailableModelRead]:
        configs = await self._list_enabled_model_configs(db)
        return [
            AvailableModelRead(
                contextWindow=config.context_window,
                maxTokens=config.max_tokens,
                id=config.id,
                providerName=config.provider.name,
                displayName=config.display_name,
                modelName=config.model_name,
            )
            for config in configs
            if has_stored_api_key(config.provider.api_key_encrypted)
        ]

    async def list_configs(self, db: AsyncSession) -> list[ModelConfigRead]:
        configs = await self._list_model_configs(db)
        return [self._serialize_config(config) for config in configs]

    async def create_config(self, db: AsyncSession, payload: ModelConfigCreate) -> ModelConfigRead:
        self.validate_capacity(payload.max_tokens, payload.context_window)
        provider_name = self._required_text(payload.provider_name, "供应商名称")
        existing_provider = await self._get_provider_by_name(db, provider_name)
        if existing_provider is not None:
            raise ModelConfigServiceError("供应商名称已存在")

        provider = ModelProvider(
            name=provider_name,
            base_url=self._required_text(payload.base_url, "Base URL"),
            api_key_encrypted=store_api_key(payload.api_key),
            enabled=payload.enabled,
        )
        db.add(provider)
        await db.flush()

        config = ModelConfig(
            provider_id=provider.id,
            model_name=self._required_text(payload.model_name, "模型名"),
            display_name=self._required_text(payload.display_name, "展示名"),
            price_input=Decimal(str(payload.price_input)),
            price_output=Decimal(str(payload.price_output)),
            price_cache_hit=Decimal(str(payload.price_cache_hit)),
            price_cache_creation=Decimal(str(payload.price_cache_creation)),
            currency=payload.currency,
            temperature=Decimal(str(payload.temperature)),
            timeout_seconds=payload.timeout_seconds,
            notes=payload.notes.strip(),
            max_tokens=payload.max_tokens,
            context_window=payload.context_window,
            enabled=payload.enabled,
        )
        db.add(config)
        await db.commit()
        created_config = await self._get_model_config(db, config.id)
        return self._serialize_config(created_config)

    async def update_config(self, db: AsyncSession, model_config_id: int, payload: ModelConfigUpdate) -> ModelConfigRead:
        config = await self._get_model_config(db, model_config_id)
        provider = config.provider

        capacity = payload.context_window if "context_window" in payload.model_fields_set else config.context_window
        self.validate_capacity(payload.max_tokens if payload.max_tokens is not None else config.max_tokens, capacity)
        config.context_window = capacity

        if payload.provider_name is not None:
            provider_name = self._required_text(payload.provider_name, "供应商名称")
            if provider_name != provider.name:
                existing_provider = await self._get_provider_by_name(db, provider_name)
                if existing_provider is not None:
                    raise ModelConfigServiceError("供应商名称已存在")
                provider.name = provider_name

        if payload.base_url is not None:
            provider.base_url = self._required_text(payload.base_url, "Base URL")
        if payload.api_key is not None and payload.api_key.strip():
            provider.api_key_encrypted = store_api_key(payload.api_key)
        if payload.enabled is not None:
            provider.enabled = payload.enabled
            config.enabled = payload.enabled
        if payload.display_name is not None:
            config.display_name = self._required_text(payload.display_name, "展示名")
        if payload.model_name is not None:
            config.model_name = self._required_text(payload.model_name, "模型名")
        if payload.max_tokens is not None:
            config.max_tokens = payload.max_tokens
        if payload.price_input is not None:
            config.price_input = Decimal(str(payload.price_input))
        if payload.price_output is not None:
            config.price_output = Decimal(str(payload.price_output))
        if payload.price_cache_hit is not None:
            config.price_cache_hit = Decimal(str(payload.price_cache_hit))
        if payload.price_cache_creation is not None:
            config.price_cache_creation = Decimal(str(payload.price_cache_creation))
        if payload.currency is not None:
            config.currency = payload.currency
        if payload.temperature is not None:
            config.temperature = Decimal(str(payload.temperature))
        if payload.timeout_seconds is not None:
            config.timeout_seconds = payload.timeout_seconds
        if payload.notes is not None:
            config.notes = payload.notes.strip()

        await db.commit()
        updated_config = await self._get_model_config(db, model_config_id)
        return self._serialize_config(updated_config)

    async def delete_config(self, db: AsyncSession, model_config_id: int) -> None:
        config = await self._get_model_config(db, model_config_id)
        provider = config.provider
        await db.delete(config)
        await db.delete(provider)
        await db.commit()

    async def test_config(self, db: AsyncSession, payload: ModelConfigTestRequest) -> ModelConfigTestResult:
        try:
            runtime_config = await self._resolve_test_config(db, payload)
            client = OpenAICompatibleClient(
                model_name=runtime_config.model_name,
                base_url=runtime_config.base_url,
                api_key=runtime_config.api_key,
                timeout=min(runtime_config.timeout_seconds, 30),
                extra_body=runtime_config.extra_body,
            )
            reply = await asyncio.wait_for(
                client.chat(
                    ModelRequest(
                        prompt="请用一句话回复：连接测试成功",
                        model_name=runtime_config.model_name,
                        max_tokens=min(runtime_config.max_tokens, 64),
                        temperature=runtime_config.temperature,
                    )
                ),
                timeout=min(runtime_config.timeout_seconds + 5, 35),
            )
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, IndexError, ModelConfigServiceError) as error:
            return ModelConfigTestResult(success=False, message=f"连接测试失败：{error}", latencyMs=0)

        return ModelConfigTestResult(success=True, message="连接测试成功", latencyMs=reply.latency_ms)

    async def resolve_runtime_models(self, db: AsyncSession, model_ids: list[int]) -> list[RuntimeModelConfig]:
        configs = await self._list_enabled_model_configs(db)
        if not configs:
            return []

        configs_by_id = {config.id: config for config in configs}
        selected_configs = [configs_by_id[model_id] for model_id in model_ids if model_id in configs_by_id]
        if not selected_configs:
            selected_configs = self._default_model_configs(configs)

        return [self._to_runtime_config(config) for config in selected_configs]

    async def resolve_runtime_model(self, db: AsyncSession, model_id: int) -> RuntimeModelConfig:
        config = await self._get_model_config(db, model_id)
        if not config.enabled or not config.provider.enabled:
            raise ModelConfigNotFoundError("评审模型未启用")
        runtime_config = self._to_runtime_config(config)
        if not runtime_config.api_key:
            raise ModelConfigNotFoundError("评审模型未配置 API Key")
        return runtime_config

    async def _resolve_test_config(self, db: AsyncSession, payload: ModelConfigTestRequest) -> RuntimeModelConfig:
        if payload.model_config_id is not None:
            config = await self._get_model_config(db, payload.model_config_id)
            runtime_config = self._to_runtime_config(config)
            provider_name = payload.provider_name.strip() if payload.provider_name and payload.provider_name.strip() else runtime_config.provider_name
            model_name = payload.model_name.strip() if payload.model_name and payload.model_name.strip() else runtime_config.model_name
            base_url = payload.base_url.strip() if payload.base_url and payload.base_url.strip() else runtime_config.base_url
            api_key = payload.api_key.strip() if payload.api_key and payload.api_key.strip() else runtime_config.api_key
            return RuntimeModelConfig(
                id=runtime_config.id,
                provider_name=provider_name,
                display_name=runtime_config.display_name,
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                input_price=runtime_config.input_price,
                output_price=runtime_config.output_price,
                cache_hit_price=runtime_config.cache_hit_price,
                cache_creation_price=runtime_config.cache_creation_price,
                currency=runtime_config.currency,
                max_tokens=payload.max_tokens or runtime_config.max_tokens,
                temperature=(
                    runtime_config.temperature
                    if payload.temperature is None
                    else payload.temperature
                ),
                timeout_seconds=payload.timeout_seconds or runtime_config.timeout_seconds,
                notes=runtime_config.notes,
                extra_body=self._extra_body_for_provider(provider_name),
            )

        provider_name = self._required_text(payload.provider_name, "供应商名称")
        return RuntimeModelConfig(
            id=0,
            provider_name=provider_name,
            display_name=payload.model_name or provider_name,
            model_name=self._required_text(payload.model_name, "模型名"),
            base_url=self._required_text(payload.base_url, "Base URL"),
            api_key=self._required_text(payload.api_key, "API Key"),
            input_price=Decimal("0"),
            output_price=Decimal("0"),
            cache_hit_price=Decimal("0"),
            cache_creation_price=Decimal("0"),
            currency="CNY",
            max_tokens=payload.max_tokens or 128,
            temperature=0.7 if payload.temperature is None else payload.temperature,
            timeout_seconds=payload.timeout_seconds or 30,
            notes="",
            extra_body=self._extra_body_for_provider(provider_name),
        )

    async def _list_model_configs(self, db: AsyncSession) -> list[ModelConfig]:
        result = await db.execute(
            select(ModelConfig).options(selectinload(ModelConfig.provider)).order_by(ModelConfig.id)
        )
        return list(result.scalars().all())

    async def _list_enabled_model_configs(self, db: AsyncSession) -> list[ModelConfig]:
        result = await db.execute(
            select(ModelConfig)
            .join(ModelConfig.provider)
            .options(selectinload(ModelConfig.provider))
            .where(ModelConfig.enabled.is_(True), ModelProvider.enabled.is_(True))
            .order_by(ModelConfig.id)
        )
        return list(result.scalars().all())

    async def _get_model_config(self, db: AsyncSession, model_config_id: int) -> ModelConfig:
        result = await db.execute(
            select(ModelConfig)
            .options(selectinload(ModelConfig.provider))
            .where(ModelConfig.id == model_config_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise ModelConfigNotFoundError("模型配置不存在")
        return config

    async def _get_provider_by_name(self, db: AsyncSession, provider_name: str) -> ModelProvider | None:
        result = await db.execute(select(ModelProvider).where(ModelProvider.name == provider_name))
        return result.scalar_one_or_none()

    def _serialize_config(self, config: ModelConfig) -> ModelConfigRead:
        provider = config.provider
        stored_key = provider.api_key_encrypted
        return ModelConfigRead(
            id=config.id,
            providerName=provider.name,
            displayName=config.display_name,
            modelName=config.model_name,
            baseUrl=provider.base_url or "",
            enabled=bool(provider.enabled and config.enabled),
            hasApiKey=has_stored_api_key(stored_key),
            maskedApiKey=mask_stored_api_key(stored_key),
            maxTokens=config.max_tokens,
            contextWindow=config.context_window,
            temperature=float(config.temperature),
            timeoutSeconds=config.timeout_seconds,
            notes=config.notes or "",
            currency=config.currency,
            priceInput=float(config.price_input),
            priceOutput=float(config.price_output),
            priceCacheHit=float(config.price_cache_hit),
            priceCacheCreation=float(config.price_cache_creation),
        )

    def _to_runtime_config(self, config: ModelConfig) -> RuntimeModelConfig:
        provider = config.provider
        return RuntimeModelConfig(
            id=config.id,
            provider_name=provider.name,
            display_name=config.display_name,
            model_name=config.model_name,
            base_url=provider.base_url or "",
            api_key=load_api_key(provider.api_key_encrypted),
            input_price=Decimal(config.price_input),
            output_price=Decimal(config.price_output),
            cache_hit_price=Decimal(config.price_cache_hit),
            cache_creation_price=Decimal(config.price_cache_creation),
            currency=config.currency,
            max_tokens=config.max_tokens,
            context_window=config.context_window,
            temperature=float(config.temperature),
            timeout_seconds=config.timeout_seconds,
            notes=config.notes or "",
            extra_body=self._extra_body_for_provider(provider.name),
        )

    def _default_model_configs(self, configs: list[ModelConfig]) -> list[ModelConfig]:
        return configs[:2]

    def _extra_body_for_provider(self, provider_name: str) -> dict[str, object]:
        if provider_name == "glm":
            return {"thinking": {"type": "disabled"}}
        return {}

    def _required_text(self, value: str | None, field_name: str) -> str:
        if value is None:
            raise ModelConfigServiceError(f"{field_name}不能为空")
        cleaned_value = value.strip()
        if not cleaned_value:
            raise ModelConfigServiceError(f"{field_name}不能为空")
        return cleaned_value

model_config_service = ModelConfigService()

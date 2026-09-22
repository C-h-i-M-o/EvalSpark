from decimal import Decimal

from app.adapters.base import ModelCostDetails, ModelReply, ModelUsage
from app.adapters.openai_compatible import OpenAICompatibleClient
from app.schemas.rag import RagModelSnapshot, RagStageUsage, RagUsageSummary, UsageStage
from app.services.model_config_service import RuntimeModelConfig


def model_snapshot(model: RuntimeModelConfig) -> RagModelSnapshot:
    """只保存计费和展示必需字段，不复制凭据、地址、备注或自由配置。"""
    return RagModelSnapshot(
        model_config_id=model.id, provider_name=model.provider_name, display_name=model.display_name,
        model_name=model.model_name, max_tokens=model.max_tokens, temperature=model.temperature,
        context_window=model.context_window,
        timeout_seconds=model.timeout_seconds, currency=model.currency, price_input=model.input_price,
        price_output=model.output_price, price_cache_hit=model.cache_hit_price, price_cache_creation=model.cache_creation_price,
    )


def external_stage(stage: UsageStage, model: RuntimeModelConfig, reply: ModelReply | None,
                   *, run_index: int = 1, pending: bool = False) -> RagStageUsage:
    snapshot = model_snapshot(model)
    if reply is None or not reply.usage_known:
        return RagStageUsage(stage=stage, run_index=run_index, model=snapshot,
                             status="pending" if pending else "unknown",
                             latency_ms=reply.latency_ms if reply else None)
    usage = reply.usage
    cost = estimate_stage_cost(snapshot, usage).total_cost
    return RagStageUsage(stage=stage, run_index=run_index, model=snapshot, status="known",
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, cache_hit_tokens=usage.cache_hit_tokens,
        cache_creation_tokens=usage.cache_creation_tokens, total_tokens=usage.total_tokens,
        latency_ms=reply.latency_ms, estimated_cost=cost)


def estimate_stage_cost(model: RagModelSnapshot, usage: ModelUsage) -> ModelCostDetails:
    # 复用既有四类费用算法；这里只做本地运算，不配置地址和密钥或发起请求。
    return OpenAICompatibleClient(model_name=model.model_name, base_url="", api_key="",
        input_price=model.price_input, output_price=model.price_output, cache_hit_price=model.price_cache_hit,
        cache_creation_price=model.price_cache_creation).estimate_cost_details(usage)


def embedding_stage(input_tokens: int | None, latency_ms: int, *, external: bool = False) -> RagStageUsage:
    if input_tokens is None:
        return RagStageUsage(stage="embed", status="unknown", latency_ms=latency_ms, external_embedding=external)
    return RagStageUsage(stage="embed", status="known", input_tokens=input_tokens, output_tokens=0,
        cache_hit_tokens=0, cache_creation_tokens=0, total_tokens=input_tokens, latency_ms=latency_ms, external_embedding=external)


def summarize_usage(stages: list[RagStageUsage]) -> RagUsageSummary:
    summary = RagUsageSummary()
    seen: set[tuple[str, int]] = set()
    for item in stages:
        key = (item.stage, item.run_index)
        if key in seen:
            raise ValueError("存在重复的阶段用量")
        seen.add(key)
        if item.stage == "embed":
            if item.status != "known" or item.external_embedding:
                summary.has_unknown_usage = True
            continue
        if item.status != "known":
            summary.has_unknown_usage = True
            continue
        if item.total_tokens is None or item.model is None or item.estimated_cost is None:
            raise ValueError("已知外部用量缺少汇总字段")
        summary.external_total_tokens += item.total_tokens
        currency = item.model.currency
        summary.cost_by_currency[currency] = summary.cost_by_currency.get(currency, Decimal("0")) + item.estimated_cost
    return summary

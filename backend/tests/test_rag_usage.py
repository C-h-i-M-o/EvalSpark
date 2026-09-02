import importlib
from decimal import Decimal

import pytest

from app.adapters.base import ModelReply, ModelUsage
from test_rag_evaluation import model


@pytest.fixture
def usage_module():
    assert importlib.util.find_spec("app.services.rag.usage") is not None, "尚未实现 RAG 分阶段用量"
    return importlib.import_module("app.services.rag.usage")


def test_usage_snapshot_excludes_credentials_and_aggregates_per_currency(usage_module) -> None:
    from dataclasses import replace
    cny = usage_module.external_stage("rewrite", model(1), ModelReply("查询", ModelUsage(10, 2, 1, 1), 30))
    usd = usage_module.external_stage("judge", replace(model(2), currency="USD"), ModelReply("评分", ModelUsage(20, 3), 40), run_index=2)
    embedding = usage_module.embedding_stage(15, 12)
    total = usage_module.summarize_usage([cny, usd, embedding])
    assert total.external_total_tokens == 37
    assert total.cost_by_currency == {"CNY": Decimal("0.0000151"), "USD": Decimal("0.000026")}
    assert not total.has_unknown_usage
    serialized = cny.model_dump_json(by_alias=True)
    assert "apiKey" not in serialized and "baseUrl" not in serialized and "不应持久化" not in serialized


def test_unknown_usage_is_not_claimed_as_zero(usage_module) -> None:
    known = usage_module.external_stage("rewrite", model(1), ModelReply("查询", ModelUsage(10, 2), 30))
    unknown = usage_module.external_stage("generate", model(1), None)
    total = usage_module.summarize_usage([known, unknown])
    assert unknown.total_tokens is None and unknown.estimated_cost is None
    assert total.external_total_tokens == 12 and total.has_unknown_usage


def test_duplicate_stage_is_rejected_instead_of_double_counted(usage_module) -> None:
    value = usage_module.external_stage("rewrite", model(1), ModelReply("查询", ModelUsage(10, 2), 30))
    with pytest.raises(ValueError, match="重复"):
        usage_module.summarize_usage([value, value])


def test_known_external_usage_requires_consistent_cost_parts(usage_module) -> None:
    from pydantic import ValidationError
    from app.schemas.rag import RagStageUsage
    usage = usage_module.external_stage("rewrite", model(1), ModelReply("查询", ModelUsage(10, 2), 30))
    payload = usage.model_dump()
    payload["estimated_cost"] = None
    with pytest.raises(ValidationError):
        RagStageUsage.model_validate(payload)

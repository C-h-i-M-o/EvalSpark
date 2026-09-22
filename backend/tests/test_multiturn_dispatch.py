"""后台分发重新校验目标、冻结价格，并仅向队列发送作业 ID。"""
from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.conversation import Conversation, ConversationAssessment
from app.services.multiturn.catalog import model_identity
from app.services.multiturn.dispatch import publish_assessment, resolve_judge, run_assessment_job
from app.services.multiturn.store import ConversationError
from app.services.rag.usage import model_snapshot
from test_rag_evaluation import model
from test_multiturn_assessments import FixedJudge, setup_job


def configuration() -> tuple[dict[str, object], list]:
    """提供候选与独立评审的非秘密快照。"""
    models = [model(1), replace(model(2), model_name="independent-judge", max_tokens=765)]
    return {"modelIds": [1], "judgeModelId": 2,
            "identities": {str(item.id): model_identity(item) for item in models},
            "models": [model_snapshot(item).model_dump(mode="json", by_alias=True) for item in models]}, models


@pytest.mark.asyncio
async def test_runtime_keeps_frozen_price_with_rotated_credentials(monkeypatch) -> None:
    """凭据轮换正常生效，价格改变不污染原评分配置。"""
    config, models = configuration()
    fresh = replace(models[1], input_price=Decimal("999"), api_key="rotated-test-key")
    resolver = AsyncMock(side_effect=lambda db, identity: fresh if identity == 2 else models[0])
    monkeypatch.setattr("app.services.multiturn.dispatch.model_config_service.resolve_runtime_model", resolver)
    judge = await resolve_judge(AsyncMock(), Conversation(config_json=config))
    assert judge.api_key == "rotated-test-key"
    assert judge.input_price == models[1].input_price
    assert judge.max_tokens == 765


@pytest.mark.asyncio
async def test_changed_model_target_rejected(monkeypatch) -> None:
    """旧快照不可用于授权另一个模型目标。"""
    config, models = configuration()
    monkeypatch.setattr("app.services.multiturn.dispatch.model_config_service.resolve_runtime_model",
        AsyncMock(return_value=replace(models[1], base_url="https://changed.invalid")))
    with pytest.raises(ConversationError):
        await resolve_judge(AsyncMock(), Conversation(config_json=config))


def test_publisher_sends_only_job_identifier(monkeypatch) -> None:
    """队列消息不包含用户文本、凭据或任意模型配置。"""
    from app.worker import celery_app
    send = Mock()
    monkeypatch.setattr(celery_app, "send_task", send)
    assert publish_assessment(12)
    send.assert_called_once_with("app.worker.run_conversation_assessment", args=[12], queue="rag", retry=False)
    with pytest.raises(ValueError):
        publish_assessment(True)


@pytest.mark.asyncio
async def test_database_dispatch_uses_owner_and_is_idempotent(rag_sessions, rag_users, monkeypatch) -> None:
    """后台入口从库取作者，完成后重复分发不再调用模型。"""
    conversation_id, job_id, _ = await setup_job(rag_sessions, rag_users[0], formal=False)
    config, models = configuration()
    async with rag_sessions() as db:
        conversation = await db.get(Conversation, conversation_id)
        conversation.config_json = config
        job = await db.get(ConversationAssessment, job_id)
        job.input_json = {**job.input_json, "currency": models[1].currency}
        await db.commit()
    monkeypatch.setattr("app.services.multiturn.dispatch.model_config_service.resolve_runtime_model",
        AsyncMock(side_effect=lambda db, identity: models[identity - 1]))
    client = FixedJudge()
    monkeypatch.setattr("app.services.multiturn.dispatch.create_client", lambda runtime: client)
    assert await run_assessment_job(rag_sessions, job_id)
    assert not await run_assessment_job(rag_sessions, job_id)
    assert client.calls == 1


@pytest.mark.asyncio
async def test_invalid_configuration_finishes_without_provider_call(rag_sessions, rag_users) -> None:
    """缺少冻结配置时保留明确失败状态，不能猜测供应商继续执行。"""
    _, job_id, _ = await setup_job(rag_sessions, rag_users[0], formal=False)
    assert not await run_assessment_job(rag_sessions, job_id)
    async with rag_sessions() as db:
        job = await db.get(ConversationAssessment, job_id)
        assert job.status == "judge_failed"
        assert job.result_json == {"errorCode": "assessment_config_unavailable"}

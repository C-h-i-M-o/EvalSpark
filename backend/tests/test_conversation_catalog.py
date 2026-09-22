"""使用隔离 MySQL 验证会话元数据服务，不请求模型供应商。"""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.schemas.conversation import ConversationCreate
from app.models.conversation import ConversationContext
from app.services.multiturn.catalog import ConversationCatalog
from app.services.multiturn.store import ConversationError
from test_rag_evaluation import model


@pytest.mark.asyncio
async def test_turn_context_metadata_is_authorized_paged_and_latest(rag_sessions, rag_users, monkeypatch) -> None:
    """分页只公开当前轮最新尝试的安全元数据，私有读者不能获取快照。"""
    monkeypatch.setattr("app.services.multiturn.catalog.model_config_service.resolve_runtime_models",
        AsyncMock(return_value=[model(1)]))
    service = ConversationCatalog()
    async with rag_sessions() as db:
        conversation = await service.create(db, rag_users[0], ConversationCreate(modelIds=[1]))
        first, _ = await service.store.reserve_turn(db, conversation.id, rag_users[0], prompt="第一轮", expected_turn=0, request_key="context-one")
        await service.store.finish_generation(db, conversation.id, first.id, rag_users[0], status="completed")
        second, _ = await service.store.reserve_turn(db, conversation.id, rag_users[0], prompt="第二轮", expected_turn=1, request_key="context-two")
        for attempt in (1, 2):
            db.add(ConversationContext(conversation_id=conversation.id, turn_id=first.id, model_config_id=1,
                attempt=attempt, covered_through_turn=0, source_hash="test", snapshot_json={"compressed": attempt == 2,
                    "estimated_tokens": attempt * 100, "messages": ["内部秘密"]}))
        db.add(ConversationContext(conversation_id=conversation.id, turn_id=second.id, model_config_id=1,
            attempt=1, covered_through_turn=1, source_hash="test", snapshot_json={"rag_requests": {
                "rewrite": {"compressed": False, "estimated_tokens": 20},
                "answer": {"compressed": True, "estimated_tokens": 30, "summary": "内部秘密"}}}))
        await db.commit()
    async with rag_sessions() as db:
        with pytest.raises(ConversationError):
            await service.turns(db, conversation.id, rag_users[1], page=1, page_size=1)
        await service.set_visibility(db, conversation.id, rag_users[0], "public")
    async with rag_sessions() as db:
        first_page = await service.turns(db, conversation.id, rag_users[1], page=1, page_size=1)
        assert first_page.total == 2 and len(first_page.items) == 1
        assert len(first_page.items[0].contexts) == 1
        assert first_page.items[0].contexts[0].estimated_tokens == 200
        second_page = await service.turns(db, conversation.id, rag_users[1], page=2, page_size=1)
        assert [item.phase for item in second_page.items[0].contexts] == ["rewrite", "answer"]
        assert "内部秘密" not in first_page.model_dump_json() + second_page.model_dump_json()
        assert (await service.turns(db, conversation.id, rag_users[1], page=3, page_size=1)).items == []


@pytest.mark.asyncio
async def test_catalog_freezes_config_without_secrets_and_public_is_read_only(rag_sessions, rag_users, monkeypatch) -> None:
    """固定模型快照不包含秘密，公开读者不具备继续对话能力。"""
    candidates = [model(1), replace(model(2), model_name="judge-other")]
    monkeypatch.setattr("app.services.multiturn.catalog.model_config_service.resolve_runtime_models", AsyncMock(return_value=candidates))
    service = ConversationCatalog()
    async with rag_sessions() as db:
        created = await service.create(db, rag_users[0], ConversationCreate(modelIds=[1], judgeModelId=2))
        assert created.visibility == "private" and created.can_continue
        assert "api_key" not in str(created.configuration) and "base_url" not in str(created.configuration)
        await service.set_visibility(db, created.id, rag_users[0], "public")
    async with rag_sessions() as db:
        public = await service.get(db, created.id, rag_users[1])
        assert not public.can_continue
        listing = await service.list(db, rag_users[1], mode="chat", page=1, page_size=100)
        assert created.id in {value.id for value in listing.items}
        turns = await service.turns(db, created.id, rag_users[1], page=1, page_size=20)
        assert turns.total == 0


@pytest.mark.asyncio
async def test_duplicate_model_identity_cannot_judge(rag_sessions, rag_users, monkeypatch) -> None:
    """两个不同配置 ID 指向同一供应商模型时仍禁止自我评审。"""
    candidate = model(1)
    monkeypatch.setattr("app.services.multiturn.catalog.model_config_service.resolve_runtime_models",
        AsyncMock(return_value=[candidate, replace(candidate, id=2)]))
    async with rag_sessions() as db:
        with pytest.raises(ConversationError, match="评审"):
            await ConversationCatalog().create(db, rag_users[0], ConversationCreate(modelIds=[1], judgeModelId=2))


@pytest.mark.asyncio
async def test_unknown_configuration_and_cross_user_private_are_rejected(rag_sessions, rag_users, monkeypatch) -> None:
    """配置解析缺项和私有会话越权都不能被降级放行。"""
    resolve = AsyncMock(return_value=[])
    monkeypatch.setattr("app.services.multiturn.catalog.model_config_service.resolve_runtime_models", resolve)
    async with rag_sessions() as db:
        with pytest.raises(ConversationError):
            await ConversationCatalog().create(db, rag_users[0], ConversationCreate(modelIds=[1]))
        resolve.return_value = [model(1)]
        created = await ConversationCatalog().create(db, rag_users[0], ConversationCreate(modelIds=[1]))
    async with rag_sessions() as db:
        with pytest.raises(ConversationError) as error:
            await ConversationCatalog().get(db, created.id, rag_users[1])
        assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("limited_id", [1, 2, 3])
async def test_context_budget_checks_every_role_and_freezes_capacity(rag_sessions, rag_users, monkeypatch, limited_id: int) -> None:
    """候选、评审、摘要均须预留输出预算，等于容量可创建，超出一枚则拒绝。"""
    models = [replace(model(index), model_name=f"capacity-model-{index}") for index in (1, 2, 3)]
    exact = 8192 + models[limited_id - 1].max_tokens
    models[limited_id - 1] = replace(models[limited_id - 1], context_window=exact - 1)
    resolve = AsyncMock(return_value=models)
    monkeypatch.setattr("app.services.multiturn.catalog.model_config_service.resolve_runtime_models", resolve)
    payload = ConversationCreate(modelIds=[1], judgeModelId=2, summaryModelId=3, inputBudget=8192)
    async with rag_sessions() as db:
        with pytest.raises(ConversationError, match="总上下文容量"):
            await ConversationCatalog().create(db, rag_users[0], payload)
        models[limited_id - 1] = replace(models[limited_id - 1], context_window=exact)
        created = await ConversationCatalog().create(db, rag_users[0], payload)
        frozen = next(item for item in created.configuration["models"] if item["modelConfigId"] == limited_id)
        assert frozen["contextWindow"] == exact

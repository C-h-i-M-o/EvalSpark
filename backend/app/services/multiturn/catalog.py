"""多轮会话配置、分页和知识库版本快照，不触发供应商请求。"""

import hashlib
import json

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationTurn, ConversationContext
from app.services.multiturn.context_status import project_context_status
from app.models.knowledge_base import KnowledgeDocument
from app.schemas.conversation import ConversationCreate, ConversationListRead, ConversationRead, TurnListRead, TurnRead
from app.services.embedding_config_service import get_config, runtime_config
from app.services.knowledge_base_service import require_owned_knowledge_base
from app.services.model_config_service import RuntimeModelConfig, model_config_service
from app.services.multiturn.store import ConversationError, ConversationStore
from app.services.rag.usage import model_snapshot


def model_identity(model: RuntimeModelConfig) -> str:
    """固定调用目标身份但不保存地址或凭据，允许凭据正常轮换。"""
    raw = json.dumps([model.provider_name, model.model_name, model.base_url.rstrip("/")], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def knowledge_snapshot(db: AsyncSession, knowledge_base_id: int, user_id: int) -> dict[str, object]:
    """读取拥有者可用的索引版本；每轮再次比较，拒绝混用新旧证据。"""
    library = await require_owned_knowledge_base(db, knowledge_base_id, user_id)
    versions = (await db.execute(select(KnowledgeDocument.id, KnowledgeDocument.index_revision).where(
        KnowledgeDocument.knowledge_base_id == knowledge_base_id, KnowledgeDocument.user_id == user_id,
        KnowledgeDocument.status == "ready", KnowledgeDocument.chunk_count > 0).order_by(KnowledgeDocument.id))).all()
    if library.status != "ready" or not versions:
        raise ConversationError("knowledge_base_not_ready", "知识库尚未就绪或没有可检索内容", 409)
    runtime = runtime_config(await get_config(db))
    return {"knowledgeBaseId": knowledge_base_id, "contentRevision": library.content_revision,
        "documents": [[document_id, revision] for document_id, revision in versions],
        "embeddingCollection": runtime.rag_embedding_collection, "embeddingRevision": runtime.rag_embedding_revision}


def serialize_conversation(value: Conversation, user_id: int) -> ConversationRead:
    """只公开白名单配置，内部目标指纹和索引版本不通过会话列表返回。"""
    config = value.config_json or {}
    visible = {key: config[key] for key in ("modelIds", "judgeModelId", "summaryModelId", "enableThinking",
        "inputBudget", "models", "knowledgeBaseId", "scoreVersion") if key in config}
    return ConversationRead(id=value.id, title=value.title, mode=value.mode, ownerId=value.user_id,
        canContinue=value.user_id == user_id, visibility=value.visibility, currentTurn=value.current_turn,
        generationStatus=value.generation_status, configuration=visible,
        createdAt=value.created_at, updatedAt=value.updated_at)


class ConversationCatalog:
    """会话元数据与生成执行分离，公开读取不会意外触发模型调用。"""

    def __init__(self) -> None:
        """初始化无全局可变状态的存储边界。"""
        self.store = ConversationStore()

    async def create(self, db: AsyncSession, user_id: int, payload: ConversationCreate) -> ConversationRead:
        """确认候选、评审、摘要模型可用后冻结非秘密配置。"""
        summary_id = payload.summary_model_id or payload.judge_model_id or payload.model_ids[0]
        ids = set(payload.model_ids) | {summary_id}
        if payload.judge_model_id is not None:
            ids.add(payload.judge_model_id)
        models = await model_config_service.resolve_runtime_models(db, sorted(ids))
        by_id = {model.id: model for model in models}
        for model in models:
            if model.context_window is not None and payload.input_budget + model.max_tokens > model.context_window:
                raise ConversationError("conversation_context_exceeded",
                    f"模型 {model.display_name} 的输入预算与最大输出之和超过总上下文容量", 422)
        if set(by_id) != ids or any(not model.api_key for model in models):
            raise ConversationError("conversation_models_unavailable", "候选、摘要或评审模型不存在或已停用", 422)
        if payload.judge_model_id is not None:
            judge = by_id[payload.judge_model_id]
            if any((judge.provider_name, judge.model_name) == (by_id[model_id].provider_name, by_id[model_id].model_name)
                   for model_id in payload.model_ids):
                raise ConversationError("conversation_self_judge", "评审模型不能与候选使用相同供应商和模型", 422)
        snapshot = await knowledge_snapshot(db, payload.knowledge_base_id, user_id) if payload.knowledge_base_id is not None else None
        config: dict[str, object] = {"modelIds": payload.model_ids, "judgeModelId": payload.judge_model_id,
            "summaryModelId": summary_id, "enableThinking": payload.enable_thinking, "inputBudget": payload.input_budget,
            "knowledgeBaseId": payload.knowledge_base_id, "scoreVersion": f"{payload.mode}-multiturn-v1",
            "models": [model_snapshot(model).model_dump(mode="json", by_alias=True) for model in models],
            "identities": {str(model.id): model_identity(model) for model in models}}
        value = await self.store.create(db, user_id, mode=payload.mode, title=payload.title,
            visibility=payload.visibility, config=config, knowledge_snapshot=snapshot)
        return serialize_conversation(value, user_id)

    async def get(self, db: AsyncSession, conversation_id: int, user_id: int) -> ConversationRead:
        """读取有权访问的会话及当前生成状态。"""
        return serialize_conversation(await self.store.get(db, conversation_id, user_id), user_id)

    async def list(self, db: AsyncSession, user_id: int, *, mode: str, page: int, page_size: int) -> ConversationListRead:
        """按工作台模式列出本人或公开会话，限制分页范围。"""
        if mode not in ("chat", "rag") or page < 1 or not 1 <= page_size <= 100:
            raise ConversationError("conversation_invalid_page", "会话模式或分页参数无效", 422)
        filters = (Conversation.mode == mode, or_(Conversation.user_id == user_id, Conversation.visibility == "public"))
        total = await db.scalar(select(func.count()).select_from(Conversation).where(*filters))
        rows = list((await db.scalars(select(Conversation).where(*filters).order_by(Conversation.id.desc())
            .offset((page - 1) * page_size).limit(page_size))).all())
        return ConversationListRead(items=[serialize_conversation(row, user_id) for row in rows], total=total or 0,
            page=page, pageSize=page_size)

    async def turns(self, db: AsyncSession, conversation_id: int, user_id: int, *, page: int, page_size: int) -> TurnListRead:
        """分页读取已授权会话的轮次，回答详情继续使用现有任务接口。"""
        await self.store.get(db, conversation_id, user_id)
        if page < 1 or not 1 <= page_size <= 100:
            raise ConversationError("conversation_invalid_page", "轮次分页参数无效", 422)
        criterion = ConversationTurn.conversation_id == conversation_id
        total = await db.scalar(select(func.count()).select_from(ConversationTurn).where(criterion))
        rows = list((await db.scalars(select(ConversationTurn).where(criterion).order_by(ConversationTurn.turn_index)
            .offset((page - 1) * page_size).limit(page_size))).all())
        contexts = (await db.scalars(select(ConversationContext).where(
            ConversationContext.conversation_id == conversation_id, ConversationContext.turn_id.in_([row.id for row in rows]))
            .order_by(ConversationContext.attempt.desc(), ConversationContext.id.desc()))).all() if rows else []
        projected = {}
        for context in contexts:
            projected.setdefault((context.turn_id, context.model_config_id), project_context_status(context))
        return TurnListRead(items=[TurnRead(id=row.id, taskId=row.task_id, turnIndex=row.turn_index,
            prompt=row.prompt, generationStatus=row.generation_status, errorCode=row.error_code, createdAt=row.created_at,
            contexts=[status for (turn_id, _), statuses in projected.items() if turn_id == row.id for status in statuses])
            for row in rows], total=total or 0, page=page, pageSize=page_size)

    async def set_visibility(self, db: AsyncSession, conversation_id: int, user_id: int, visibility: str) -> ConversationRead:
        """作者修改权限时同步所有轮次任务。"""
        return serialize_conversation(await self.store.set_visibility(db, conversation_id, user_id, visibility), user_id)


conversation_catalog = ConversationCatalog()

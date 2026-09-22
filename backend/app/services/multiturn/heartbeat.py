"""标记生成请求仍然存活；不重放供应商调用，也不自行结束轮次。"""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.conversation import Conversation, ConversationTurn

logger = logging.getLogger(__name__)


async def pulse_generation(sessions: async_sessionmaker[AsyncSession], conversation_id: int,
                           turn_id: int, owner: int, *, now: datetime | None = None, generation_epoch: int = 1) -> bool:
    """只有当前作者的生成轮次可以刷新心跳，旧轮次不能给后续轮次续期。"""
    current_turn = exists(select(ConversationTurn.id).where(ConversationTurn.id == turn_id,
        ConversationTurn.conversation_id == Conversation.id,
        ConversationTurn.turn_index == Conversation.current_turn,
        ConversationTurn.generation_status == "generating", ConversationTurn.generation_epoch == generation_epoch))
    async with sessions() as db:
        result = await db.execute(update(Conversation).where(Conversation.id == conversation_id,
            Conversation.user_id == owner, Conversation.generation_status == "generating", current_turn)
            .values(updated_at=now or datetime.utcnow()).execution_options(synchronize_session=False))
        await db.commit()
        return result.rowcount > 0


def start_generation_heartbeat(sessions: async_sessionmaker[AsyncSession], conversation_id: int,
                               turn_id: int, owner: int, *, interval: float = 60, generation_epoch: int = 1) -> asyncio.Task[None]:
    """启动可取消的单请求心跳，生成器负责在所有退出路径等待其结束。"""
    if interval <= 0:
        raise ValueError("心跳间隔必须大于零")

    async def run() -> None:
        """数据库暂不可用时留待下次心跳，轮次已结束则退出，不触发生成重试。"""
        while True:
            try:
                if not await pulse_generation(sessions, conversation_id, turn_id, owner, generation_epoch=generation_epoch):
                    return
            except Exception:
                logger.warning("多轮生成心跳暂未保存，等待下次更新")
            await asyncio.sleep(interval)

    return asyncio.create_task(run(), name=f"conversation-heartbeat:{conversation_id}:{turn_id}")

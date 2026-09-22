"""按变更轮次保存要求版本，旧评分可重建当时有效的约束。"""
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationRequirement, ConversationTurn
from app.services.multiturn.requirements import Requirement
from app.services.multiturn.store import ConversationStore, ConversationError


async def load_requirements(db: AsyncSession, conversation_id: int, owner: int,
                            *, through_turn: int) -> tuple[Requirement, ...]:
    """只读取作者权限下截至目标轮次的最新版本，不泄漏未来修改。"""
    conversation = await ConversationStore().get(db, conversation_id, owner, owner_only=True)
    if type(through_turn) is not int or not 0 <= through_turn <= conversation.current_turn:
        raise ConversationError("requirement_turn_invalid", "要求记录的截止轮次无效", 422)
    rows = list((await db.scalars(select(ConversationRequirement).where(
        ConversationRequirement.conversation_id == conversation_id,
        ConversationRequirement.source_turn <= through_turn,
    ).order_by(ConversationRequirement.requirement_key, ConversationRequirement.version))).all())
    latest: dict[str, Requirement] = {}
    for row in rows:
        latest[row.requirement_key] = Requirement.model_validate_json(json.dumps(row.detail_json))
    return tuple(latest.values())


async def save_requirements(db: AsyncSession, conversation_id: int, owner: int, *, turn_id: int,
                            records: tuple[Requirement, ...]) -> None:
    """保存本轮新增与退休版本，禁止删除、改写原文或覆盖历史版本。"""
    conversation = await ConversationStore().get(db, conversation_id, owner, owner_only=True, lock=True)
    turn = await db.scalar(select(ConversationTurn).where(ConversationTurn.id == turn_id,
                                                        ConversationTurn.conversation_id == conversation_id))
    if turn is None or turn.generation_status != "generating" or turn.turn_index != conversation.current_turn:
        raise ConversationError("requirement_turn_conflict", "要求提取只能保存到当前生成轮次", 409)
    prior = list((await db.scalars(select(ConversationRequirement).where(
        ConversationRequirement.conversation_id == conversation_id,
    ).order_by(ConversationRequirement.version))).all())
    latest = {row.requirement_key: row for row in prior}
    incoming = {record.id: record for record in records}
    if len(incoming) != len(records) or not latest.keys() <= incoming.keys():
        raise ConversationError("requirement_history_conflict", "要求记录不能重复或删除已有历史", 409)
    additions: list[ConversationRequirement] = []
    for record in records:
        serialized = record.model_dump(mode="json")
        old = latest.get(record.id)
        if old is not None and old.detail_json == serialized:
            continue
        if old is not None:
            allowed = {**old.detail_json, "retired_at": turn.turn_index}
            if old.detail_json.get("retired_at") is not None or serialized != allowed:
                raise ConversationError("requirement_history_conflict", "已有要求仅允许新增退休版本", 409)
            if not any(record.id in other.supersedes and other.source_turn == turn.turn_index for other in records):
                raise ConversationError("requirement_history_conflict", "退休要求必须关联本轮替代记录", 409)
        else:
            if (record.source_turn != turn.turn_index or record.source_id != f"turn:{turn.id}:user"
                    or not record.quote.strip() or record.quote not in turn.prompt or record.retired_at is not None):
                raise ConversationError("requirement_source_invalid", "新增要求必须引用本轮用户原文", 422)
            if any(key not in latest for key in record.supersedes):
                raise ConversationError("requirement_source_invalid", "替代关系引用未知要求", 422)
            if any(incoming[key].retired_at != turn.turn_index for key in record.supersedes):
                raise ConversationError("requirement_history_conflict", "替代要求必须同时结束旧要求", 409)
            if record.ambiguous and (record.critical or record.supersedes):
                raise ConversationError("requirement_source_invalid", "歧义要求不能作为关键要求或撤销旧要求", 422)
        additions.append(ConversationRequirement(conversation_id=conversation_id, requirement_key=record.id,
            version=old.version + 1 if old else 1, source_turn=turn.turn_index, detail_json=serialized))
    db.add_all(additions)
    await db.commit()

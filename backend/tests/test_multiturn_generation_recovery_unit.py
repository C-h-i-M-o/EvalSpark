"""未迁移环境不执行多轮恢复查询或写入。"""
import pytest
from sqlalchemy import create_engine, inspect

from app.services.multiturn.generation_recovery import recover_generations
from test_rag_evaluation_store import AsyncTestSession


@pytest.mark.asyncio
async def test_unmigrated_database_skips_generation_recovery() -> None:
    """真实内存 SQLite 没有新表时正常退出，不创建表或落入新字段查询。"""
    engine = create_engine("sqlite://")
    try:
        await recover_generations(lambda: AsyncTestSession(engine))
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()

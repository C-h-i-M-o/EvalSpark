import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture(scope="session")
def rag_test_database() -> dict[str, int]:
    """所有真实写入先核对显式开关、测试服务和数据库名。"""
    if os.environ.get("RAG_INTEGRATION_TESTS") != "1":
        pytest.skip("需要显式启用隔离 RAG 集成测试")
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host == "mysql-test" and url.database == "multichateval_rag_test"
    engine = create_engine(url.set(drivername="mysql+pymysql"))
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT DATABASE()")) == "multichateval_rag_test"
        initialized = connection.scalar(text(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='alembic_version'"
        ))
        if not initialized:
            source = (Path(__file__).resolve().parents[2] / "docker/mysql/init/001_schema.sql").read_text(encoding="utf-8")
            source = source.replace("CREATE DATABASE IF NOT EXISTS multichateval ", "CREATE DATABASE IF NOT EXISTS multichateval_rag_test ")
            source = source.replace("USE multichateval;", "USE multichateval_rag_test;")
            for statement in source.split(";"):
                if statement.strip():
                    connection.exec_driver_sql(statement)
    config = Config("alembic.ini")
    with engine.connect() as connection:
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
    legacy_id = 0
    if version == "20260612_01":
        command.upgrade(config, "20260705_03")
        version = "20260705_03"
    if version == "20260705_03":
        with engine.begin() as connection:
            result = connection.execute(text(
                "INSERT INTO evaluation_tasks(user_id,prompt,status,visibility) VALUES (0,'迁移保留原问题','completed','private')"
            ))
            legacy_id = result.lastrowid
            response = connection.execute(text(
                "INSERT INTO model_responses(task_id,answer_text) VALUES (:task_id,'迁移保留原回答')"
            ), {"task_id": legacy_id})
            connection.execute(text(
                "INSERT INTO evaluation_results(response_id,rule_score,final_score) VALUES (:id,8,7.85)"
            ), {"id": response.lastrowid})
    command.upgrade(config, "head")
    with engine.connect() as connection:
        legacy_id = connection.scalar(text("SELECT MIN(id) FROM evaluation_tasks WHERE prompt='迁移保留原问题'"))
        assert legacy_id is not None
    engine.dispose()
    return {"legacy_task_id": legacy_id}


@pytest_asyncio.fixture
async def rag_sessions(rag_test_database: dict[str, int]) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()


@pytest_asyncio.fixture
async def rag_users(rag_sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    from app.models.user import User
    async with rag_sessions() as db:
        owner = User(username=f"rag_owner_{uuid4().hex}", password_hash="test-only", role="user", status="active")
        other = User(username=f"rag_admin_{uuid4().hex}", password_hash="test-only", role="admin", status="active")
        db.add_all([owner, other])
        await db.commit()
        # 测试库保留数据便于复核，每轮使用唯一用户，不清空任何现存表。
        return owner.id, other.id

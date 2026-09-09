from decimal import Decimal

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_migration_adds_tables_without_changing_legacy_answers_or_scores(rag_sessions, rag_test_database) -> None:
    async with rag_sessions() as db:
        rows = (await db.execute(text(
            "SELECT t.prompt,t.task_type,r.answer_text,e.final_score FROM evaluation_tasks t "
            "JOIN model_responses r ON r.task_id=t.id JOIN evaluation_results e ON e.response_id=r.id "
            "WHERE t.id=:id"
        ), {"id": rag_test_database["legacy_task_id"]})).all()
        assert rows
        assert all(tuple(row) == ("迁移保留原问题", "chat", "迁移保留原回答", Decimal("7.85")) for row in rows)
        assert await db.scalar(text("SELECT version_num FROM alembic_version")) == "20260909_01"
        tables = (await db.execute(text("SHOW TABLES"))).scalars().all()
        assert {"knowledge_bases", "knowledge_documents", "knowledge_chunks", "rag_jobs", "rag_response_details"}.issubset(tables)

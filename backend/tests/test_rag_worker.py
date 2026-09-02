import importlib
from uuid import uuid4

import pytest

from app.worker import celery_app


def test_worker_registers_index_job_and_recovery_bootstep() -> None:
    assert "app.worker.run_rag_job" in celery_app.tasks
    assert any(step.__name__ == "RagRecoveryStep" for step in celery_app.steps["worker"])
    assert celery_app.conf.worker_concurrency == 1
    assert celery_app.conf.task_acks_late


@pytest.mark.asyncio
async def test_recovery_publishes_committed_jobs_and_tracks_delivery(rag_sessions, rag_users, tmp_path) -> None:
    from test_rag_jobs import queued_document
    _, _, accepted = await queued_document(rag_sessions, rag_users[0], tmp_path)
    assert importlib.util.find_spec("app.services.rag.recovery") is not None, "尚未实现恢复投递"
    module = importlib.import_module("app.services.rag.recovery")
    from app.models.knowledge_base import RagJob
    published = []
    def publisher(job_id: str) -> bool:
        published.append(job_id)
        return True
    # 测试库保留先前用例数据，批次取大以验证本次作业；线上默认每轮 100 个。
    await module.recover_jobs(rag_sessions, publisher, limit=10_000)
    assert accepted.job_id in published
    async with rag_sessions() as db:
        job = await db.get(RagJob, accepted.job_id)
        assert job.dispatched_at is not None and job.status == "queued"

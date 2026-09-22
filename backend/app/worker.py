"""RAG 异步作业入口；业务状态保存在 MySQL，不使用 Celery 结果后端。"""

import asyncio
import logging
import threading
from uuid import UUID

from celery import Celery, bootsteps

from app.core.config import settings

celery_app = Celery("multichateval.rag", broker=str(settings.rag_redis_url))
celery_app.conf.update(
    task_default_queue="rag",
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_concurrency=1,
    worker_prefetch_multiplier=1,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_soft_time_limit=10500,
    task_time_limit=10800,
    broker_transport_options={"visibility_timeout": 14400, "socket_connect_timeout": 5, "socket_timeout": 5},
    broker_connection_timeout=5,
    broker_connection_retry_on_startup=True,
    timezone="Asia/Shanghai",
)

logger = logging.getLogger(__name__)


async def _run_assessment(job_id: int) -> None:
    """为本次 Worker 事件循环创建独立连接池并执行持久化评审。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.services.multiturn.dispatch import run_assessment_job
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        await run_assessment_job(async_sessionmaker(engine, expire_on_commit=False), job_id)
    finally:
        await engine.dispose()


@celery_app.task(name="app.worker.run_conversation_assessment")
def run_conversation_assessment(job_id: int) -> None:
    """队列重复投递交由数据库认领防重，不自动重试收费请求。"""
    if type(job_id) is not int or job_id <= 0:
        logger.warning("多轮评分作业标识无效")
        return
    try:
        asyncio.run(_run_assessment(job_id))
    except Exception:
        logger.warning("多轮评分执行中断，请检查数据库任务状态")


async def _run_job(job_id: str) -> None:
    from qdrant_client import AsyncQdrantClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.services.rag.clients import EmbeddingClient, VectorStore
    from app.services.rag.indexing import IndexRunner

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    vectors = AsyncQdrantClient(url=str(settings.rag_qdrant_url), timeout=settings.rag_embedding_timeout_seconds, check_compatibility=False, trust_env=False)
    try:
        await IndexRunner(async_sessionmaker(engine, expire_on_commit=False), vectors=VectorStore(vectors), embedding=EmbeddingClient(), resolve_config=True).run(job_id)
    finally:
        await vectors.close()
        await engine.dispose()


@celery_app.task(name="app.worker.run_rag_job")
def run_rag_job(job_id: str) -> None:
    try:
        UUID(job_id)
        asyncio.run(_run_job(job_id))
    except Exception:
        # DB 故障等无法落状态时保留原租约，恢复循环稍后重领，不泄漏原始异常。
        logger.warning("RAG 作业执行中断，等待数据库状态恢复或租约重领")


class RagRecoveryStep(bootsteps.StartStopStep):
    requires = {"celery.worker.components:Pool"}

    def start(self, worker) -> None:
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="rag-recovery", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        from app.services.rag.recovery import recover_once
        while not self.stopping.is_set():
            try:
                asyncio.run(recover_once())
            except Exception:
                logger.warning("RAG 作业恢复暂不可用，将在下一轮重试")
            self.stopping.wait(60)

    def stop(self, worker) -> None:
        self.stopping.set()
        self.thread.join(timeout=10)

    terminate = stop


celery_app.steps["worker"].add(RagRecoveryStep)

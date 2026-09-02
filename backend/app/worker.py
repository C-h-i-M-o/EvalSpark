"""RAG 异步作业入口；业务状态保存在 MySQL，不使用 Celery 结果后端。"""

from celery import Celery

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
    broker_transport_options={"visibility_timeout": 14400},
    broker_connection_retry_on_startup=True,
    timezone="Asia/Shanghai",
)

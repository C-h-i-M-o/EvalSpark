from fastapi import APIRouter
from app.api.v1 import embedding_config

from app.api.v1 import admin_users, auth, evaluation, feedback_stats, health, knowledge_bases, model_configs, models, scoring, token_usage

api_router = APIRouter()
api_router.include_router(embedding_config.router, prefix="/admin/embedding-config", tags=["admin-embedding"])
api_router.include_router(embedding_config.public_router, prefix="/embedding-config", tags=["embedding"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(knowledge_bases.router, prefix="/knowledge-bases", tags=["knowledge-bases"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(evaluation.router, prefix="/evaluation", tags=["evaluation"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(token_usage.router, prefix="/token-usage", tags=["token-usage"])
api_router.include_router(feedback_stats.router, prefix="/feedback-stats", tags=["feedback-stats"])
api_router.include_router(admin_users.router, prefix="/admin/users", tags=["admin-users"])
api_router.include_router(
    feedback_stats.admin_router,
    prefix="/admin/feedback-stats",
    tags=["admin-feedback-stats"],
)
api_router.include_router(
    model_configs.router,
    prefix="/admin/model-configs",
    tags=["admin-model-configs"],
)
api_router.include_router(
    scoring.router,
    prefix="/admin/scoring",
    tags=["admin-scoring"],
)

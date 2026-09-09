from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT_DIR / ".env"


class Settings(BaseSettings):
    app_name: str = "MultiChatEval"
    app_env: str = "development"
    database_url: str = "mysql+aiomysql://multichateval:multichateval@localhost:3306/multichateval"
    backend_cors_origins: str = "http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174"
    jwt_secret_key: str = "development-only-change-this-secret-before-production-2026"
    access_token_expire_minutes: int = 480
    auth_cookie_secure: bool = False

    # RAG 依赖只在实际调用时连接，普通评测不等待模型缓存就绪。
    rag_embedding_url: AnyHttpUrl = AnyHttpUrl("http://embedding:80")
    rag_qdrant_url: AnyHttpUrl = AnyHttpUrl("http://qdrant:6333")
    rag_redis_url: RedisDsn = RedisDsn("redis://redis:6379/0")
    rag_embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    rag_embedding_protocol: Literal["tei", "openai"] = "tei"
    rag_embedding_api_key: SecretStr = SecretStr("")
    rag_embedding_dimensions: int = Field(default=1024, ge=1, le=65536)
    rag_embedding_query_prefix: str = "Instruct: 根据问题检索能够支持回答的文档片段\nQuery: "
    rag_embedding_collection: str = "rag_chunks_v1"
    rag_embedding_revision: str = Field(
        default="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3", pattern=r"^[0-9a-f]{40}$"
    )
    rag_embedding_batch_size: int = Field(default=16, ge=1, le=16)
    rag_embedding_max_batch_tokens: int = Field(default=2048, ge=1, le=32768)
    rag_embedding_timeout_seconds: float = Field(default=60, gt=0, le=300)
    rag_model_cache_dir: Path = Path("/data")
    rag_documents_dir: Path = Path("/documents")

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.backend_cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

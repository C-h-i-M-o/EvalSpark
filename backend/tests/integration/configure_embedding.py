"""仅为隔离验收选择确定性 API 或历史 TEI；运行前必须停止两个测试 Worker。"""

import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models.embedding import EmbeddingConfig
from app.schemas.embedding import EmbeddingConfigPayload
from app.services.embedding_config_service import payload_values


def main() -> None:
    mode = sys.argv[1]
    url = make_url(os.environ["DATABASE_URL"])
    if mode not in ("api", "tei") or os.environ.get("RAG_INTEGRATION_TESTS") != "1" or url.host != "mysql-test" or url.database != "multichateval_rag_test":
        raise RuntimeError("拒绝在非隔离测试库配置 Embedding")
    engine = create_engine(url.set(drivername="mysql+pymysql"))
    try:
        with Session(engine) as db, db.begin():
            row = db.scalar(select(EmbeddingConfig).where(EmbeddingConfig.id == 1).with_for_update())
            if row is None:
                raise RuntimeError("隔离测试库尚未迁移")
            row.settings_json = payload_values(EmbeddingConfigPayload(version=row.version,
                base_url="http://model-test:8080/v1", api_key="rag-acceptance-only", model_name="embedding-test", dimensions=3), None) if mode == "api" else {}
            row.version += 1
        print(f"隔离测试 Embedding 模式：{mode}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

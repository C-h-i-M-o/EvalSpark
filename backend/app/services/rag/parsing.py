"""受限文档解析进程：仅供 Docker Worker 调用，不执行文档中的代码。"""

import json
import logging
import resource
import sys
from pathlib import Path


def main() -> None:
    # 在加载解析库之前设限；原文和库内部异常不得进入 Worker 日志。
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (120, 121))
    logging.disable(logging.CRITICAL)
    from app.services.rag.documents import parse_document
    from app.services.rag.errors import KnowledgeBaseError

    try:
        blocks = parse_document(Path(sys.argv[1]), sys.argv[2])
        payload = {"blocks": [
            {"text": block.text, "source": block.source.model_dump(), "markdown": block.markdown, "heading": block.heading}
            for block in blocks
        ]}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    except KnowledgeBaseError as error:
        sys.stdout.write(json.dumps({"error": error.code, "message": error.message, "status": error.status_code}))
    except Exception:
        sys.stdout.write(json.dumps({"error": "document_too_complex", "message": "文档解析失败，请检查或拆分文档", "status": 415}))


if __name__ == "__main__":
    main()

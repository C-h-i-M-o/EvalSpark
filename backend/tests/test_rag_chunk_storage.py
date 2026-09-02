from uuid import uuid4

import pytest

from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument


@pytest.mark.asyncio
async def test_long_token_valid_chunk_is_stored_without_byte_truncation(rag_sessions, rag_users) -> None:
    # Qwen 可将连续空格合并为少量 Token；Token 合规不意味着 UTF-8 正文小于 64 KiB。
    content = ("word" + " " * 128) * 600 + "end"
    async with rag_sessions() as db:
        kb = KnowledgeBase(user_id=rag_users[0], name="长块存储验证")
        db.add(kb)
        await db.flush()
        doc = KnowledgeDocument(knowledge_base_id=kb.id, user_id=rag_users[0], original_name="空白密集.txt", storage_key=f"{uuid4().hex}.txt", media_type="text/plain", size_bytes=len(content), content_hash="0" * 64)
        db.add(doc)
        await db.flush()
        chunk = KnowledgeChunk(id=str(uuid4()), document_id=doc.id, knowledge_base_id=kb.id, user_id=rag_users[0], index_revision=1, chunk_index=0, text=content, token_count=1802, source_json={"kind": "text", "line_start": 1, "line_end": 1})
        db.add(chunk)
        await db.commit()
        chunk_id = chunk.id
    async with rag_sessions() as db:
        stored = await db.get(KnowledgeChunk, chunk_id)
        assert stored.text == content and len(stored.text.encode()) > 65_535

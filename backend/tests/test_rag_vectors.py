from uuid import uuid4
from contextlib import asynccontextmanager

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.services.rag import clients


@asynccontextmanager
async def vector_client():
    client = AsyncQdrantClient(":memory:")
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_vector_storage_is_idempotent_scoped_and_contains_no_text() -> None:
    store_type = getattr(clients, "VectorStore", None)
    assert store_type is not None, "尚未实现索引存储"
    async with vector_client() as client:
        store = store_type(client)
        await store.ensure_collection()
        point_id = str(uuid4())
        await store.upsert(1, 11, 111, 1, [(point_id, 0)], [[1.0] + [0.0] * 1023])
        await store.upsert(1, 11, 111, 1, [(point_id, 0)], [[1.0] + [0.0] * 1023])
        await store.upsert(2, 22, 222, 1, [(str(uuid4()), 0)], [[1.0] + [0.0] * 1023])
        assert await store.count(1, 11, 111, 1) == 1
        points = await client.retrieve(clients.VECTOR_COLLECTION, [point_id])
        assert points[0].payload == {"user_id": 1, "knowledge_base_id": 11, "document_id": 111, "index_revision": 1, "chunk_index": 0}
        await store.delete(1, 11, 111)
        assert await store.count(1, 11, 111) == 0
        assert await store.count(2, 22, 222) == 1


@pytest.mark.asyncio
async def test_vector_storage_rejects_existing_incompatible_collection() -> None:
    store_type = getattr(clients, "VectorStore", None)
    assert store_type is not None, "尚未实现索引存储"
    async with vector_client() as client:
        await client.create_collection(clients.VECTOR_COLLECTION, vectors_config=models.VectorParams(size=3, distance=models.Distance.DOT))
        with pytest.raises(clients.RagClientError) as error:
            await store_type(client).ensure_collection()
        assert error.value.code == "vector_collection_mismatch"
        info = await client.get_collection(clients.VECTOR_COLLECTION)
        assert info.config.params.vectors.size == 3


@pytest.mark.asyncio
async def test_old_version_cleanup_keeps_published_version() -> None:
    store_type = getattr(clients, "VectorStore", None)
    assert store_type is not None, "尚未实现索引存储"
    async with vector_client() as client:
        store = store_type(client)
        await store.ensure_collection()
        for revision in (1, 2):
            await store.upsert(1, 11, 111, revision, [(str(uuid4()), 0)], [[1.0] + [0.0] * 1023])
        await store.delete(1, 11, 111, except_revision=2)
        assert await store.count(1, 11, 111) == 1
        assert await store.count(1, 11, 111, 2) == 1

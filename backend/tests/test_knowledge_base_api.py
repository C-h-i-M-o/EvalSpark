from fastapi.testclient import TestClient
import pytest

from app.main import app


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/knowledge-bases"), ("POST", "/api/knowledge-bases"),
    ("GET", "/api/knowledge-bases/1/documents"),
    ("POST", "/api/knowledge-bases/1/documents"),
    ("GET", "/api/knowledge-bases/1/documents/1/download"),
])
def test_knowledge_base_endpoints_require_login(method: str, path: str) -> None:
    response = TestClient(app).request(method, path)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_owner_api_and_administrator_isolation(rag_sessions, rag_users, tmp_path, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient
    from app.api.dependencies import get_current_user
    from app.api.v1 import knowledge_bases
    from app.db.session import get_db
    from app.main import create_app
    from app.models.user import User
    from app.services.knowledge_base_service import KnowledgeBaseService

    local_app = create_app()
    active_user = rag_users[0]
    async def database():
        async with rag_sessions() as db:
            yield db
    async def current_user():
        async with rag_sessions() as db:
            return await db.get(User, active_user)
    local_app.dependency_overrides[get_db] = database
    local_app.dependency_overrides[get_current_user] = current_user
    monkeypatch.setattr(knowledge_bases, "knowledge_base_service", KnowledgeBaseService(documents_dir=tmp_path, publisher=lambda _: False))

    async with AsyncClient(transport=ASGITransport(app=local_app), base_url="http://test") as client:
        response = await client.post("/api/knowledge-bases", json={"name": "  私有知识库  "})
        assert response.status_code == 201, response.text
        kb = response.json()
        assert kb["name"] == "私有知识库" and kb["chunkSize"] == 800 and not kb["available"]
        path = f'/api/knowledge-bases/{kb["id"]}'
        assert (await client.post("/api/knowledge-bases", json={"name": "伪造", "userId": rag_users[1]})).status_code == 422
        assert (await client.get("/api/knowledge-bases?pageSize=101")).status_code == 422
        assert (await client.patch(path, json={"chunkSize": None})).status_code == 422
        assert (await client.post(path + "/reindex")).status_code == 409
        response = await client.post(path + "/documents", files={"file": ("规则.txt", "私有测试内容".encode(), "text/plain")})
        assert response.status_code == 202, response.text
        accepted = response.json()
        doc_path = path + f'/documents/{accepted["documentId"]}'
        assert accepted["dispatchPending"] and accepted["status"] == "queued"
        listing = (await client.get(path + "/documents")).json()
        assert listing["total"] == 1
        assert listing["items"][0]["currentJob"]["id"] == accepted["jobId"]
        for hidden in ["userId", "storageKey", "contentHash", "dispatchedAt", "leaseExpiresAt"]:
            assert hidden not in str(listing)
        downloaded = await client.get(doc_path + "/download")
        assert downloaded.content == "私有测试内容".encode()
        assert downloaded.headers["content-disposition"].startswith("attachment;")
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert (await client.post(doc_path + "/retry")).json()["jobId"] == accepted["jobId"]
        assert (await client.patch(path, json={"chunkSize": 1000})).status_code == 409

        active_user = rag_users[1]  # 管理员也不能访问其他用户的私有库。
        for method, target in [
            ("GET", path), ("PATCH", path), ("DELETE", path), ("POST", path + "/reindex"),
            ("GET", path + "/documents"), ("POST", path + "/documents"),
            ("GET", doc_path + "/download"), ("DELETE", doc_path), ("POST", doc_path + "/retry"),
        ]:
            response = await client.request(method, target, **({"json": {"name": "越权"}} if method == "PATCH" else {}))
            assert response.status_code == 404, (method, target, response.text)
            assert response.json()["detail"]["code"] == "knowledge_base_not_found"
        assert (await client.get("/api/knowledge-bases")).json()["total"] == 0
        assert len(list(tmp_path.iterdir())) == 1

        active_user = rag_users[0]
        for form in [
            {"files": {"other": ("a.txt", b"hello", "text/plain")}},
            {"files": {"file": ("a.txt", b"hello", "text/plain")}, "data": {"userId": "2"}},
        ]:
            assert (await client.post(path + "/documents", **form)).status_code == 422
        assert (await client.post(path + "/documents", files={"file": ("a.pdf", b"fake", "application/pdf")})).status_code == 415
        first = await client.delete(doc_path)
        second = await client.delete(doc_path)
        assert first.status_code == second.status_code == 202
        assert first.json()["jobId"] == second.json()["jobId"]
        assert (await client.get(doc_path + "/download")).status_code == 404
        deletion = await client.delete(path)
        assert deletion.status_code == 202
        assert (await client.delete(path)).json()["jobId"] == deletion.json()["jobId"]
        assert (await client.patch(path, json={"name": "不能修改"})).status_code == 409

import io

import pytest
from starlette.datastructures import UploadFile
from starlette.requests import Request

from app.services.rag.documents import read_multipart_upload
from app.services.rag.errors import KnowledgeBaseError


def request_for(body: bytes, content_type: str = "multipart/form-data; boundary=test", length: int | None = None) -> Request:
    headers = [(b"content-type", content_type.encode())]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    source = io.BytesIO(body)
    async def receive() -> dict[str, object]:
        data = source.read(65536)
        return {"type": "http.request", "body": data, "more_body": source.tell() < len(body)}
    return Request({"type": "http", "method": "POST", "headers": headers}, receive)


def file_part(name: str = "file", content: bytes = b"hello") -> bytes:
    return (f'--test\r\nContent-Disposition: form-data; name="{name}"; filename="a.txt"\r\n'
            'Content-Type: text/plain\r\n\r\n').encode() + content + b"\r\n"


@pytest.mark.asyncio
async def test_single_file_is_parsed_and_closed_after_context() -> None:
    request = request_for(file_part() + b"--test--\r\n")
    async with read_multipart_upload(request) as value:
        assert isinstance(value, UploadFile)
        assert await value.read() == b"hello"
    assert value.file.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    file_part("other") + b"--test--\r\n",
    file_part() + file_part() + b"--test--\r\n",
    b'--test\r\nContent-Disposition: form-data; name="userId"\r\n\r\n2\r\n--test--\r\n',
    b"--test--\r\n",
    file_part(),  # 缺少结束边界不能被当作完整文件接受。
])
async def test_multipart_rejects_extra_fields_files_and_incomplete_body(body: bytes) -> None:
    with pytest.raises(KnowledgeBaseError) as error:
        async with read_multipart_upload(request_for(body)):
            pytest.fail("无效表单不能被接受")
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_stream_limit_does_not_trust_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.rag.documents.MAX_MULTIPART_BYTES", 300)
    with pytest.raises(KnowledgeBaseError) as error:
        async with read_multipart_upload(request_for(file_part(content=b"a" * 500) + b"--test--\r\n", length=1)):
            pytest.fail("实际流量超限不能被接受")
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_non_multipart_and_missing_boundary_are_rejected() -> None:
    for content_type, expected in [("application/json", 415), ("multipart/form-data", 422)]:
        with pytest.raises(KnowledgeBaseError) as error:
            async with read_multipart_upload(request_for(b"{}", content_type)):
                pytest.fail("请求格式无效")
        assert error.value.status_code == expected


@pytest.mark.asyncio
async def test_partial_form_closes_spooled_file_on_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile
    created = []
    def tracked_temporary(*args, **kwargs):
        value = tempfile.SpooledTemporaryFile(*args, **kwargs)
        created.append(value)
        return value
    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", tracked_temporary)
    count = 0
    async def receive():
        nonlocal count
        count += 1
        if count == 1:
            return {"type": "http.request", "body": file_part(), "more_body": True}
        return {"type": "http.disconnect"}
    from starlette.requests import ClientDisconnect
    request = Request({"type": "http", "headers": [(b"content-type", b"multipart/form-data; boundary=test")]}, receive)
    with pytest.raises(ClientDisconnect):
        async with read_multipart_upload(request):
            pytest.fail("断开的上传不能被接受")
    assert created and all(value.closed for value in created)

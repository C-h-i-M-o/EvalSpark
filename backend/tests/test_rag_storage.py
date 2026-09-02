import hashlib
import io
import zipfile
import struct
from pathlib import Path

import pytest
from starlette.datastructures import Headers, UploadFile

from app.services.rag.documents import resolve_storage_path, store_upload
from app.services.rag.errors import KnowledgeBaseError


def upload(content: bytes, name: str = "资料.txt", mime: str = "text/plain") -> UploadFile:
    return UploadFile(io.BytesIO(content), filename=name, size=len(content), headers=Headers({"content-type": mime}))


@pytest.mark.asyncio
async def test_upload_uses_generated_key_not_supplied_path_and_hashes_exact_bytes(tmp_path: Path) -> None:
    content = "\ufeff公司制度\n第二行".encode("utf-8")
    result = await store_upload(upload(content, "../../secret.txt"), root=tmp_path)
    assert result.original_name == "secret.txt"
    assert result.size_bytes == len(content)
    assert result.content_hash == hashlib.sha256(content).hexdigest()
    assert result.media_type == "text/plain"
    assert result.storage_key != "secret.txt"
    assert resolve_storage_path(result.storage_key, root=tmp_path).read_bytes() == content
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "name", "mime", "code"), [
    (b"", "empty.txt", "text/plain", "empty_file"),
    (b"text", "fake.pdf", "application/pdf", "invalid_file"),
    (b"text", "fake.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "invalid_file"),
    (b"text", "file.exe", "application/octet-stream", "unsupported_file"),
    (b"text", "file.txt", "application/pdf", "unsupported_file"),
    (b"\xff\xfe\x00", "file.txt", "text/plain", "invalid_encoding"),
    (b"binary\x00text", "file.txt", "text/plain", "invalid_file"),
    (b"text", "bad\r\nname.txt", "text/plain", "invalid_filename"),
])
async def test_rejected_upload_leaves_no_file(
    tmp_path: Path, content: bytes, name: str, mime: str, code: str,
) -> None:
    with pytest.raises(KnowledgeBaseError) as error:
        await store_upload(upload(content, name, mime), root=tmp_path)
    assert error.value.code == code
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_actual_size_limit_cannot_be_bypassed_with_false_upload_size(tmp_path: Path) -> None:
    value = upload(b"a" * 20_000_001)
    value.size = 1
    with pytest.raises(KnowledgeBaseError) as error:
        await store_upload(value, root=tmp_path)
    assert error.value.status_code == 413
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_exact_twenty_megabytes_are_accepted(tmp_path: Path) -> None:
    result = await store_upload(upload(b"a" * 20_000_000), root=tmp_path)
    assert result.size_bytes == 20_000_000


@pytest.mark.parametrize("key", ["../secret.txt", "/etc/passwd", "a/secret.txt", "..\\secret.txt", "document.txt"])
def test_storage_key_rejects_untrusted_paths(tmp_path: Path, key: str) -> None:
    with pytest.raises(KnowledgeBaseError):
        resolve_storage_path(key, root=tmp_path)


@pytest.mark.asyncio
async def test_docx_rejects_macros_without_extracting_archive(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "types")
        archive.writestr("word/document.xml", "document")
        archive.writestr("word/vbaProject.bin", "macro")
    with pytest.raises(KnowledgeBaseError) as error:
        await store_upload(upload(buffer.getvalue(), "file.docx", "application/octet-stream"), root=tmp_path)
    assert error.value.code == "invalid_file"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_storage_creation_error_is_sanitized(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_bytes(b"existing")
    with pytest.raises(KnowledgeBaseError) as error:
        await store_upload(upload(b"hello"), root=occupied)
    assert error.value.code == "storage_unavailable"
    assert str(occupied) not in str(error.value)
    assert occupied.read_bytes() == b"existing"


@pytest.mark.asyncio
async def test_partial_read_error_cleans_only_own_temporary_file(tmp_path: Path) -> None:
    existing = tmp_path / "existing.txt"
    existing.write_bytes(b"keep")
    class BrokenStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            if self.tell() > 0:
                raise OSError("内部磁盘路径")
            return super().read(2)
    value = upload(b"unused")
    value.file = BrokenStream(b"hello")
    with pytest.raises(KnowledgeBaseError) as error:
        await store_upload(value, root=tmp_path)
    assert error.value.code == "storage_unavailable"
    assert list(tmp_path.iterdir()) == [existing]


@pytest.mark.asyncio
async def test_utf8_character_across_read_boundary_is_preserved(tmp_path: Path) -> None:
    content = b"a" * 65535 + "中文".encode()
    stored = await store_upload(upload(content), root=tmp_path)
    assert (tmp_path / stored.storage_key).read_bytes() == content


@pytest.mark.asyncio
async def test_docx_required_structure_is_accepted_and_excess_entries_rejected(tmp_path: Path) -> None:
    for count in (0, 1000):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
            for index in range(count):
                archive.writestr(f"extra/{index}", "")
        value = upload(buffer.getvalue(), "file.docx", "application/octet-stream")
        if count == 0:
            stored = await store_upload(value, root=tmp_path)
            assert stored.media_type.endswith("wordprocessingml.document")
        else:
            with pytest.raises(KnowledgeBaseError) as error:
                await store_upload(value, root=tmp_path)
            assert error.value.code == "invalid_file"
    assert len(list(tmp_path.iterdir())) == 1


def test_storage_symlink_cannot_escape_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep")
    root = tmp_path / "documents"
    root.mkdir()
    (root / ("a" * 32 + ".txt")).symlink_to(outside)
    with pytest.raises(KnowledgeBaseError):
        resolve_storage_path("a" * 32 + ".txt", root=root)
    assert outside.read_bytes() == b"keep"


@pytest.mark.asyncio
async def test_corrupt_docx_compressed_stream_is_a_safe_file_error(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    damaged = bytearray(buffer.getvalue())
    name_size, extra_size = struct.unpack_from("<HH", damaged, 26)
    damaged[30 + name_size + extra_size] = 0x07  # 无效的 DEFLATE 块类型。
    with pytest.raises(KnowledgeBaseError) as error:
        await store_upload(upload(bytes(damaged), "broken.docx", "application/octet-stream"), root=tmp_path)
    assert error.value.code == "invalid_file"
    assert list(tmp_path.iterdir()) == []

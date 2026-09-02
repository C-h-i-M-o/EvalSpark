import codecs
import hashlib
import os
import re
import tempfile
import zipfile
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request

from app.core.config import settings
from app.services.rag.errors import KnowledgeBaseError

MAX_FILE_BYTES = 20_000_000
MAX_MULTIPART_BYTES = MAX_FILE_BYTES + 65_536
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


class _UploadParser(MultiPartParser):
    complete = False

    def on_end(self) -> None:
        self.complete = True


@asynccontextmanager
async def read_multipart_upload(request: Request) -> AsyncIterator[UploadFile]:
    """鉴权之后解析单文件表单，按实际流量限制请求大小。"""
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "multipart/form-data":
        raise KnowledgeBaseError("invalid_multipart", "请使用 multipart/form-data 上传文件", 415)
    length = request.headers.get("content-length", "")
    if length.isdecimal() and int(length) > MAX_MULTIPART_BYTES:
        raise KnowledgeBaseError("file_too_large", "上传请求不能超过 20 MB 文件及表单开销上限", 413)

    async def bounded_stream() -> AsyncIterator[bytes]:
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_MULTIPART_BYTES:
                raise KnowledgeBaseError("file_too_large", "上传请求不能超过 20 MB 文件及表单开销上限", 413)
            yield chunk

    parser = _UploadParser(request.headers, bounded_stream(), max_files=1, max_fields=0)
    try:
        try:
            form = await parser.parse()
        except KnowledgeBaseError:
            raise
        except (MultiPartException, ValueError):
            raise KnowledgeBaseError("invalid_multipart", "表单必须包含且仅包含一个 file 文件", 422) from None
        value = form.get("file")
        if not parser.complete or len(form) != 1 or not isinstance(value, UploadFile):
            raise KnowledgeBaseError("invalid_multipart", "表单必须包含且仅包含一个完整的 file 文件", 422)
        yield value
    finally:
        # Starlette 的临时文件清单也包含尚未到达结束边界的文件，防止断流时残留句柄。
        for temporary in parser._files_to_close_on_error:
            temporary.close()


@dataclass(frozen=True)
class StoredDocument:
    storage_key: str
    original_name: str
    media_type: str
    size_bytes: int
    content_hash: str


def resolve_storage_path(key: str, *, root: Path = settings.rag_documents_dir) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}\.(pdf|docx|txt|md)", key) is None:
        raise KnowledgeBaseError("invalid_storage_key", "文档存储标识无效", 404)
    directory = root.resolve()
    path = (directory / key).resolve()
    if not path.is_relative_to(directory):
        raise KnowledgeBaseError("invalid_storage_key", "文档存储标识无效", 404)
    return path


def _validate_file(path: Path, extension: str) -> None:
    if extension in (".txt", ".md"):
        decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="strict")
        try:
            with path.open("rb") as stream:
                while data := stream.read(65_536):
                    decoder.decode(data)
                    if b"\x00" in data:
                        raise KnowledgeBaseError("invalid_file", "文本文件包含二进制内容", 415)
                decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            raise KnowledgeBaseError("invalid_encoding", "文本文件必须使用 UTF-8 编码", 415) from None
    elif extension == ".pdf":
        with path.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                raise KnowledgeBaseError("invalid_file", "PDF 文件格式无效", 415)
    else:
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if (
                    len(entries) > 1000 or len(set(names)) != len(names)
                    or sum(entry.file_size for entry in entries) > 100_000_000
                    or any(entry.flag_bits & 1 for entry in entries)
                    or any("vbaproject" in name.lower() for name in names)
                    or not {"[Content_Types].xml", "word/document.xml"}.issubset(names)
                    or archive.getinfo("[Content_Types].xml").file_size > 262_144
                ):
                    raise ValueError
                if b"macroenabled" in archive.read("[Content_Types].xml").lower():
                    raise ValueError
        except (ValueError, OSError, zipfile.BadZipFile, RuntimeError, zlib.error):
            raise KnowledgeBaseError("invalid_file", "DOCX 文件无效，或包含宏、加密及超限压缩内容", 415) from None


def _store_upload(upload: UploadFile, root: Path) -> StoredDocument:
    raw_name = upload.filename or ""
    name = raw_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or len(name) > 255 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise KnowledgeBaseError("invalid_filename", "文件名无效", 415)
    extension = Path(name).suffix.lower()
    media_type = MEDIA_TYPES.get(extension)
    accepted = {media_type, "application/octet-stream", ""}
    if extension == ".md":
        accepted.update({"text/plain", "text/x-markdown"})
    if media_type is None or (upload.content_type or "").split(";", 1)[0].lower() not in accepted:
        raise KnowledgeBaseError("unsupported_file", "仅支持 PDF、DOCX、TXT 和 Markdown 文件", 415)
    if upload.size is not None and upload.size > MAX_FILE_BYTES:
        raise KnowledgeBaseError("file_too_large", "单个文件不能超过 20 MB", 413)

    temporary: Path | None = None
    try:
        directory = root.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        size = 0
        digest = hashlib.sha256()
        # 临时文件和最终文件处于同一受控目录，保证原子改名。
        with tempfile.NamedTemporaryFile(dir=directory, prefix="upload-", delete=False) as output:
            temporary = Path(output.name)
            upload.file.seek(0)
            while data := upload.file.read(65_536):
                size += len(data)
                if size > MAX_FILE_BYTES:
                    raise KnowledgeBaseError("file_too_large", "单个文件不能超过 20 MB", 413)
                digest.update(data)
                output.write(data)
        if size == 0:
            raise KnowledgeBaseError("empty_file", "不能上传空文件", 415)
        _validate_file(temporary, extension)
        key = f"{uuid4().hex}{extension}"
        os.replace(temporary, resolve_storage_path(key, root=directory))
        return StoredDocument(key, name, media_type, size, digest.hexdigest())
    except OSError:
        raise KnowledgeBaseError("storage_unavailable", "文档存储暂时不可用", 503) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise KnowledgeBaseError("storage_unavailable", "临时文档清理失败，请检查存储服务", 503) from None


async def store_upload(upload: UploadFile, *, root: Path = settings.rag_documents_dir) -> StoredDocument:
    # 文件复制和轻量校验放在线程池，API 事件循环不做同步磁盘工作。
    return await run_in_threadpool(_store_upload, upload, root)

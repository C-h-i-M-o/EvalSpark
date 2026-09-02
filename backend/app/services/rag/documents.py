from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
import zlib
from array import array
from bisect import bisect_left, bisect_right
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import TypeAdapter
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request

from app.core.config import settings
from app.schemas.knowledge_base import DocxSource, PdfSource, SourceLocation, TextSource
from app.services.rag.errors import KnowledgeBaseError

if TYPE_CHECKING:
    from tokenizers import Tokenizer

MAX_FILE_BYTES = 20_000_000
MAX_MULTIPART_BYTES = MAX_FILE_BYTES + 65_536
MAX_EXTRACTED_CHARACTERS = 20_000_000
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


@dataclass(frozen=True)
class SourceBlock:
    text: str
    source: SourceLocation
    markdown: bool = False
    heading: bool = False


@dataclass(frozen=True)
class DocumentChunk:
    text: str
    token_count: int
    source: SourceLocation


def _parse_in_subprocess(path: Path, media_type: str) -> list[SourceBlock]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.services.rag.parsing", str(path), media_type],
            capture_output=True, timeout=120, check=False,
        )
    except subprocess.TimeoutExpired:
        raise KnowledgeBaseError("parse_timeout", "文档解析超时，请拆分文档", 415) from None
    except OSError:
        raise KnowledgeBaseError("parser_unavailable", "文档解析进程暂时不可用", 503) from None
    if result.returncode != 0:
        raise KnowledgeBaseError("document_too_complex", "文档解析进程超过资源限制或异常退出", 415)
    try:
        payload = json.loads(result.stdout)
        if "error" in payload:
            raise KnowledgeBaseError(payload["error"], payload["message"], payload["status"])
        source_adapter = TypeAdapter(SourceLocation)
        return [SourceBlock(
            item["text"], source_adapter.validate_python(item["source"]), item["markdown"], item["heading"],
        ) for item in payload["blocks"]]
    except KnowledgeBaseError:
        raise
    except (ValueError, KeyError, TypeError):
        raise KnowledgeBaseError("parser_invalid_response", "文档解析结果无效", 415) from None


async def parse_in_subprocess(path: Path, media_type: str) -> list[SourceBlock]:
    # 子进程限制复杂文档的资源消耗，等待工作交给线程池，事件循环可继续续租。
    return await run_in_threadpool(_parse_in_subprocess, path, media_type)


def parse_document(path: Path, media_type: str) -> list[SourceBlock]:
    """仅负责提取；实际 Worker 在受资源限制的子进程调用本函数。"""
    extension = next((key for key, value in MEDIA_TYPES.items() if value == media_type), None)
    if extension is None:
        raise KnowledgeBaseError("unsupported_file", "文档类型不受支持", 415)
    blocks: list[SourceBlock] = []
    total = 0

    def append(block: SourceBlock) -> None:
        nonlocal total
        total += len(block.text)
        if total > MAX_EXTRACTED_CHARACTERS:
            raise KnowledgeBaseError("document_too_complex", "提取文本超过处理上限，请拆分文档", 415)
        if block.text.strip():
            blocks.append(block)

    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise KnowledgeBaseError("file_too_large", "单个文件不能超过 20 MB", 413)
        _validate_file(path, extension)
        if extension in (".txt", ".md"):
            text = path.read_text(encoding="utf-8-sig")
            line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
            append(SourceBlock(text, TextSource(line_start=1, line_end=max(1, line_count)), markdown=extension == ".md"))
        elif extension == ".pdf":
            from pypdf import PdfReader
            with path.open("rb") as stream:
                reader = PdfReader(stream, strict=True)
                if reader.is_encrypted:
                    raise KnowledgeBaseError("encrypted_document", "暂不支持加密文档，请上传未加密版本", 415)
                for index, page in enumerate(reader.pages, 1):
                    append(SourceBlock((page.extract_text() or "").strip(), PdfSource(page_start=index, page_end=index)))
        else:
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph

            def table_text(table: Table, depth: int = 0) -> str:
                if depth > 32:
                    raise KnowledgeBaseError("document_too_complex", "表格嵌套过深，请简化文档", 415)
                rows = []
                seen = set()
                for row in table.rows:
                    cells = []
                    for cell in row.cells:
                        # python-docx 会把合并单元格映射为重复视图，只提取一次。
                        if cell._tc in seen:
                            continue
                        seen.add(cell._tc)
                        cells.append("\n".join(
                            item.text if isinstance(item, Paragraph) else table_text(item, depth + 1)
                            for item in cell.iter_inner_content()
                        ))
                    rows.append("\t".join(cells))
                return "\n".join(rows)

            document = Document(path)
            for index, item in enumerate(document.iter_inner_content(), 1):
                is_paragraph = isinstance(item, Paragraph)
                append(SourceBlock(
                    item.text if is_paragraph else table_text(item),
                    DocxSource(block_start=index, block_end=index, block_type="paragraph" if is_paragraph else "table"),
                    heading=is_paragraph and item.style is not None and item.style.name.startswith("Heading"),
                ))
    except KnowledgeBaseError:
        raise
    except MemoryError:
        raise KnowledgeBaseError("document_too_complex", "文档解析超出内存上限，请拆分文档", 415) from None
    except Exception:
        raise KnowledgeBaseError("invalid_file", "文档损坏或无法解析，请检查文件格式", 415) from None
    if not blocks:
        if extension == ".pdf":
            raise KnowledgeBaseError("pdf_no_text", "暂不支持扫描件，请上传可提取文本的 PDF", 415)
        raise KnowledgeBaseError("document_no_text", "文档中没有可提取的文本", 415)
    return blocks


def split_blocks(blocks: list[SourceBlock], tokenizer: Tokenizer, chunk_size: int, overlap: int) -> list[DocumentChunk]:
    from semantic_text_splitter import MarkdownSplitter, TextSplitter

    if type(chunk_size) is not int or type(overlap) is not int or not 128 <= chunk_size <= 2048 or not 0 <= overlap < chunk_size:
        raise KnowledgeBaseError("invalid_chunking", "切块大小或重叠 Token 数无效", 422)
    if not blocks or len({block.source.kind for block in blocks}) != 1:
        raise KnowledgeBaseError("invalid_source", "文档来源为空或类型不一致", 415)
    tokenizer.no_truncation()
    tokenizer.no_padding()
    parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    line_breaks: list[array] = []
    position = 0
    for block in blocks:
        if parts:
            separator = "\n\n\n" if block.heading else "\n\n"
            parts.append(separator)
            position += len(separator)
        starts.append(position)
        parts.append(block.text)
        position += len(block.text)
        ends.append(position)
        # 紧凑索引避免大量短行创建大量 Python int/源块对象。
        line_breaks.append(array("I", (match.start() for match in re.finditer("\n", block.text))) if isinstance(block.source, TextSource) else array("I"))
    text = "".join(parts)
    splitter_type = MarkdownSplitter if blocks[0].markdown else TextSplitter
    # 开源切分器仅计算正文 Token；Qwen 在推理输入末尾另加 EOS，需预留相同开销。
    capacity = chunk_size - tokenizer.num_special_tokens_to_add(False)
    splitter = splitter_type.from_huggingface_tokenizer(tokenizer, capacity, overlap=min(overlap, capacity - 1), trim=True)
    chunks: list[DocumentChunk] = []
    covered_until = 0
    for start, content in splitter.chunk_indices(text):
        end = start + len(content)
        if text[start:end] != content or end <= covered_until or text[covered_until:start].strip():
            raise KnowledgeBaseError("invalid_chunk_location", "切块来源覆盖校验失败", 415)
        covered_until = end
        token_count = len(tokenizer.encode(content, add_special_tokens=True).ids)
        if not 0 < token_count <= chunk_size:
            raise KnowledgeBaseError("chunk_too_large", "切块 Token 复核失败，不能截断后索引", 415)
        first = bisect_right(ends, start)
        last = bisect_left(starts, end) - 1
        left, right = blocks[first].source, blocks[last].source
        if isinstance(left, TextSource) and isinstance(right, TextSource):
            source: SourceLocation = TextSource(
                line_start=left.line_start + bisect_left(line_breaks[first], start - starts[first]),
                line_end=right.line_start + bisect_left(line_breaks[last], end - 1 - starts[last]),
            )
        elif isinstance(left, PdfSource) and isinstance(right, PdfSource):
            source = PdfSource(page_start=left.page_start, page_end=right.page_end)
        elif isinstance(left, DocxSource) and isinstance(right, DocxSource):
            kinds = {item.source.block_type for item in blocks[first:last + 1] if isinstance(item.source, DocxSource)}
            source = DocxSource(block_start=left.block_start, block_end=right.block_end, block_type=left.block_type if len(kinds) == 1 else "mixed")
        else:
            raise KnowledgeBaseError("invalid_source", "来源类型无法匹配", 415)
        chunks.append(DocumentChunk(content, token_count, source))
        if len(chunks) > 100_000:
            raise KnowledgeBaseError("chunk_limit", "切块数量超过知识库上限", 415)
    if not chunks or text[covered_until:].strip():
        raise KnowledgeBaseError("invalid_chunk_location", "文档内容未被完整切分", 415)
    return chunks

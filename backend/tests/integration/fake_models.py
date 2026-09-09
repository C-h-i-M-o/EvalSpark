"""只在隔离验收容器运行的确定性模型 HTTP 服务，不连接任何上游。"""
import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StrictBool

app = FastAPI(title="RAG 隔离验收假模型")


class EmbeddingRequest(BaseModel):
    model: Literal["embedding-test"]
    input: list[str] = Field(min_length=1, max_length=16)
    encoding_format: Literal["float"]


@app.post("/v1/embeddings")
async def embed(payload: EmbeddingRequest, authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
    if authorization != "Bearer rag-acceptance-only":
        raise HTTPException(401, "只接受隔离验收凭据")
    # 确定性非零向量用于验证协议、存储与归属；不代表真实检索语义质量。
    return {"data": [{"index": index, "embedding": [1.0, 0.5, 0.25]} for index in reversed(range(len(payload.input)))],
        "usage": {"prompt_tokens": 5 * len(payload.input), "total_tokens": 5 * len(payload.input)}}


class Message(BaseModel):
    role: Literal["system", "user"]
    content: str = Field(min_length=1)


class CompletionRequest(BaseModel):
    model: Literal["candidate-a", "candidate-b", "candidate-fail", "judge"]
    messages: list[Message] = Field(min_length=2, max_length=2)
    stream: StrictBool = False


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions", response_model=None)
async def complete(payload: CompletionRequest, authorization: Annotated[str | None, Header()] = None) -> dict[str, object] | StreamingResponse:
    if authorization != "Bearer rag-acceptance-only":
        raise HTTPException(401, "只接受隔离验收凭据")
    if [message.role for message in payload.messages] != ["system", "user"]:
        raise HTTPException(422, "必须保留独立的系统指令与用户数据")
    if payload.model == "candidate-fail":
        raise HTTPException(503, "合成候选故障")
    if payload.model == "judge" or payload.stream:
        try:
            data = json.loads(payload.messages[1].content)
            if not isinstance(data["question"], str) or not data["evidence"] or data["evidence"][0]["label"] != "S1":
                raise ValueError("缺少问题或本回答证据")
            if payload.model == "judge" and "[S1]" not in data["answer"]:
                raise ValueError("评审缺少候选引用")
        except (ValueError, TypeError, KeyError, IndexError) as error:
            raise HTTPException(422, "验收请求缺少结构化问题、回答或证据") from error
    if payload.model == "judge":
        content = json.dumps({"answerQuality": 8, "faithfulness": 9, "citationCorrectness": 8,
            "citationCompleteness": 7, "claims": [{"claim": "报销申请需要发票", "evidenceLabels": ["S1"],
                "invalidCitationLabels": [], "supported": True, "needsCitation": True,
                "citationSupported": True, "reason": "合成报销指南支持该断言"}]}, ensure_ascii=False)
        usage = {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}
    else:
        content = "报销发票申请材料" if payload.model == "candidate-a" else "主管审批电子凭证"
        usage = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    if not payload.stream:
        return {"id": "rag-test", "object": "chat.completion", "model": payload.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": usage}

    async def events() -> AsyncIterator[str]:
        for part in ("报销申请需要发票", "和主管审批。[S1]"):
            yield "data: " + json.dumps({"id": "rag-test", "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]}, ensure_ascii=False) + "\n\n"
        yield "data: " + json.dumps({"id": "rag-test", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")

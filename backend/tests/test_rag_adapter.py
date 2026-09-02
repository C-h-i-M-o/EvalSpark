import json

import httpx
import pytest

from app.adapters.base import ModelRequest
from app.adapters.openai_compatible import OpenAICompatibleClient


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("with_usage", [False, True])
async def test_rag_uses_system_role_and_marks_missing_usage(monkeypatch, stream: bool, with_usage: bool) -> None:
    captured: list[dict[str, object]] = []
    def handle(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        usage = {"prompt_tokens": 4, "completion_tokens": 2} if with_usage else {}
        if stream:
            data = {"choices": [{"delta": {"content": "回答"}}], "usage": usage}
            return httpx.Response(200, text=f"data: {json.dumps(data)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": "回答"}}], "usage": usage})
    factory = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(handle), **kwargs))
    client = OpenAICompatibleClient("测试", "http://invalid", "test-only")
    request = ModelRequest("不可信的资料", "测试", system_prompt="只依据资料回答")
    if stream:
        events = [event async for event in client.stream_chat(request)]
        reply = events[-1].reply
    else:
        reply = await client.chat(request)
    assert captured[0]["messages"] == [
        {"role": "system", "content": "只依据资料回答"}, {"role": "user", "content": "不可信的资料"}]
    assert reply.usage_known is with_usage

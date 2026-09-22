from decimal import Decimal

import httpx
import pytest

from app.adapters.base import ChatMessage, ModelRequest
from app.adapters.openai_compatible import OpenAICompatibleClient


@pytest.mark.asyncio
async def test_adapter_sends_explicit_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式角色消息优先于旧 prompt，且不发出真实网络请求。"""
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录请求并返回确定性模型回答。"""
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "好"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}})

    original = httpx.AsyncClient

    class FakeClient:
        def __init__(self, **_: object) -> None:
            """使用替换前的客户端避免测试桩递归调用自己。"""
            self.client = original(transport=httpx.MockTransport(handler))

        async def __aenter__(self) -> "FakeClient":
            """打开内存传输客户端。"""
            await self.client.__aenter__()
            return self

        async def __aexit__(self, *args: object) -> None:
            """关闭内存传输客户端。"""
            await self.client.__aexit__(*args)

        async def post(self, *args: object, **kwargs: object) -> httpx.Response:
            """将请求转交确定性传输器。"""
            return await self.client.post(*args, **kwargs)

    monkeypatch.setattr("app.adapters.openai_compatible.httpx.AsyncClient", FakeClient)
    client = OpenAICompatibleClient("m", "https://example.test", "key", extra_body={"messages": [{"role": "system", "content": "越权"}]})
    await client.chat(ModelRequest("旧 prompt", "m", messages=(ChatMessage("system", "系统"), ChatMessage("user", "新问题"))))
    assert captured["messages"] == [{"role": "system", "content": "系统"}, {"role": "user", "content": "新问题"}]
    monkeypatch.setattr("app.adapters.openai_compatible.httpx.AsyncClient", original)

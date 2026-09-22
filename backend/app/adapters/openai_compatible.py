import json
from collections.abc import AsyncIterator
from decimal import Decimal
from time import perf_counter

import httpx

from app.adapters.base import ModelClient, ModelCostDetails, ModelReply, ModelRequest, ModelStreamEvent, ModelUsage

TOKENS_PER_MILLION = Decimal("1000000")


class OpenAICompatibleClient(ModelClient):
    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        input_price: Decimal = Decimal("0"),
        output_price: Decimal = Decimal("0"),
        cache_hit_price: Decimal = Decimal("0"),
        cache_creation_price: Decimal = Decimal("0"),
        timeout: float = 60,
        extra_body: dict[str, object] | None = None,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.input_price = input_price
        self.output_price = output_price
        self.cache_hit_price = cache_hit_price
        self.cache_creation_price = cache_creation_price
        self.timeout = timeout
        self.extra_body = extra_body or {}

    async def chat(self, request: ModelRequest) -> ModelReply:
        if not self.api_key:
            raise ValueError(f"{self.model_name} 未配置 API Key")
        if not self.base_url:
            raise ValueError(f"{self.model_name} 未配置 Base URL")

        started_at = perf_counter()
        payload = {
            "model": request.model_name,
            "messages": self._request_messages(request),
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False,
        }
        payload.update(self.extra_body)
        payload.update(request.extra_body)
        if request.messages or request.system_prompt:
            # 结构化对话和系统规则不能被供应商扩展参数覆盖。
            payload["messages"] = self._request_messages(request)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        message = data["choices"][0]["message"]
        answer = message.get("content") or ""
        reasoning_content = (message.get("reasoning_content") or "").strip()
        if reasoning_content:
            answer = f"<think>\n{reasoning_content}\n</think>\n\n{answer}".strip()
        usage = self.normalize_usage(
            data.get("usage", {}),
            prompt_fallback=len(request.prompt),
            answer_fallback=len(answer),
        )
        latency_ms = int((perf_counter() - started_at) * 1000)
        return ModelReply(answer=answer, usage=usage, latency_ms=latency_ms,
                          usage_known=self._usage_known(data.get("usage")))

    async def stream_chat(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        if not self.api_key:
            raise ValueError(f"{self.model_name} 未配置 API Key")
        if not self.base_url:
            raise ValueError(f"{self.model_name} 未配置 Base URL")

        started_at = perf_counter()
        payload = {
            "model": request.model_name,
            "messages": self._request_messages(request),
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": True,
        }
        payload.update(self.extra_body)
        payload.update(request.extra_body)
        if request.messages or request.system_prompt:
            # 流式与非流式请求采用相同的角色边界。
            payload["messages"] = self._request_messages(request)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        answer_parts: list[str] = []
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        usage_data: dict[str, object] = {}
        reasoning_open = False
        content_started = False

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    chunk = self._parse_sse_line(line)
                    if chunk is None:
                        continue
                    if chunk == "[DONE]":
                        break

                    data = json.loads(chunk)
                    if isinstance(data.get("usage"), dict):
                        usage_data = data["usage"]
                    delta = self._extract_stream_delta(data)
                    reasoning_delta = delta.get("reasoning_content")
                    content_delta = delta.get("content")

                    if reasoning_delta:
                        reasoning_text = str(reasoning_delta)
                        reasoning_parts.append(reasoning_text)
                        if not reasoning_open:
                            output = f"<think>\n{reasoning_text}"
                            reasoning_open = True
                        else:
                            output = reasoning_text
                        answer_parts.append(output)
                        yield ModelStreamEvent(delta=output)

                    if content_delta:
                        content_text = str(content_delta)
                        content_parts.append(content_text)
                        output = content_text
                        if reasoning_open and not content_started:
                            output = f"\n</think>\n\n{content_text}"
                        content_started = True
                        answer_parts.append(output)
                        yield ModelStreamEvent(delta=output)

        if reasoning_open and not content_started:
            closing = "\n</think>"
            answer_parts.append(closing)
            yield ModelStreamEvent(delta=closing)

        answer = "".join(answer_parts).strip()
        if not answer and (reasoning_parts or content_parts):
            reasoning_content = "".join(reasoning_parts).strip()
            content = "".join(content_parts)
            answer = f"<think>\n{reasoning_content}\n</think>\n\n{content}".strip() if reasoning_content else content
        usage = self.normalize_usage(
            usage_data,
            prompt_fallback=len(request.prompt),
            answer_fallback=len(answer),
        )
        latency_ms = int((perf_counter() - started_at) * 1000)
        yield ModelStreamEvent(delta="", reply=ModelReply(answer=answer, usage=usage, latency_ms=latency_ms,
                                                         usage_known=self._usage_known(usage_data)))

    @staticmethod
    def _request_messages(request: ModelRequest) -> list[dict[str, str]]:
        """优先发送结构化消息，旧请求继续使用 system_prompt/prompt。"""
        if request.messages:
            return [{"role": message.role, "content": message.content} for message in request.messages]
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        return messages

    @staticmethod
    def _usage_known(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        prompt = value.get("prompt_tokens", value.get("input_tokens"))
        output = value.get("completion_tokens", value.get("output_tokens"))
        return type(prompt) is int and prompt >= 0 and type(output) is int and output >= 0

    def get_model_name(self) -> str:
        return self.model_name

    def estimate_cost(self, usage: ModelUsage) -> Decimal:
        return self.estimate_cost_details(usage).total_cost

    def estimate_cost_details(self, usage: ModelUsage) -> ModelCostDetails:
        return ModelCostDetails(
            input_cost=Decimal(usage.input_tokens) * self.input_price / TOKENS_PER_MILLION,
            output_cost=Decimal(usage.output_tokens) * self.output_price / TOKENS_PER_MILLION,
            cache_hit_cost=Decimal(usage.cache_hit_tokens) * self.cache_hit_price / TOKENS_PER_MILLION,
            cache_creation_cost=(
                Decimal(usage.cache_creation_tokens) * self.cache_creation_price / TOKENS_PER_MILLION
            ),
        )

    @staticmethod
    def _parse_sse_line(line: str) -> str | None:
        stripped_line = line.strip()
        if not stripped_line or not stripped_line.startswith("data:"):
            return None
        return stripped_line.removeprefix("data:").strip()

    @staticmethod
    def _extract_stream_delta(data: object) -> dict[str, object]:
        if not isinstance(data, dict):
            return {}
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return {}
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return {}
        delta = first_choice.get("delta")
        return delta if isinstance(delta, dict) else {}

    @staticmethod
    def normalize_usage(
        usage_data: dict[str, object],
        prompt_fallback: int,
        answer_fallback: int,
    ) -> ModelUsage:
        prompt_details = usage_data.get("prompt_tokens_details")
        details = prompt_details if isinstance(prompt_details, dict) else {}
        cache_hit_tokens = int(
            details.get("cached_tokens")
            or usage_data.get("cache_read_input_tokens")
            or usage_data.get("prompt_cache_hit_tokens")
            or 0
        )
        cache_creation_tokens = int(
            details.get("cache_creation_tokens")
            or usage_data.get("cache_creation_input_tokens")
            or usage_data.get("prompt_cache_creation_tokens")
            or 0
        )
        prompt_tokens = int(usage_data.get("prompt_tokens") or usage_data.get("input_tokens") or 0)
        output_tokens = int(usage_data.get("completion_tokens") or usage_data.get("output_tokens") or 0)
        input_tokens = max(prompt_tokens - cache_hit_tokens - cache_creation_tokens, 0)
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

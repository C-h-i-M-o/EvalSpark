from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class ModelRequest:
    prompt: str
    model_name: str
    max_tokens: int = 1024
    temperature: float = 0.7
    extra_body: dict[str, object] = field(default_factory=dict)
    system_prompt: str = ""


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_hit_tokens + self.cache_creation_tokens


@dataclass(frozen=True)
class ModelCostDetails:
    input_cost: Decimal
    output_cost: Decimal
    cache_hit_cost: Decimal
    cache_creation_cost: Decimal

    @property
    def total_cost(self) -> Decimal:
        return self.input_cost + self.output_cost + self.cache_hit_cost + self.cache_creation_cost


@dataclass(frozen=True)
class ModelReply:
    answer: str
    usage: ModelUsage
    latency_ms: int
    # 兼容旧适配器；明确缺少上游用量时由适配器设为 False。
    usage_known: bool = True


@dataclass(frozen=True)
class ModelStreamEvent:
    delta: str
    reply: ModelReply | None = None


class ModelClient(ABC):
    @abstractmethod
    async def chat(self, request: ModelRequest) -> ModelReply:
        raise NotImplementedError

    @abstractmethod
    def get_model_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def estimate_cost(self, usage: ModelUsage) -> Decimal:
        raise NotImplementedError

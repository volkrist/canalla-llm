from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

ArgsT = TypeVar("ArgsT", bound=BaseModel)


class RiskLevel(StrEnum):
    READ = "READ"
    NORMAL_CHANGE = "NORMAL_CHANGE"
    SENSITIVE = "SENSITIVE"
    CRITICAL = "CRITICAL"


class ToolError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CredentialReference:
    provider: str
    reference_id: str


class ToolCredentialProvider(ABC):
    @abstractmethod
    def configured(self, provider: str) -> bool: ...

    @abstractmethod
    def resolve(self, provider: str) -> str: ...


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    capability: str
    risk_level: RiskLevel
    cost_class: str
    timeout: float
    provider: str
    auto_route: bool = True

    @property
    def input_schema(self):
        return self.input_model.model_json_schema()

    def llm_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass
class ToolResult:
    text: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cost_actual: float | None = None
    cost_estimate: float | None = None
    provider_run_id: str | None = None
    metadata: dict = field(default_factory=dict)


class ToolProvider(ABC, Generic[ArgsT]):
    @abstractmethod
    async def execute(self, args: ArgsT, context) -> ToolResult: ...


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, tuple[ToolDefinition, ToolProvider[Any]]] = {}

    def register(self, definition: ToolDefinition, provider: ToolProvider[Any]):
        if definition.name in self._tools:
            raise ValueError("Duplicate tool")
        self._tools[definition.name] = (definition, provider)

    def get(self, name: str):
        if name not in self._tools:
            raise ToolError("unknown_tool")
        return self._tools[name]

    def definitions(self, auto_only=True):
        return [d for d, _ in self._tools.values() if not auto_only or d.auto_route]

    async def close(self):
        for _, provider in self._tools.values():
            # Only the browser providers expose a session teardown; typing it as a protocol
            # would force every provider to grow the attribute.
            close_all = getattr(provider, "close_all", None)
            if close_all is not None:
                await close_all()

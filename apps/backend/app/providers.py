import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx

from .config import Settings


class LLMProvider(ABC):
    @abstractmethod
    def stream_chat(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...

    async def chat(self, messages: list[dict[str, str]]) -> str:
        return "".join([part async for part in self.stream_chat(messages)])

    @abstractmethod
    async def health(self) -> bool: ...


class MockLLMProvider(LLMProvider):
    def __init__(self, delay: float = 0.035):
        self.delay = delay

    async def health(self):
        return True

    async def stream_chat(self, messages):
        prompt = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "Hello")
        response = (
            "Это **Alex LLM**, демонстрационный ответ mock-провайдера.\n\n"
            f"Вы написали: {prompt}\n\n"
            "Авторизация, история и потоковая передача уже работают без GPU. "
            "Этот ответ генерируется локальным шаблоном, а не языковой моделью.\n\n"
            "Пример кода:\n\n```python\ndef greet(name: str) -> str:\n"
            '    return f"Hello, {name}!"\n\nprint(greet("Alex LLM"))\n```\n'
        )
        for start in range(0, len(response), 7):
            await asyncio.sleep(self.delay)
            yield response[start : start + 7]


class LlamaCppProvider(LLMProvider):
    """Backend-only adapter. base_url points to the server root or its /v1 path."""

    def __init__(self, settings: Settings):
        self.base = settings.llm_base_url.rstrip("/")
        self.api = self.base if self.base.endswith("/v1") else self.base + "/v1"
        self.model = settings.llm_model
        self.headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}

    async def health(self):
        try:
            async with httpx.AsyncClient(timeout=5, headers=self.headers) as client:
                response = await client.get(self.api + "/models")
                return response.is_success
        except httpx.HTTPError:
            return False

    async def stream_chat(self, messages):
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), headers=self.headers) as client:
            async with client.stream(
                "POST",
                self.api + "/chat/completions",
                json={"model": self.model, "messages": messages, "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    event = json.loads(data)
                    if "error" in event:
                        raise RuntimeError("LLM provider returned an error")
                    choices = event.get("choices", [])
                    if choices:
                        content = choices[0].get("delta", {}).get("content")
                        if content:
                            yield content
                raise RuntimeError("LLM stream ended before its completion marker")


def make_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockLLMProvider(settings.mock_delay)
    return LlamaCppProvider(settings)

import asyncio
import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx

from .config import Settings
from .tools.web_router import classify_web


class LLMProvider(ABC):
    supports_tools = False

    async def plan_tools(self, messages, tools, usage):
        raise LLMError("tools_unsupported")

    @abstractmethod
    def stream_chat(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...

    async def chat(self, messages: list[dict[str, str]]) -> str:
        return "".join([part async for part in self.stream_chat(messages)])

    async def stream_with_usage(self, messages, usage):
        iterator = self.stream_chat(messages)
        try:
            async for part in iterator:
                yield part
        finally:
            await iterator.aclose()

    @abstractmethod
    async def health(self) -> bool: ...


class MockLLMProvider(LLMProvider):
    supports_tools = True

    async def plan_tools(self, messages, tools, usage):
        # Deterministic mock-only planner. Production decisions come from llama.cpp.
        prompt = next((m.get("content", "") for m in reversed(messages) if m["role"] == "user"), "")
        previous = [m for m in messages if m["role"] == "tool"]
        if previous:
            last = json.loads(previous[-1]["content"])
            if len(previous) == 1 and last.get("sources") and not last.get("error"):
                return {
                    "tool_calls": [
                        {
                            "id": "mock-fetch",
                            "type": "function",
                            "function": {
                                "name": "web_fetch",
                                "arguments": json.dumps(
                                    {"urls": [last["sources"][0]["final_url"]], "fresh": True}
                                ),
                            },
                        }
                    ]
                }
            return {"tool_calls": []}
        policy = str(messages[0].get("content", ""))
        mode = "auto"
        if "Web mode=on" in policy:
            mode = "on"
        elif "Web mode=off" in policy:
            mode = "off"
        intent = classify_web(prompt, mode)
        if not intent.required:
            return {"tool_calls": []}
        urls = re.findall(r"https?://[^\s<>]+", prompt)
        name, args = (
            ("web_fetch", {"urls": urls[:1], "fresh": intent.fresh})
            if urls
            else (
                "web_search",
                {"query": intent.query},
            )
        )
        return {
            "tool_calls": [
                {
                    "id": "mock-web",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                }
            ]
        }

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
        if any(m["content"].startswith("[Untrusted reference material — documents:") for m in messages):
            response += "\nВ запрос передан контекст документов. Это проверка доставки контекста; mock не делает выводы по источникам.\n"
        for message in messages:
            if message.get("content", "").startswith("[Untrusted reference material — web/tools]"):
                labels = sorted(set(re.findall(r"\[(?:W|T)\d+\]", message["content"])))
                response += "\nWeb-контекст передан mock-провайдеру: " + " ".join(labels) + ".\n"
        for start in range(0, len(response), 7):
            await asyncio.sleep(self.delay)
            yield response[start : start + 7]


class LlamaCppProvider(LLMProvider):
    """Backend-only adapter. base_url points to the server root or its /v1 path."""

    supports_tools = True

    def _chat_payload(self, messages, **extra):
        payload = {
            "model": self.model,
            "messages": messages,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        payload.update(extra)
        return payload

    async def plan_tools(self, messages, tools, usage):
        try:
            async with self.client(httpx.Timeout(90, connect=10)) as client:
                response = await client.post(
                    self.endpoint() + "/chat/completions",
                    json=self._chat_payload(
                        messages,
                        stream=False,
                        tools=tools,
                        tool_choice="auto",
                        max_tokens=1200,
                    ),
                )
                if not response.is_success:
                    raise LLMError("tools_unsupported" if response.status_code == 400 else "llm_unavailable")
                if len(response.content) > 256000:
                    raise LLMError("malformed_response")
                value = response.json()
                message = value["choices"][0]["message"]
                if not isinstance(message, dict) or not isinstance(message.get("tool_calls", []), list):
                    raise LLMError("malformed_response")
                usage["_planner_calls"] = usage.get("_planner_calls", 0) + 1
                reported = value.get("usage") or {}
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    count = reported.get(key)
                    if isinstance(count, int) and count >= 0:
                        usage[key] = usage.get(key, 0) + count
                    else:
                        usage["_planner_usage_incomplete"] = True
                return message
        except httpx.TimeoutException:
            raise LLMError("llm_timeout") from None
        except httpx.HTTPError:
            raise LLMError("llm_unavailable") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("malformed_response") from None

    def __init__(self, settings: Settings, target=None, transport=None):
        self.base = settings.llm_base_url.rstrip("/")
        self.api = self.base if self.base.endswith("/v1") else self.base + "/v1"
        self.model = settings.llm_model
        self.headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        self.target = target
        self.transport = transport

    def endpoint(self):
        base = self.target() if self.target else self.base
        if not base:
            raise LLMError("offline")
        return base.rstrip("/") if base.rstrip("/").endswith("/v1") else base.rstrip("/") + "/v1"

    def client(self, timeout):
        kwargs = {"timeout": timeout, "headers": self.headers, "follow_redirects": False}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    async def status(self):
        try:
            endpoint = self.endpoint()
            async with self.client(5) as client:
                response = await client.get(endpoint + "/models")
                if response.status_code == 503:
                    return "loading_model"
                if response.status_code in (401, 403):
                    return "connection_auth_failed"
                if not response.is_success:
                    return "offline"
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                    return "malformed_response"
                if not any(isinstance(row, dict) and row.get("id") == self.model for row in data["data"]):
                    return "model_mismatch"
                return "ready"
        except (httpx.HTTPError, LLMError):
            return "offline"
        except (ValueError, TypeError):
            return "malformed_response"

    async def health(self):
        return await self.status() == "ready"

    async def stream_chat(self, messages):
        iterator = self.stream_with_usage(messages, {})
        try:
            async for part in iterator:
                yield part
        finally:
            await iterator.aclose()

    async def stream_with_usage(self, messages, usage):
        iterator = self._stream(messages, usage)
        try:
            async for part in iterator:
                yield part
        except httpx.TimeoutException:
            raise LLMError("llm_timeout") from None
        except httpx.HTTPError:
            raise LLMError("stream_interrupted") from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise LLMError("malformed_response") from None
        finally:
            await iterator.aclose()

    async def _stream(self, messages, usage):
        endpoint = self.endpoint()
        async with self.client(httpx.Timeout(120, connect=10)) as client:
            async with client.stream(
                "POST",
                endpoint + "/chat/completions",
                json=self._chat_payload(
                    messages,
                    stream=True,
                    stream_options={"include_usage": True},
                ),
            ) as response:
                if response.status_code == 503:
                    raise LLMError("loading_model")
                if response.status_code in (401, 403):
                    raise LLMError("connection_auth_failed")
                if not response.is_success:
                    raise LLMError("llm_unavailable")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    event = json.loads(data)
                    if not isinstance(event, dict):
                        raise LLMError("malformed_response")
                    if "error" in event:
                        raise LLMError("llm_unavailable")
                    reported = event.get("usage")
                    if isinstance(reported, dict):
                        for upstream, column in (
                            ("prompt_tokens", "input_tokens"),
                            ("completion_tokens", "output_tokens"),
                            ("total_tokens", "total_tokens"),
                        ):
                            value = reported.get(upstream)
                            if (
                                isinstance(value, int)
                                and not isinstance(value, bool)
                                and 0 <= value <= 2**31 - 1
                            ):
                                usage[column] = value
                    choices = event.get("choices", [])
                    if not isinstance(choices, list):
                        raise LLMError("malformed_response")
                    if choices:
                        content = choices[0].get("delta", {}).get("content")
                        if content:
                            if not isinstance(content, str):
                                raise LLMError("malformed_response")
                            yield content
                raise LLMError("stream_interrupted")


class LLMError(RuntimeError):
    def __init__(self, code):
        self.code = code
        messages = {
            "offline": "AI выключен или недоступен.",
            "loading_model": "Модель загружается. Дождитесь AI Ready.",
            "connection_auth_failed": "Не удалось авторизовать защищённое подключение AI.",
            "malformed_response": "AI вернул неподдерживаемый ответ.",
            "llm_timeout": "AI не ответил вовремя.",
            "stream_interrupted": "Поток AI прерван до completion marker. Полученный текст сохранён.",
            "llm_unavailable": "AI временно недоступен.",
        }
        super().__init__(messages.get(code, messages["llm_unavailable"]))


def make_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockLLMProvider(settings.mock_delay)
    return LlamaCppProvider(settings)

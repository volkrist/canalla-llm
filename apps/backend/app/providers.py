import asyncio
import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

import httpx

from .config import Settings
from .tools.local.plan import looks_like_autonomous, needs_research
from .tools.local.workspace import looks_like_coding
from .tools.tor.browser import browser_target_from_prompt, looks_like_tor_browser
from .tools.tor.router import classify_tor
from .tools.web_router import classify_web


def _mock_tool_call(name, args, call_id):
    return {
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }
        ]
    }


def _mock_coding_plan(prompt, previous, names, policy):
    if not looks_like_coding(prompt) and not looks_like_autonomous(prompt):
        return None
    if "list_directory" not in names and "read_file" not in names:
        return None
    mode = "auto"
    if "Web mode=on" in policy:
        mode = "on"
    elif "Web mode=off" in policy:
        mode = "off"
    web_needed = needs_research(prompt) or classify_web(prompt, mode).required
    done = []
    last = {}
    for message in previous:
        try:
            last = json.loads(message.get("content") or "{}")
        except (ValueError, TypeError):
            last = {}
        done.append(last.get("tool") or ("web" if last.get("sources") else "other"))
    if web_needed:
        if not any(item in done for item in ("web_search", "web_fetch", "web")):
            return None
        if "web_search" in done and "web_fetch" not in done:
            return None
    root = "C:\\AlexWorkspace"
    match = re.search(r"CodingWorkspace root=([^;]+)", policy or "")
    if match and match.group(1).strip() and match.group(1).strip() != "(not set)":
        root = match.group(1).strip()
    if "list_directory" in names and "list_directory" not in done:
        return _mock_tool_call("list_directory", {"path": root, "purpose": "Inspect project"}, "mock-list")
    if "git_status" in names and "git_status" not in done:
        return _mock_tool_call("git_status", {"cwd": root, "purpose": "Inspect git"}, "mock-git")
    if "run_python" in names and "run_python" not in done:
        return _mock_tool_call(
            "run_python",
            {"argv": ["-m", "pytest", "-q"], "cwd": root, "purpose": "Run baseline tests"},
            "mock-pytest",
        )
    if last.get("metadata", {}).get("exit_code") not in (None, 0) or last.get("failures"):
        text = str(last.get("text") or "")
        path_match = re.search(r"([\w./\\-]+\.py)", text)
        rel = path_match.group(1) if path_match else "app.py"
        target = rel if ":" in rel or rel.startswith("\\") or rel.startswith("/") else str(Pathish(root, rel))
        if "read_file" in names and done.count("read_file") < 3:
            return _mock_tool_call(
                "read_file", {"path": target, "purpose": "Read failing source"}, "mock-read-fail"
            )
    if "git_diff" in names and "git_diff" not in done and last.get("metadata", {}).get("exit_code") == 0:
        return _mock_tool_call("git_diff", {"cwd": root, "purpose": "Review diff"}, "mock-diff")
    return {"tool_calls": []}


def Pathish(root, rel):
    return root.rstrip("\\/") + "\\" + rel.replace("/", "\\").lstrip("\\")


class LLMProvider(ABC):
    supports_tools = False

    async def plan_tools(self, messages, tools, usage) -> dict:
        raise LLMError("tools_unsupported")

    @abstractmethod
    def stream_chat(self, messages: list[dict[str, str]]) -> AsyncGenerator[str, None]: ...

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
        names = {item.get("function", {}).get("name") for item in tools if isinstance(item, dict)}
        policy = str(messages[0].get("content", "")) if messages else ""
        coding_plan = _mock_coding_plan(prompt, previous, names, policy)
        if coding_plan is not None:
            return coding_plan
        if "tor_browser" in names and looks_like_tor_browser(prompt) and not previous:
            url = browser_target_from_prompt(prompt) or "https://check.torproject.org/"
            return {
                "tool_calls": [
                    {
                        "id": "mock-tor-browser-open",
                        "type": "function",
                        "function": {
                            "name": "tor_browser",
                            "arguments": json.dumps(
                                {"operation": "open", "url": url, "wait_ms": 500},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ]
            }
        if previous:
            last = json.loads(previous[-1]["content"])
            if "tor_browser" in names and (
                last.get("needs_browser")
                or last.get("retrieval") == "browser"
                or any(item.get("needs_browser") for item in last.get("sources") or [])
                or looks_like_tor_browser(prompt)
            ):
                links = last.get("links") or []
                token = next(
                    (
                        str(item.get("id") or "")
                        for item in links
                        if str(item.get("id") or "").upper().startswith("L")
                    ),
                    "",
                )
                if token and last.get("retrieval") == "browser" and len(previous) == 1:
                    return {
                        "tool_calls": [
                            {
                                "id": "mock-tor-browser-click",
                                "type": "function",
                                "function": {
                                    "name": "tor_browser",
                                    "arguments": json.dumps({"operation": "click", "link_id": token}),
                                },
                            }
                        ]
                    }
                url = (last.get("sources") or [{}])[0].get("final_url") or (last.get("sources") or [{}])[
                    0
                ].get("url")
                if url and last.get("retrieval") != "browser":
                    return {
                        "tool_calls": [
                            {
                                "id": "mock-tor-browser",
                                "type": "function",
                                "function": {
                                    "name": "tor_browser",
                                    "arguments": json.dumps(
                                        {"operation": "open", "url": url, "wait_ms": 500}
                                    ),
                                },
                            }
                        ]
                    }
            if (
                len(previous) == 1
                and last.get("sources")
                and not last.get("error")
                and last.get("retrieval") != "browser"
            ):
                url = last["sources"][0].get("final_url")
                fetch_name = "tor_fetch" if "tor_fetch" in names else "web_fetch"
                if last.get("origin") == "server_policy" and "tor_fetch" in names:
                    fetch_name = "tor_fetch"
                if (url or "").endswith(".onion") or ".onion/" in (url or ""):
                    fetch_name = "tor_fetch" if "tor_fetch" in names else fetch_name
                return {
                    "tool_calls": [
                        {
                            "id": "mock-fetch",
                            "type": "function",
                            "function": {
                                "name": fetch_name,
                                "arguments": json.dumps({"urls": [url], "fresh": True}),
                            },
                        }
                    ]
                }
            if (
                len(previous) == 2
                and "tor_fetch" in names
                and last.get("links")
                and any(item.get("is_onion") for item in last.get("links") or [])
            ):
                onion = next(item["url"] for item in last["links"] if item.get("is_onion"))
                return {
                    "tool_calls": [
                        {
                            "id": "mock-tor-follow",
                            "type": "function",
                            "function": {
                                "name": "tor_fetch",
                                "arguments": json.dumps({"urls": [onion], "fresh": True}),
                            },
                        }
                    ]
                }
            return {"tool_calls": []}
        mode = "auto"
        if "Web mode=on" in policy:
            mode = "on"
        elif "Web mode=off" in policy:
            mode = "off"
        tor_mode = "off"
        if "tor_mode=on" in policy:
            tor_mode = "on"
        elif "tor_mode=auto" in policy:
            tor_mode = "auto"
        if "tor_mode=off" in policy:
            tor_mode = "off"
        elif "tor_enabled=True" in policy:
            tor_mode = "auto"
        tor_intent = classify_tor(prompt, tor_mode)
        if "Previously seen Tor URLs" in policy and "tor_fetch" in names:
            seen = [
                item.rstrip(".;")
                for item in re.findall(
                    r"https?://[^\s;]+",
                    policy.split("Previously seen Tor URLs:", 1)[-1],
                )
                if ".onion" in item
            ]
            if seen:
                return {
                    "tool_calls": [
                        {
                            "id": "mock-tor-continue",
                            "type": "function",
                            "function": {
                                "name": "tor_fetch",
                                "arguments": json.dumps({"urls": seen[:1], "fresh": True}),
                            },
                        }
                    ]
                }
        if tor_intent.required and "tor_search" in names:
            return {
                "tool_calls": [
                    {
                        "id": "mock-tor",
                        "type": "function",
                        "function": {
                            "name": "tor_search",
                            "arguments": json.dumps({"query": tor_intent.query}, ensure_ascii=False),
                        },
                    }
                ]
            }
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
            "Это **Canalla LLM**, демонстрационный ответ mock-провайдера.\n\n"
            f"Вы написали: {prompt}\n\n"
            "Авторизация, история и потоковая передача уже работают без GPU. "
            "Этот ответ генерируется локальным шаблоном, а не языковой моделью.\n\n"
            "Пример кода:\n\n```python\ndef greet(name: str) -> str:\n"
            '    return f"Hello, {name}!"\n\nprint(greet("Canalla LLM"))\n```\n'
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
            # A capacity search that ran out of its 60-second window is a typed external blocker,
            # not a broken model: the user is told what is missing and may simply ask again.
            "gpu_capacity_unavailable": (
                "Подходящих GPU сейчас нет в наличии. Попробуйте отправить сообщение ещё раз."
            ),
            "gateway_not_connected": "Canalla Cloud не подключён.",
            "price_limit": "Подходящий GPU существует, но превышает ваш лимит.",
            "no_compatible_gpu": "Подходящих GPU сейчас нет.",
            "startup_timeout": "AI не успел запуститься вовремя. Попробуйте ещё раз.",
        }
        super().__init__(messages.get(code, messages["llm_unavailable"]))


def make_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockLLMProvider(settings.mock_delay)
    return LlamaCppProvider(settings)

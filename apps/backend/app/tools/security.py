import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit

from .contracts import ToolError


def public_ip(value):
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast and not address.is_reserved


async def validate_url(value: str, resolver=None):
    try:
        if len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
            raise ValueError()
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 80, 443}
            or host == "localhost"
            or host.endswith(".onion")
            or host.endswith((".localhost", ".local", ".internal", ".lan", ".home", ".test"))
        ):
            raise ValueError()
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            if "." not in host:
                raise ValueError()
            if resolver:
                addresses = await resolver(host)
            else:
                records = await asyncio.wait_for(
                    asyncio.get_running_loop().getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM),
                    5,
                )
                addresses = [record[4][0] for record in records]
        if not addresses or not all(public_ip(address) for address in addresses):
            raise ValueError()
        return value
    except (ValueError, OSError, asyncio.TimeoutError):
        raise ToolError("unsafe_url") from None


def sanitized(text, secrets=(), limit=20000):
    value = str(text)
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?i)https?://[^/\s:@]+:[^/\s@]+@", "https://[redacted]@", value)
    value = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[redacted]", value)
    value = re.sub(
        r"(?i)((?:api[_-]?key|password|passwd|secret|access_token|authorization)\s*[:=]\s*)[^\s,;&]+",
        r"\1[redacted]",
        value,
    )
    return "".join(c for c in value if c in "\n\t" or ord(c) >= 32)[:limit]


def digest(args, action=None):
    payload = args if action is None else {"action": action, "command": action, "arguments": args}
    if action is not None and isinstance(args, dict) and args.get("path") is not None:
        payload = {**payload, "path": args.get("path")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def input_summary(definition, args, secrets=(), follow=False):
    # Full payload is held only by the running executor. Audit stores bounded safe preview.
    from .contracts import RiskLevel
    from .explain import explanation
    from .policy import LOCAL_CAPABILITIES, effective_risk

    values = args.model_dump()
    result = {"action": definition.name, "provider": definition.provider}
    risk = effective_risk(definition, args)
    for key in (
        "query",
        "url",
        "urls",
        "goal",
        "action",
        "selector",
        "text",
        "path",
        "executable",
        "argv",
        "cwd",
        "source",
        "destination",
        "root",
        "content",
        "expected_before_sha256",
        "timeout_seconds",
        "tool_run_id",
        "purpose",
        "reference",
        "hive",
        "key",
        "name",
        "package",
        "device",
        "paths",
        "elevate",
        "pid",
        "rev",
        "remote",
        "branch",
        "force",
        "mode",
        "operation",
        "link_id",
        "ref",
        "message",
        "old_text",
        "new_text",
        "start_type",
        "max_count",
        "delete",
        "field",
        "item",
        "quantity",
        "currency",
        "total_price",
        "seller",
        "to",
        "subject",
        "body",
        "target",
    ):
        if key in values and values[key] not in (None, "", [], {}, False):
            result[key] = sanitized(json.dumps(values[key], ensure_ascii=False), secrets, 500)
    if definition.name == "tor_search" and result.get("query"):
        result["action_detail"] = "Search: " + str(result["query"])[:200]
    if definition.name == "tor_fetch" and result.get("urls"):
        prefix = "Followed link: " if follow else "Opened: "
        result["action_detail"] = prefix + str(result["urls"])[:200]
    if definition.name == "tor_browser":
        operation = str(values.get("operation") or "open")
        if operation == "open":
            result["action_detail"] = "Tor Browser · Открыто " + str(values.get("url") or "")[:180]
        elif operation == "click":
            result["action_detail"] = "Tor Browser · Перешёл по ссылке " + str(values.get("link_id") or "")
        elif operation == "close":
            result["action_detail"] = "Tor Browser · Завершено"
        else:
            result["action_detail"] = "Tor Browser · Rendering"
    if risk in {RiskLevel.SENSITIVE, RiskLevel.CRITICAL} or definition.capability in LOCAL_CAPABILITIES:
        result.update(explanation(definition, args, risk=risk))
    return result

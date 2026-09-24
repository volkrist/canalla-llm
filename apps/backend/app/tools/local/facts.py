"""Verified facts from successful tools. Secrets never stored."""

import os
import re
from datetime import datetime, timezone

from .paths import native_path

SECRET_KEYS = {
    "password",
    "secret",
    "token",
    "cookie",
    "authorization",
    "api_key",
    "cdp_url",
    "credential",
}

ALLOWED_KINDS = {
    "FILE_CREATED",
    "FILE_READ",
    "FILE_WRITTEN",
    "FILE_FOUND",
    "SYSTEM_INFO",
    "HASH_RESULT",
    "SOFTWARE_INSTALLED",
    "PROCESS_STARTED",
    "PROCESS_STOPPED",
    "BROWSER_PAGE",
    "BROWSER_ERROR",
    "RESEARCH_SOURCE",
    "DIRECTORY_CREATED",
    "KNOWN_FOLDERS",
    "FILE_TARGET",
    "BROWSER_CLOSED",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clean(value):
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if str(key).casefold() not in SECRET_KEYS}
    if isinstance(value, list):
        return [_clean(item) for item in value[:32]]
    if isinstance(value, str):
        return value[:4000]
    return value


def store(facts: dict) -> list:
    payload = dict(facts or {})
    items = payload.get("verified")
    return list(items) if isinstance(items, list) else []


def record(facts: dict, kind: str, data: dict, *, tool=None, tool_run_id=None, step_id=None) -> dict:
    if kind not in ALLOWED_KINDS:
        return facts or {}
    payload = dict(facts or {})
    items = store(payload)
    if kind in {"FILE_READ", "HASH_RESULT", "FILE_WRITTEN", "FILE_TARGET"} and data.get("path"):
        items = [
            item
            for item in items
            if not (
                item.get("kind") == kind
                and str(item.get("path") or "").casefold() == str(data.get("path") or "").casefold()
            )
        ]
    entry = {
        "kind": kind,
        "confidence": "verified",
        "timestamp": _now(),
        "tool": tool,
        "source_tool_run_id": tool_run_id,
        "step_id": step_id,
    }
    cleaned = _clean(data)
    # _clean recurses over JSON values, so the checker cannot see that a mapping stays a mapping.
    if isinstance(cleaned, dict):
        entry.update(cleaned)
    items.append(entry)
    payload["verified"] = items[-80:]
    payload["verified_count"] = len(payload["verified"])
    return payload


def latest(facts: dict, kind: str, path: str | None = None) -> dict | None:
    items = [item for item in reversed(store(facts)) if item.get("kind") == kind]
    if path:
        items = [item for item in items if _same_path(item.get("path"), path)]
    return items[0] if items else None


def _same_path(stored, needle) -> bool:
    left = native_path(str(stored or "")).casefold().rstrip(os.sep)
    right = native_path(str(needle or "")).casefold().rstrip(os.sep)
    if not right:
        return True
    if left == right:
        return True
    if os.sep not in right and (left.endswith(os.sep + right) or left.rsplit(os.sep, 1)[-1] == right):
        return True
    return False


def public_block(facts: dict) -> str:
    items = store(facts)
    if not items:
        return ""
    lines = [
        "VERIFIED_RESULTS",
        "These are verified facts from successful tools. Do not contradict them.",
        "Do not claim you lack access if a successful local tool is listed.",
        "If the user asked for a value present here, include it. Never invent missing success.",
    ]
    for item in items[-24:]:
        kind = item.get("kind")
        if kind == "FILE_READ":
            lines.append(
                f"file_read path={item.get('path')} exists=true sha256={item.get('sha256') or ''} "
                f"content={item.get('content_excerpt') or ''}"
            )
        elif kind == "FILE_CREATED":
            lines.append(f"file_created path={item.get('path')} exists=true")
        elif kind == "FILE_WRITTEN":
            lines.append(
                f"file_written path={item.get('path')} after_sha={item.get('after_sha') or ''} "
                f"expected_sha={item.get('expected_sha256') or ''} verified={item.get('verified')} "
                f"content={item.get('content_excerpt') or ''} exists=true"
            )
        elif kind == "FILE_FOUND":
            lines.append(f"file_found query={item.get('query')} path={item.get('path')}")
        elif kind == "HASH_RESULT":
            lines.append(
                f"sha256 path={item.get('path')} algorithm={item.get('algorithm') or 'sha256'} "
                f"digest={item.get('digest')}"
            )
        elif kind == "SYSTEM_INFO":
            lines.append(
                f"system_info windows={item.get('windows_version')} cpu={item.get('cpu')} "
                f"ram={item.get('ram')} disk={item.get('disk')}"
            )
        elif kind == "SOFTWARE_INSTALLED":
            lines.append(
                f"software_installed package={item.get('package_id')} version={item.get('version')} "
                f"verification={item.get('verification_result')}"
            )
        elif kind == "PROCESS_STARTED":
            lines.append(
                f"process_started pid={item.get('pid')} tool_run_id={item.get('tool_run_id')} "
                f"started_by_alex=true"
            )
        elif kind == "PROCESS_STOPPED":
            lines.append(f"process_stopped pid={item.get('pid')} verified_dead={item.get('verified_dead')}")
        elif kind == "BROWSER_PAGE":
            lines.append(
                f"browser_page url={item.get('url')} title={item.get('title')} source={item.get('source_id')}"
            )
        elif kind == "BROWSER_ERROR":
            lines.append(
                f"browser_error code={item.get('error_code')} status={item.get('session_status')} "
                f"url={item.get('url') or ''}"
            )
        elif kind == "RESEARCH_SOURCE":
            lines.append(
                f"research_source id={item.get('label')} url={item.get('url')} "
                f"authority={item.get('authority')}"
            )
        elif kind == "DIRECTORY_CREATED":
            lines.append(f"directory_created path={item.get('path')} exists=true")
        elif kind == "KNOWN_FOLDERS":
            lines.append(
                f"known_folders desktop={item.get('desktop')} documents={item.get('documents')} "
                f"downloads={item.get('downloads')}"
            )
        elif kind == "FILE_TARGET":
            lines.append(
                f"file_target path={item.get('path')} kind={item.get('kind') or 'file'} "
                f"source={item.get('source') or ''}"
            )
        elif kind == "BROWSER_CLOSED":
            lines.append(
                f"browser_closed session={item.get('session_id') or ''} "
                f"supplier_stop_confirmed={item.get('supplier_stop_confirmed')}"
            )
    return "\n".join(lines)


def from_tool(facts: dict, name: str, output: dict, arguments=None, tool_run_id=None) -> dict:
    meta = (output or {}).get("metadata") or {}
    text = str((output or {}).get("text") or "")
    args = arguments if isinstance(arguments, dict) else {}
    path = str(meta.get("path") or args.get("path") or args.get("destination") or "")
    payload = facts or {}
    if name == "read_file" and text:
        digest = _digest_hex(meta.get("sha256") or meta.get("before_sha256") or "")
        body = text
        if body.lower().startswith("sha256="):
            first, _, rest = body.partition("\n")
            if not digest:
                digest = _digest_hex(first.split("=", 1)[1] if "=" in first else "")
            body = rest
        payload = record(
            payload,
            "FILE_READ",
            {"path": path, "sha256": digest, "content_excerpt": body[:1500], "exists": True},
            tool=name,
            tool_run_id=tool_run_id,
        )
        if digest:
            payload = record(
                payload,
                "HASH_RESULT",
                {"path": path, "algorithm": "sha256", "digest": digest, "verified": True},
                tool=name,
                tool_run_id=tool_run_id,
            )
    elif name == "hash_file":
        digest = _digest_hex(
            meta.get("digest") or meta.get("sha256") or text,
        )
        if not digest:
            return payload
        payload = record(
            payload,
            "HASH_RESULT",
            {
                "path": path,
                "algorithm": "sha256",
                "digest": digest,
                "verified": True,
            },
            tool=name,
            tool_run_id=tool_run_id,
        )
    elif name == "write_file":
        expected = str(meta.get("expected_sha256") or "")
        actual = str(meta.get("after_sha256") or meta.get("after_sha") or "")
        verified = bool(meta.get("verified")) or (expected and expected == actual)
        content = str(args.get("content") or meta.get("content_excerpt") or "")[:1500]
        payload = record(
            payload,
            "FILE_CREATED" if not meta.get("before_sha256") else "FILE_WRITTEN",
            {
                "path": path,
                "before_sha": meta.get("before_sha256"),
                "after_sha": actual,
                "expected_sha256": expected,
                "verified": verified,
                "content_excerpt": content,
                "exists": True,
            },
            tool=name,
            tool_run_id=tool_run_id,
        )
    elif name == "create_directory":
        payload = record(
            payload, "DIRECTORY_CREATED", {"path": path, "exists": True}, tool=name, tool_run_id=tool_run_id
        )
    elif name in {"search_code", "search_files"} and text.strip() and "no_match" not in text.casefold():
        first = text.strip().splitlines()[0]
        numbered = re.match(r"^(.*):(\d+):(.*)$", first)
        found = numbered.group(1) if numbered else first
        payload = record(
            payload,
            "FILE_FOUND",
            {"query": args.get("query"), "path": found},
            tool=name,
            tool_run_id=tool_run_id,
        )
    elif name == "get_system_info":
        payload = record(
            payload,
            "SYSTEM_INFO",
            {
                "windows_version": meta.get("os_version") or _kv(text, "os_version"),
                "cpu": meta.get("cpu_logical_processors") or _kv(text, "cpu_logical_processors"),
                "ram": meta.get("ram_total_mb") or _kv(text, "ram_total_mb"),
                "disk": meta.get("system_disk_free_gb") or _kv(text, "system_disk_free_gb"),
            },
            tool=name,
            tool_run_id=tool_run_id,
        )
    elif name == "get_known_folders":
        payload = record(
            payload,
            "KNOWN_FOLDERS",
            {
                "desktop": meta.get("desktop") or _kv(text, "desktop"),
                "documents": meta.get("documents") or _kv(text, "documents"),
                "downloads": meta.get("downloads") or _kv(text, "downloads"),
            },
            tool=name,
            tool_run_id=tool_run_id,
        )
    elif name in {"run_process", "run_python", "run_powershell"}:
        pid = meta.get("pid")
        if not pid:
            match = re.search(r"pid=(\d+)", text)
            pid = int(match.group(1)) if match else None
        if pid:
            payload = record(
                payload,
                "PROCESS_STARTED",
                {
                    "pid": pid,
                    "tool_run_id": tool_run_id or meta.get("tool_run_id"),
                    "started_by_alex": True,
                },
                tool=name,
                tool_run_id=tool_run_id,
            )
    elif name == "stop_process":
        payload = record(
            payload,
            "PROCESS_STOPPED",
            {"pid": meta.get("pid"), "tool_run_id": args.get("tool_run_id"), "verified_dead": True},
            tool=name,
            tool_run_id=tool_run_id,
        )
    elif name == "install_software" and not (output or {}).get("error"):
        payload = record(
            payload,
            "SOFTWARE_INSTALLED",
            {
                "package_id": args.get("package"),
                "version": meta.get("version") or text[:80],
                "verification_result": text[:200],
            },
            tool=name,
            tool_run_id=tool_run_id,
        )
    elif name == "web_browser" and (output or {}).get("error"):
        payload = record(
            payload,
            "BROWSER_ERROR",
            {
                "error_code": (output or {}).get("error") or meta.get("error_code"),
                "session_status": meta.get("session_status") or "FAILED",
                "url": meta.get("current_url") or args.get("url") or "",
            },
            tool=name,
            tool_run_id=tool_run_id,
        )
    elif name == "web_browser" and (args.get("operation") or "") == "close":
        payload = record(
            payload,
            "BROWSER_CLOSED",
            {
                "session_id": meta.get("session_id") or "",
                "supplier_stop_confirmed": meta.get("supplier_stop_confirmed"),
                "session_status": meta.get("session_status") or "CLOSED",
            },
            tool=name,
            tool_run_id=tool_run_id,
        )
    elif name == "web_browser":
        source = (output.get("sources") or [{}])[0] if (output or {}).get("sources") else {}
        title = str(meta.get("title") or source.get("title") or (text.split("\n", 1)[0] if text else ""))[
            :200
        ]
        url = str(
            meta.get("current_url")
            or meta.get("final_url")
            or source.get("final_url")
            or source.get("url")
            or args.get("url")
            or ""
        )
        if title or url:
            existing = [
                item
                for item in store(payload)
                if item.get("kind") == "BROWSER_PAGE"
                and str(item.get("url") or "").rstrip("/") == url.rstrip("/")
            ]
            if not existing:
                payload = record(
                    payload,
                    "BROWSER_PAGE",
                    {
                        "url": url,
                        "title": title,
                        "source_id": source.get("label"),
                        "page_index": 1
                        + len([item for item in store(payload) if item.get("kind") == "BROWSER_PAGE"]),
                        "verified": True,
                    },
                    tool=name,
                    tool_run_id=tool_run_id,
                )
    if name in {"read_file", "write_file", "hash_file", "patch_file", "delete_file"} and path:
        from .targets import looks_like_file_path

        if looks_like_file_path(path):
            payload = record(
                payload,
                "FILE_TARGET",
                {"path": path, "kind": "file", "source": "previous_action"},
                tool=name,
                tool_run_id=tool_run_id,
            )
    if (output or {}).get("sources") and str(name).startswith(("web_", "tor_")):
        for source in (output.get("sources") or [])[:8]:
            payload = record(
                payload,
                "RESEARCH_SOURCE",
                {
                    "label": source.get("label"),
                    "url": source.get("final_url") or source.get("url"),
                    "authority": source.get("authority"),
                },
                tool=name,
                tool_run_id=tool_run_id,
            )
    return payload


def _digest_hex(value) -> str:
    token = str(value or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", token):
        return token.lower()
    match = re.search(r"\b([0-9a-fA-F]{64})\b", token)
    return match.group(1).lower() if match else ""


def _kv(text: str, key: str) -> str:
    prefix = key.lower() + "="
    for line in (text or "").splitlines():
        if line.lower().startswith(prefix):
            return line.split("=", 1)[1].strip()
    return ""


def bump_metric(facts: dict, key: str, amount: int = 1) -> dict:
    payload = dict(facts or {})
    metrics = dict(payload.get("metrics") or {})
    metrics[key] = int(metrics.get(key) or 0) + amount
    payload["metrics"] = metrics
    return payload

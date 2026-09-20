"""Duplicate action suppression and no-progress detection."""

import hashlib
import json
import re

from .facts import bump_metric, store


def signature(name: str, arguments) -> str:
    if isinstance(arguments, str):
        payload = arguments
    else:
        payload = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{name}:{payload}".encode("utf-8")).hexdigest()


def remember(facts: dict, name: str, arguments, novelty: bool) -> dict:
    payload = dict(facts or {})
    recent = list(payload.get("action_signatures") or [])
    digest = signature(name, arguments)
    recent.append({"tool": name, "digest": digest, "novelty": bool(novelty)})
    payload["action_signatures"] = recent[-24:]
    return payload


def similar_count(facts: dict, name: str, arguments) -> int:
    digest = signature(name, arguments)
    return sum(
        1
        for item in (facts or {}).get("action_signatures") or []
        if item.get("tool") == name and item.get("digest") == digest and not item.get("novelty")
    )


def should_block(facts: dict, name: str, arguments, prompt: str = "") -> str | None:
    asked = prompt or ""
    args = arguments if isinstance(arguments, dict) else {}
    if name == "read_file" and re.search(r"(?i)перечитай|re-?read|recheck|заново прочит", asked):
        return None
    if re.search(r"(?i)заново посчитай|пересчитай|recompute", asked) and name == "hash_file":
        return None
    if name in {"hash_file", "read_file"}:
        from .facts import latest
        from .targets import coerce_file_argument, is_directory_path

        offered = str(args.get("path") or "")
        try:
            resolved = coerce_file_argument(name, offered, asked)
        except Exception:
            resolved = offered
        if name == "hash_file":
            hashed = latest(facts, "HASH_RESULT", resolved or None) or latest(facts, "HASH_RESULT")
            if hashed and hashed.get("digest"):
                if offered and is_directory_path(offered):
                    return "duplicate_readonly"
                if resolved and _same_tool_path(hashed.get("path"), resolved):
                    return "duplicate_readonly"
        if name == "read_file":
            read = latest(facts, "FILE_READ", resolved or None)
            if read and not re.search(r"(?i)перечитай|re-?read|заново прочит", asked):
                if offered and is_directory_path(offered):
                    return "duplicate_readonly"
    digest = signature(name, arguments)
    history = list((facts or {}).get("action_signatures") or [])
    same = [item for item in history if item.get("digest") == digest]
    if len(same) >= 1 and name in {
        "read_file",
        "hash_file",
        "get_system_info",
        "get_known_folders",
        "list_directory",
        "search_code",
        "search_files",
    }:
        return "duplicate_readonly"
    stale = [item for item in history if item.get("tool") == name and not item.get("novelty")]
    if len(stale) >= 2 and name in {
        "list_directory",
        "search_files",
        "search_code",
        "run_powershell",
        "run_python",
    }:
        return "no_progress"
    return None


def _same_tool_path(stored, needle) -> bool:
    left = str(stored or "").replace("/", "\\").casefold().rstrip("\\")
    right = str(needle or "").replace("/", "\\").casefold().rstrip("\\")
    return bool(left and right and left == right)


def apply_block(facts: dict, code: str) -> dict:
    payload = bump_metric(
        facts, "duplicate_actions_blocked" if code == "duplicate_readonly" else "no_progress_events"
    )
    payload["last_progress_error"] = code
    return payload


def recommended(code: str) -> str:
    if code == "duplicate_readonly":
        return "reuse_verified_fact"
    if code == "no_progress":
        return "use_typed_search_or_replan"
    return "stop"


def reused_result(facts: dict, name: str, arguments) -> dict | None:
    from .facts import latest
    from .targets import coerce_file_argument

    args = arguments if isinstance(arguments, dict) else {}
    path = str(args.get("path") or args.get("root") or "")
    prompt = str(args.get("purpose") or "")
    if name in {"read_file", "hash_file"} and path:
        try:
            path = coerce_file_argument(name, path, prompt)
        except Exception:
            pass
    if name == "read_file":
        item = latest(facts, "FILE_READ", path or None)
        if item:
            return {
                "text": f"sha256={item.get('sha256') or ''}\n{item.get('content_excerpt') or ''}",
                "metadata": {"sha256": item.get("sha256"), "path": item.get("path"), "reused": True},
                "origin": "server_policy",
                "tool": name,
            }
    if name == "hash_file":
        item = latest(facts, "HASH_RESULT", path or None)
        if item and item.get("digest"):
            return {
                "text": str(item["digest"]),
                "metadata": {
                    "digest": item["digest"],
                    "sha256": item["digest"],
                    "path": item.get("path"),
                    "reused": True,
                },
                "origin": "server_policy",
                "tool": name,
            }
    if name == "get_system_info":
        item = latest(facts, "SYSTEM_INFO")
        if item:
            text = (
                f"os_version={item.get('windows_version')}\ncpu_logical_processors={item.get('cpu')}\n"
                f"ram_total_mb={item.get('ram')}\nsystem_disk_free_gb={item.get('disk')}"
            )
            return {
                "text": text,
                "metadata": {**item, "reused": True},
                "origin": "server_policy",
                "tool": name,
            }
    if name in {"search_code", "search_files"}:
        item = latest(facts, "FILE_FOUND")
        if item and str(args.get("query") or "") == str(item.get("query") or ""):
            return {
                "text": str(item.get("path") or ""),
                "metadata": {"path": item.get("path"), "reused": True},
                "origin": "server_policy",
                "tool": name,
            }
    return None


def novelty_from_output(name: str, output: dict, facts: dict) -> bool:
    text = str((output or {}).get("text") or "")
    if not text.strip():
        return False
    if name in {"search_code", "search_files"}:
        return bool(text.strip()) and not latest_same_text(facts, text)
    if (output or {}).get("error"):
        return False
    return True


def latest_same_text(facts: dict, text: str) -> bool:
    needle = (text or "").strip()[:200]
    for item in reversed(store(facts)):
        blob = str(item.get("content_excerpt") or item.get("path") or "")
        if needle and needle in blob:
            return True
    return False

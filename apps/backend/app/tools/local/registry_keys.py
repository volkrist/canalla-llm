from ..contracts import ToolError

HIVES = {"HKCU", "HKLM"}
DENIED_PARTS = {"sam", "security"}
DENIED_SUFFIX = "\\system\\currentcontrolset\\control\\lsa\\"


def assert_registry_key(hive: str, key: str) -> str:
    if hive not in HIVES:
        raise ToolError("path_denied")
    value = str(key or "").replace("/", "\\").strip().strip("\\")
    if not value or "\x00" in value:
        raise ToolError("path_denied")
    parts = [part for part in value.split("\\") if part]
    if any(part == ".." for part in parts):
        raise ToolError("path_denied")
    if any(part.casefold() in DENIED_PARTS for part in parts):
        raise ToolError("path_denied")
    lowered = "\\" + "\\".join(parts).casefold() + "\\"
    if DENIED_SUFFIX in lowered:
        raise ToolError("path_denied")
    return "\\".join(parts)

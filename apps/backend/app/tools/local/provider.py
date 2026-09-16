from pydantic import BaseModel, ConfigDict, Field

from ..contracts import RiskLevel, ToolDefinition, ToolError, ToolProvider, ToolResult
from .paths import assert_allowed_path


class PathArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)


class WriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=20000)
    expected_before_sha256: str | None = Field(default=None, max_length=64)


class CopyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=500)
    destination: str = Field(min_length=1, max_length=500)


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str = Field(min_length=1, max_length=500)
    query: str = Field(min_length=1, max_length=200)


class ProcessArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executable: str = Field(min_length=1, max_length=300)
    argv: list[str] = Field(default_factory=list, max_length=32)
    cwd: str | None = Field(default=None, max_length=500)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class InterpreterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argv: list[str] = Field(min_length=1, max_length=32)
    cwd: str | None = Field(default=None, max_length=500)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class ProcessIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_run_id: str = Field(min_length=36, max_length=36)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalDeviceProvider(ToolProvider):
    """Does not execute OS commands. Waits for the paired Tauri host result."""

    def __init__(self, capability):
        self.capability = capability

    async def execute(self, args, context):
        payload = (context.preview or {}).get("host_result") or {}
        if not payload:
            raise ToolError("host_offline")
        return ToolResult(
            text=str(payload.get("text") or payload.get("stdout") or "")[:20000],
            metadata={
                k: payload.get(k)
                for k in (
                    "exit_code",
                    "stdout",
                    "stderr",
                    "cwd",
                    "before_sha256",
                    "after_sha256",
                    "files_changed",
                )
                if k in payload
            },
        )


def _guard_paths(args, roots):
    for key in ("path", "source", "destination", "root", "cwd"):
        value = getattr(args, key, None)
        if value:
            assert_allowed_path(value, roots)


def register_local_tools(registry):
    provider = LocalDeviceProvider("local_fs")
    process = LocalDeviceProvider("local_process")
    info = LocalDeviceProvider("local_info")
    for name, schema, capability, risk, description, adapter in (
        (
            "list_directory",
            PathArgs,
            "local_fs",
            RiskLevel.READ_ONLY,
            "List a trusted workspace directory.",
            provider,
        ),
        (
            "read_file",
            PathArgs,
            "local_fs",
            RiskLevel.READ_ONLY,
            "Read a UTF-8 file in a trusted workspace.",
            provider,
        ),
        (
            "write_file",
            WriteArgs,
            "local_fs",
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            "Atomically write a file in a trusted workspace.",
            provider,
        ),
        (
            "create_directory",
            PathArgs,
            "local_fs",
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            "Create a directory in a trusted workspace.",
            provider,
        ),
        (
            "copy_file",
            CopyArgs,
            "local_fs",
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            "Copy a file inside trusted roots.",
            provider,
        ),
        (
            "move_file",
            CopyArgs,
            "local_fs",
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            "Move a file inside trusted roots.",
            provider,
        ),
        (
            "search_files",
            SearchArgs,
            "local_fs",
            RiskLevel.READ_ONLY,
            "Search file names in a trusted root.",
            provider,
        ),
        (
            "run_process",
            ProcessArgs,
            "local_process",
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            "Run executable + argv in a trusted workspace. Never shell=True.",
            process,
        ),
        (
            "run_powershell",
            InterpreterArgs,
            "local_process",
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            "Run PowerShell with explicit argv. Requires confirmation.",
            process,
        ),
        (
            "run_python",
            InterpreterArgs,
            "local_process",
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            "Run Python with explicit argv. Requires confirmation.",
            process,
        ),
        (
            "process_status",
            ProcessIdArgs,
            "local_info",
            RiskLevel.READ_ONLY,
            "Status of an Alex-started process.",
            info,
        ),
        (
            "stop_process",
            ProcessIdArgs,
            "local_info",
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            "Stop only the Alex Job Object for this tool_run_id.",
            info,
        ),
        (
            "get_system_info",
            EmptyArgs,
            "local_info",
            RiskLevel.READ_ONLY,
            "Non-identifying local host summary.",
            info,
        ),
    ):
        registry.register(
            ToolDefinition(name, description, schema, capability, risk, "free", 120, "local_device"),
            adapter,
        )

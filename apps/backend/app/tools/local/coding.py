from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import RiskLevel, ToolDefinition
from .provider import EmptyArgs, LocalDeviceProvider, RegistryArgs, ServiceArgs


class PatchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    old_text: str = Field(min_length=1, max_length=20000)
    new_text: str = Field(default="", max_length=20000)
    expected_before_sha256: str = Field(min_length=64, max_length=64)
    purpose: str | None = Field(default=None, max_length=500)


class SearchCodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str = Field(min_length=1, max_length=500)
    query: str = Field(min_length=1, max_length=200)
    purpose: str | None = Field(default=None, max_length=500)


class InspectProcessArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: int = Field(ge=1, le=4_000_000_000)
    purpose: str | None = Field(default=None, max_length=500)


class ServiceSettingsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_\-]+$")
    start_type: Literal["demand", "auto", "disabled"] = "demand"
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = True


class GitCwdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str = Field(min_length=1, max_length=500)
    purpose: str | None = Field(default=None, max_length=500)


class GitLogArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str = Field(min_length=1, max_length=500)
    max_count: int = Field(default=10, ge=1, le=50)
    purpose: str | None = Field(default=None, max_length=500)


class GitShowArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str = Field(min_length=1, max_length=500)
    rev: str = Field(min_length=1, max_length=64, pattern=r"^(HEAD|[A-Za-z0-9._/\-]+)$")
    purpose: str | None = Field(default=None, max_length=500)


class GitAddArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str = Field(min_length=1, max_length=500)
    paths: list[str] = Field(min_length=1, max_length=32)
    purpose: str | None = Field(default=None, max_length=500)


class GitCommitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=200)
    purpose: str | None = Field(default=None, max_length=500)


class GitPushArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str = Field(min_length=1, max_length=500)
    remote: str = Field(default="origin", max_length=80, pattern=r"^[A-Za-z0-9._\-]+$")
    branch: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9._/\-]+$")
    force: bool = False
    purpose: str | None = Field(default=None, max_length=500)


class GitResetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str = Field(min_length=1, max_length=500)
    mode: Literal["soft", "mixed", "hard"] = "mixed"
    ref: str = Field(default="HEAD", max_length=64, pattern=r"^(HEAD|[A-Za-z0-9._/\-]+)$")
    purpose: str | None = Field(default=None, max_length=500)


class GitBranchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str = Field(min_length=1, max_length=500)
    delete: bool = False
    force: bool = False
    name: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9._/\-]+$")
    purpose: str | None = Field(default=None, max_length=500)


class GitRestoreArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cwd: str = Field(min_length=1, max_length=500)
    path: str = Field(min_length=1, max_length=500)
    purpose: str | None = Field(default=None, max_length=500)


def register_coding_tools(registry):
    files = LocalDeviceProvider("local_fs")
    info = LocalDeviceProvider("local_info")
    registry_p = LocalDeviceProvider("local_registry")
    service = LocalDeviceProvider("local_service")
    install = LocalDeviceProvider("local_install")
    git = LocalDeviceProvider("local_git")
    for name, schema, capability, risk, description, adapter in (
        (
            "patch_file",
            PatchArgs,
            "local_fs",
            RiskLevel.NORMAL_CHANGE,
            "Replace old_text with new_text. expected_before_sha256 must equal the sha256 from the latest read_file of that path. CONFLICT if the file changed.",
            files,
        ),
        (
            "search_code",
            SearchCodeArgs,
            "local_fs",
            RiskLevel.READ,
            "Search file contents under a known root. WHEN TO USE: find a marker/text in this folder. WHEN NOT: whole user profile, helper scripts, or filename-only search.",
            files,
        ),
        (
            "list_processes",
            EmptyArgs,
            "local_info",
            RiskLevel.READ,
            "List running processes: name and PID only. No command lines or hardware identifiers.",
            info,
        ),
        (
            "inspect_process",
            InspectProcessArgs,
            "local_info",
            RiskLevel.READ,
            "Inspect one process by PID. No window titles or command-line secrets.",
            info,
        ),
        (
            "list_volumes",
            EmptyArgs,
            "local_info",
            RiskLevel.READ,
            "List disks and volumes without serial numbers.",
            info,
        ),
        (
            "list_installed_software",
            EmptyArgs,
            "local_info",
            RiskLevel.READ,
            "Inspect installed software names from the current-user uninstall list.",
            install,
        ),
        (
            "query_service",
            ServiceArgs,
            "local_service",
            RiskLevel.READ,
            "Query a Windows service.",
            service,
        ),
        (
            "start_service",
            ServiceArgs,
            "local_service",
            RiskLevel.SENSITIVE,
            "Start a Windows service. Shows UAC. Live tests must not target real system services.",
            service,
        ),
        (
            "stop_service",
            ServiceArgs,
            "local_service",
            RiskLevel.SENSITIVE,
            "Stop a Windows service. Shows UAC. Live tests must not target real system services.",
            service,
        ),
        (
            "restart_service",
            ServiceArgs,
            "local_service",
            RiskLevel.SENSITIVE,
            "Restart a Windows service. Shows UAC.",
            service,
        ),
        (
            "change_service_settings",
            ServiceSettingsArgs,
            "local_service",
            RiskLevel.SENSITIVE,
            "Change a service start type after confirmation. Shows UAC.",
            service,
        ),
        (
            "read_registry",
            RegistryArgs,
            "local_registry",
            RiskLevel.READ,
            "Read an HKCU or HKLM registry value.",
            registry_p,
        ),
        (
            "write_registry",
            RegistryArgs,
            "local_registry",
            RiskLevel.SENSITIVE,
            "Write an HKCU or HKLM registry value. HKLM requires elevation.",
            registry_p,
        ),
        (
            "delete_registry_value",
            RegistryArgs,
            "local_registry",
            RiskLevel.SENSITIVE,
            "Delete a registry value. Live tests use only HKCU\\Software\\AlexLLM\\Test.",
            registry_p,
        ),
        (
            "delete_registry_key",
            RegistryArgs,
            "local_registry",
            RiskLevel.SENSITIVE,
            "Delete a registry key. Live tests use only disposable HKCU test keys.",
            registry_p,
        ),
        (
            "git_status",
            GitCwdArgs,
            "local_git",
            RiskLevel.READ,
            "Inspect git status in a workspace. Never prints credentials.",
            git,
        ),
        (
            "git_diff",
            GitCwdArgs,
            "local_git",
            RiskLevel.READ,
            "Inspect git diff. Credentials in remotes are redacted.",
            git,
        ),
        (
            "git_log",
            GitLogArgs,
            "local_git",
            RiskLevel.READ,
            "Inspect recent git history.",
            git,
        ),
        (
            "git_show",
            GitShowArgs,
            "local_git",
            RiskLevel.READ,
            "Show a git object. Do not request credential files.",
            git,
        ),
        (
            "git_branch",
            GitBranchArgs,
            "local_git",
            RiskLevel.READ,
            "List or delete a git branch. Deleting main/master/develop is CRITICAL.",
            git,
        ),
        (
            "git_add",
            GitAddArgs,
            "local_git",
            RiskLevel.NORMAL_CHANGE,
            "Stage paths with git add. Explicit paths only.",
            git,
        ),
        (
            "git_commit",
            GitCommitArgs,
            "local_git",
            RiskLevel.NORMAL_CHANGE,
            "Create a git commit. Does not push. Do not put secrets in the message.",
            git,
        ),
        (
            "git_restore",
            GitRestoreArgs,
            "local_git",
            RiskLevel.SENSITIVE,
            "Restore a path from HEAD. Used as a task rollback when git exists.",
            git,
        ),
        (
            "git_push",
            GitPushArgs,
            "local_git",
            RiskLevel.SENSITIVE,
            "Push to a named remote. Always requires confirmation. force is CRITICAL and not automatic.",
            git,
        ),
        (
            "git_reset",
            GitResetArgs,
            "local_git",
            RiskLevel.SENSITIVE,
            "git reset. --hard is CRITICAL and is not executed automatically.",
            git,
        ),
    ):
        registry.register(
            ToolDefinition(name, description, schema, capability, risk, "free", 120, "local_device"),
            adapter,
        )

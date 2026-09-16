from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import RiskLevel, ToolDefinition, ToolError, ToolProvider, ToolResult
from .paths import assert_local_path
from .registry_keys import assert_registry_key


class PathArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    purpose: str | None = Field(default=None, max_length=500)


class WriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=20000)
    expected_before_sha256: str | None = Field(default=None, max_length=64)
    purpose: str | None = Field(default=None, max_length=500)


class CopyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=500)
    destination: str = Field(min_length=1, max_length=500)
    purpose: str | None = Field(default=None, max_length=500)


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str = Field(min_length=1, max_length=500)
    query: str = Field(min_length=1, max_length=200)
    purpose: str | None = Field(default=None, max_length=500)


class ProcessArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executable: str = Field(min_length=1, max_length=300)
    argv: list[str] = Field(default_factory=list, max_length=32)
    cwd: str | None = Field(default=None, max_length=500)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = False


class InterpreterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    argv: list[str] = Field(min_length=1, max_length=32)
    cwd: str | None = Field(default=None, max_length=500)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = False


class ProcessIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_run_id: str = Field(min_length=36, max_length=36)
    purpose: str | None = Field(default=None, max_length=500)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str | None = Field(default=None, max_length=500)


class MassDeleteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = Field(min_length=2, max_length=32)
    purpose: str | None = Field(default=None, max_length=500)


class RegistryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hive: Literal["HKCU", "HKLM"] = "HKCU"
    key: str = Field(min_length=1, max_length=400)
    name: str | None = Field(default=None, max_length=200)
    value: str | None = Field(default=None, max_length=2000)
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = False


class ServiceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_\-]+$")
    action: Literal["query", "start", "stop"] = "query"
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = True


class TaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    action: Literal["query", "create", "delete", "run"] = "query"
    command: str | None = Field(default=None, max_length=500)
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = True


class FirewallArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    action: Literal["list", "add", "delete"] = "list"
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = True


class InstallArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: str = Field(min_length=1, max_length=200)
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = True


class EnvironmentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str = Field(min_length=0, max_length=2000)
    scope: Literal["user", "machine"] = "user"
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = False


class CredentialRefArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    purpose: str | None = Field(default=None, max_length=500)


class VolumeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str = Field(min_length=1, max_length=80)
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = True


class ShutdownArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["shutdown", "reboot"] = "shutdown"
    delay_seconds: int = Field(default=120, ge=30, le=600)
    purpose: str | None = Field(default=None, max_length=500)
    elevate: bool = True


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
                    "reference",
                    "available",
                    "executed",
                    "armed",
                )
                if k in payload
            },
        )


def _guard_paths(args, roots):
    del roots
    for key in ("path", "source", "destination", "root", "cwd"):
        value = getattr(args, key, None)
        if value:
            assert_local_path(value)
    for item in getattr(args, "paths", None) or []:
        assert_local_path(item)
    executable = getattr(args, "executable", None)
    if executable and len(executable) >= 2 and executable[1] == ":":
        assert_local_path(executable)
    if getattr(args, "hive", None) is not None and getattr(args, "key", None):
        assert_registry_key(args.hive, args.key)


def register_local_tools(registry):
    provider = LocalDeviceProvider("local_fs")
    process = LocalDeviceProvider("local_process")
    info = LocalDeviceProvider("local_info")
    registry_p = LocalDeviceProvider("local_registry")
    service = LocalDeviceProvider("local_service")
    install = LocalDeviceProvider("local_install")
    system = LocalDeviceProvider("local_system")
    creds = LocalDeviceProvider("local_credential")
    for name, schema, capability, risk, description, adapter in (
        (
            "list_directory",
            PathArgs,
            "local_fs",
            RiskLevel.READ,
            "List a local directory on this computer.",
            provider,
        ),
        (
            "read_file",
            PathArgs,
            "local_fs",
            RiskLevel.READ,
            "Read a UTF-8 file on this computer.",
            provider,
        ),
        (
            "write_file",
            WriteArgs,
            "local_fs",
            RiskLevel.NORMAL_CHANGE,
            "Atomically write a file on this computer.",
            provider,
        ),
        (
            "create_directory",
            PathArgs,
            "local_fs",
            RiskLevel.NORMAL_CHANGE,
            "Create a directory on this computer.",
            provider,
        ),
        (
            "copy_file",
            CopyArgs,
            "local_fs",
            RiskLevel.NORMAL_CHANGE,
            "Copy a file on this computer.",
            provider,
        ),
        (
            "move_file",
            CopyArgs,
            "local_fs",
            RiskLevel.NORMAL_CHANGE,
            "Move a file on this computer.",
            provider,
        ),
        (
            "search_files",
            SearchArgs,
            "local_fs",
            RiskLevel.READ,
            "Search file names under a local directory.",
            provider,
        ),
        (
            "delete_file",
            PathArgs,
            "local_fs",
            RiskLevel.SENSITIVE,
            "Delete a single file on this computer.",
            provider,
        ),
        (
            "delete_directory",
            PathArgs,
            "local_fs",
            RiskLevel.SENSITIVE,
            "Delete a directory tree on this computer.",
            provider,
        ),
        (
            "mass_delete",
            MassDeleteArgs,
            "local_fs",
            RiskLevel.CRITICAL,
            "Delete several paths in one irreversible operation.",
            provider,
        ),
        (
            "run_process",
            ProcessArgs,
            "local_process",
            RiskLevel.NORMAL_CHANGE,
            "Run executable + argv. Never shell=True. elevate=true shows Windows UAC.",
            process,
        ),
        (
            "run_powershell",
            InterpreterArgs,
            "local_process",
            RiskLevel.NORMAL_CHANGE,
            "Run PowerShell with explicit argv.",
            process,
        ),
        (
            "run_python",
            InterpreterArgs,
            "local_process",
            RiskLevel.NORMAL_CHANGE,
            "Run Python with explicit argv.",
            process,
        ),
        (
            "process_status",
            ProcessIdArgs,
            "local_info",
            RiskLevel.READ,
            "Status of an Alex-started process.",
            info,
        ),
        (
            "stop_process",
            ProcessIdArgs,
            "local_info",
            RiskLevel.NORMAL_CHANGE,
            "Stop only the Alex Job Object for this tool_run_id.",
            info,
        ),
        (
            "get_system_info",
            EmptyArgs,
            "local_info",
            RiskLevel.READ,
            "Non-identifying local host summary. No hostname, MAC, or hardware serials.",
            info,
        ),
        (
            "registry_read",
            RegistryArgs,
            "local_registry",
            RiskLevel.READ,
            "Read an HKCU or HKLM registry value.",
            registry_p,
        ),
        (
            "registry_write",
            RegistryArgs,
            "local_registry",
            RiskLevel.SENSITIVE,
            "Write an HKCU or HKLM registry value. HKLM requires elevation.",
            registry_p,
        ),
        (
            "windows_service_status",
            ServiceArgs,
            "local_service",
            RiskLevel.READ,
            "Query a Windows service.",
            service,
        ),
        (
            "windows_service_control",
            ServiceArgs,
            "local_service",
            RiskLevel.SENSITIVE,
            "Start or stop a Windows service. Shows UAC.",
            service,
        ),
        (
            "scheduled_task",
            TaskArgs,
            "local_service",
            RiskLevel.SENSITIVE,
            "Query or change a scheduled task.",
            service,
        ),
        (
            "firewall_rule",
            FirewallArgs,
            "local_service",
            RiskLevel.SENSITIVE,
            "List or change a Windows firewall rule.",
            service,
        ),
        (
            "install_software",
            InstallArgs,
            "local_install",
            RiskLevel.SENSITIVE,
            "Install a package with winget after confirmation and UAC.",
            install,
        ),
        (
            "uninstall_software",
            InstallArgs,
            "local_install",
            RiskLevel.SENSITIVE,
            "Uninstall a package with winget after confirmation and UAC.",
            install,
        ),
        (
            "set_environment",
            EnvironmentArgs,
            "local_system",
            RiskLevel.SENSITIVE,
            "Set a persistent user or machine environment variable.",
            system,
        ),
        (
            "credential_list",
            EmptyArgs,
            "local_credential",
            RiskLevel.READ,
            "List logical credential references stored on this computer. No secrets.",
            creds,
        ),
        (
            "credential_use",
            CredentialRefArgs,
            "local_credential",
            RiskLevel.SENSITIVE,
            "Use a local credential by reference such as github-main. Raw secret stays on the host.",
            creds,
        ),
        (
            "format_volume",
            VolumeArgs,
            "local_system",
            RiskLevel.CRITICAL,
            "Format a volume. Irreversible. Allow once only.",
            system,
        ),
        (
            "manage_partition",
            VolumeArgs,
            "local_system",
            RiskLevel.CRITICAL,
            "Create, delete, or resize a partition. Irreversible. Allow once only.",
            system,
        ),
        (
            "boot_config",
            VolumeArgs,
            "local_system",
            RiskLevel.CRITICAL,
            "Change boot configuration. Allow once only.",
            system,
        ),
        (
            "bitlocker_change",
            VolumeArgs,
            "local_system",
            RiskLevel.CRITICAL,
            "Change BitLocker or recovery settings. Allow once only.",
            system,
        ),
        (
            "system_shutdown",
            ShutdownArgs,
            "local_system",
            RiskLevel.CRITICAL,
            "Shut down or reboot when work may be lost. Allow once only.",
            system,
        ),
    ):
        registry.register(
            ToolDefinition(name, description, schema, capability, risk, "free", 120, "local_device"),
            adapter,
        )

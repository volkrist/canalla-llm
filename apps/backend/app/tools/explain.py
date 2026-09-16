from .contracts import RiskLevel

CONSEQUENCES = {
    RiskLevel.READ: "Будут прочитаны данные. Изменений не будет.",
    RiskLevel.NORMAL_CHANGE: "Будет создан, изменён или запущен объект. Это можно отменить вручную.",
    RiskLevel.SENSITIVE: "Изменение может быть трудно отменить. Возможны потеря данных или изменение системы.",
    RiskLevel.CRITICAL: "Операция необратима или затрагивает загрузку, диск либо питание компьютера.",
}

ELEVATED_TOOLS = {
    "windows_service_control",
    "scheduled_task",
    "firewall_rule",
    "install_software",
    "uninstall_software",
    "format_volume",
    "manage_partition",
    "boot_config",
    "bitlocker_change",
    "system_shutdown",
    "set_environment",
    "start_service",
    "stop_service",
    "restart_service",
    "change_service_settings",
}


def target_of(args) -> str:
    if args is None:
        return ""
    for key in (
        "path",
        "source",
        "destination",
        "root",
        "executable",
        "name",
        "reference",
        "package",
        "device",
        "key",
        "cwd",
        "pid",
        "rev",
        "remote",
        "branch",
    ):
        value = getattr(args, key, None)
        if value:
            hive = getattr(args, "hive", None)
            return f"{hive}\\{value}" if hive and key == "key" else str(value)
    paths = getattr(args, "paths", None)
    if paths:
        return "; ".join(str(item) for item in paths[:8])
    return getattr(args, "action", None) or ""


def explanation(definition, args=None, risk=None) -> dict[str, str]:
    from .policy import effective_risk

    purpose = (getattr(args, "purpose", None) or "").strip() if args is not None else ""
    elevate = bool(getattr(args, "elevate", False)) if args is not None else False
    hive = getattr(args, "hive", None) if args is not None else None
    if definition.name in ELEVATED_TOOLS or hive == "HKLM":
        elevate = True
    target = target_of(args)
    action = definition.description
    if definition.name.startswith("delete") and target:
        action = f"удалить {target}"
    level = risk or effective_risk(definition, args)
    return {
        "reason": purpose or "Модель запросила это действие для текущей задачи.",
        "action_detail": action,
        "target": target,
        "consequences": CONSEQUENCES[level],
        "risk_level": level.value,
        "elevation_required": "yes" if elevate else "no",
    }

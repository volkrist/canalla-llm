export type WebMode = "off" | "auto" | "on";
export type TorMode = "off" | "auto" | "on";
export type ComputerMode = "off" | "ask" | "trusted";
export interface ToolRun {
  id: string;
  chat_id: string;
  generation_id: string | null;
  tool_name: string;
  provider: string;
  status: string;
  risk_level: string;
  input_summary: Record<string, string>;
  cost_estimate: number | string | null;
  cost_actual: number | string | null;
  result_metadata: Record<string, unknown>;
  error_code: string | null;
  origin?: string;
}
export const WEB_TOOLS = new Set(["web_search", "web_fetch"]);
export const TOR_TOOLS = new Set(["tor_search", "tor_fetch", "tor_browser"]);
export const COMPUTER_TOOLS = new Set([
  "list_directory",
  "read_file",
  "write_file",
  "patch_file",
  "create_directory",
  "copy_file",
  "move_file",
  "search_files",
  "search_code",
  "delete_file",
  "delete_directory",
  "mass_delete",
  "run_process",
  "run_powershell",
  "run_python",
  "process_status",
  "stop_process",
  "get_system_info",
  "list_processes",
  "inspect_process",
  "list_volumes",
  "list_installed_software",
  "registry_read",
  "registry_write",
  "read_registry",
  "write_registry",
  "delete_registry_value",
  "delete_registry_key",
  "windows_service_status",
  "windows_service_control",
  "query_service",
  "start_service",
  "stop_service",
  "restart_service",
  "change_service_settings",
  "scheduled_task",
  "firewall_rule",
  "install_software",
  "uninstall_software",
  "set_environment",
  "credential_list",
  "credential_use",
  "format_volume",
  "manage_partition",
  "boot_config",
  "bitlocker_change",
  "system_shutdown",
  "git_status",
  "git_diff",
  "git_log",
  "git_show",
  "git_branch",
  "git_add",
  "git_commit",
  "git_restore",
  "git_push",
  "git_reset",
]);
export const FILE_CHANGE_TOOLS = new Set([
  "write_file",
  "patch_file",
  "copy_file",
  "move_file",
  "create_directory",
  "delete_file",
  "delete_directory",
  "git_restore",
]);
export const toolStates: Record<string, string> = {
  planning: "Планирование",
  searching: "Поиск в интернете",
  reading: "Чтение источников",
  running_agent: "Выполнение web-задачи",
  waiting_confirmation: "Ожидание подтверждения",
  waiting_host: "Выполнение на компьютере",
  approved: "Подтверждено",
  browser_working: "Работа браузера",
  finishing: "Подготовка ответа",
  running: "Выполняется",
  completed: "Завершено",
  stopped: "Остановлено",
  failed: "Ошибка инструмента",
  unavailable: "Web недоступен",
};
export const toolErrors: Record<string, string> = {
  pricing_not_confirmed:
    "Цена Search/Fetch не подтверждена. Обратитесь к администратору.",
  agent_read_only_boundary_unavailable:
    "Agent API не предоставляет техническое ограничение read-only. Запуск заблокирован; используйте Search/Fetch или Browser с подтверждением действий.",
  provider_not_configured: "TinyFish API не настроен. Чат работает без web.",
  provider_auth: "Не удалось авторизовать TinyFish.",
  billing_required: "TinyFish отклонил запрос по условиям оплаты.",
  rate_limited: "Достигнут лимит запросов TinyFish.",
  provider_timeout: "Истекло время ожидания TinyFish.",
  provider_unavailable: "TinyFish временно недоступен.",
  unsafe_url: "Разрешены только публичные HTTP/HTTPS-адреса.",
  unsafe_redirect: "Источник перенаправляет на недопустимый адрес.",
  tool_disabled: "Инструмент выключен в настройках.",
  daily_budget: "Дневной бюджет инструментов исчерпан.",
  run_budget: "Достигнут бюджет запуска.",
  sensitive_arguments: "Передача секретов в инструменты запрещена.",
  agent_side_effect_not_supported:
    "Agent поддерживает только чтение. Для действий используйте Browser с отдельным подтверждением.",
  credentials_not_supported:
    "Ввод паролей и платёжных данных не поддерживается.",
  model_tools_unsupported: "Эта модель не поддерживает вызовы инструментов.",
  timeout: "Достигнут лимит времени инструмента.",
  tor_not_configured: "Tor не настроен.",
  tor_unavailable: "Tor недоступен.",
  tor_search_not_configured: "Tor Search provider not configured",
  tor_search_failed: "Tor Search did not return results",
  tor_browser_disabled: "Автоматизация Tor Browser выключена.",
  tor_browser_not_installed: "Tor Browser не найден.",
  tor_browser_not_ready: "Tor Browser automation недоступна.",
  download_blocked: "Загрузка файлов через Tor Browser заблокирована.",
  computer_disabled: "Режим компьютера выключен.",
  host_offline: "Локальный компьютер недоступен.",
  path_denied: "Путь недоступен или запрещён.",
  secret_path: "Чтение этого файла запрещено.",
  confirmation_denied: "Действие отклонено.",
  confirmation_mismatch: "Payload изменился. Нужно новое подтверждение.",
  credentials_stay_on_host: "Секрет остаётся на локальном компьютере.",
  critical_not_armed: "Критическое действие подготовлено и не выполнено.",
  uac_declined: "Пользователь отклонил запрос UAC.",
  conflict: "Файл изменился после чтения. Нужно прочитать снова.",
  git_not_installed: "Git не установлен на этом компьютере.",
  task_file_limit: "Достигнут лимит изменённых файлов в задаче.",
  file_size_limit: "Файл слишком большой для этой операции.",
  process_timeout_limit: "Превышен лимит времени процесса.",
};
export function isPendingConfirmation(run: ToolRun) {
  return run.status === "waiting_confirmation";
}
export function familyOf(run: ToolRun) {
  if (WEB_TOOLS.has(run.tool_name)) return "web";
  if (TOR_TOOLS.has(run.tool_name)) return "tor";
  if (COMPUTER_TOOLS.has(run.tool_name) || run.provider === "local_device")
    return "computer";
  return "other";
}

export function networkLabel(run: ToolRun) {
  const value = run.result_metadata?.network;
  if (value === "tor") return "Network: Tor";
  if (value === "direct") return "Network: Direct";
  return "";
}
export function groupStatus(runs: ToolRun[]) {
  if (
    runs.some((run) =>
      [
        "planning",
        "searching",
        "reading",
        "running",
        "waiting_host",
        "browser_working",
        "running_agent",
      ].includes(run.status),
    )
  )
    return "Выполняется";
  if (runs.some((run) => run.status === "failed")) return "Ошибка";
  if (runs.some((run) => run.status === "stopped")) return "Остановлено";
  if (runs.length && runs.every((run) => run.status === "completed"))
    return "Завершено";
  return toolStates[runs.at(-1)?.status || ""] || "Выполняется";
}
export function summarizeFamily(
  family: "web" | "tor" | "computer",
  runs: ToolRun[],
) {
  const status = groupStatus(runs);
  if (family === "web") {
    const searches = runs.filter(
      (run) => run.tool_name === "web_search",
    ).length;
    const fetches = runs.filter((run) => run.tool_name === "web_fetch").length;
    return `Веб · ${runs.length} запросов · ${searches} Search · ${fetches} Fetch · ${status}`;
  }
  if (family === "tor") {
    const searches = runs.filter(
      (run) => run.tool_name === "tor_search",
    ).length;
    const fetches = runs.filter((run) => run.tool_name === "tor_fetch").length;
    const browsers = runs.filter(
      (run) => run.tool_name === "tor_browser",
    ).length;
    return `Tor · ${runs.length} действий · ${searches} Search · ${fetches} Fetch · ${browsers} Browser · ${status}`;
  }
  const changed = runs.filter(
    (run) => FILE_CHANGE_TOOLS.has(run.tool_name) && run.status === "completed",
  ).length;
  return `Компьютер · ${runs.length} действий · ${changed} файлов изменено · ${status}`;
}
export function canonicalUrl(value: string) {
  try {
    const url = new URL(value);
    url.hash = "";
    url.hostname = url.hostname.replace(/\.$/, "").toLowerCase();
    if (
      (url.protocol === "https:" && url.port === "443") ||
      (url.protocol === "http:" && url.port === "80")
    )
      url.port = "";
    const drop = new Set([
      "utm_source",
      "utm_medium",
      "utm_campaign",
      "fbclid",
      "gclid",
    ]);
    [...url.searchParams.keys()].forEach((key) => {
      if (drop.has(key.toLowerCase()) || key.toLowerCase().startsWith("utm_"))
        url.searchParams.delete(key);
    });
    let path = url.pathname || "/";
    if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
    url.pathname = path;
    return url.toString();
  } catch {
    return value.trim();
  }
}
export function dedupeSources<T extends { final_url?: string; url?: string }>(
  sources: T[],
) {
  const seen = new Set<string>();
  return sources.filter((source) => {
    const key = canonicalUrl(source.final_url || source.url || "");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

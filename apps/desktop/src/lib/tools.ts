export type WebMode = "off" | "auto" | "on";
export type TorMode = "off" | "auto" | "on";
export type ComputerMode = "off" | "ask" | "trusted";
export interface TaskStep {
  id: string;
  title: string;
  description: string;
  status: string;
  tool_category: string;
  verification_required: boolean;
  attempts: number;
  result_summary: string;
  key: string;
}
export interface AutonomousTask {
  id: string;
  title: string;
  status: string;
  chat_id: string | null;
  workspace: string;
  current_step: string;
  current_phase: string;
  plan_revision: number;
  tool_calls_used: number;
  tool_budget: number;
  files_changed: number;
  file_change_budget: number;
  elapsed_runtime: number;
  runtime_budget: number;
  last_error: string | null;
  completion_summary: string | null;
  message: string;
  queue_position?: number | null;
  promoted_from_queue?: boolean;
  steps: TaskStep[];
  events?: {
    id: string;
    kind: string;
    payload: Record<string, string>;
    created_at: string;
  }[];
}
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
export const WEB_TOOLS = new Set([
  "web_search",
  "web_fetch",
  "web_agent",
  "web_agent_read",
  "web_browser",
  "browser_start",
  "browser_read",
  "browser_write",
]);
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
  "get_known_folders",
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
  "inspect_form",
  "fill_form_field",
  "submit_form",
  "inspect_product",
  "checkout_purchase",
  "email_send",
  "message_send",
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
  paid_tool_not_selected:
    "Платный TinyFish-инструмент не выбран политикой сервера.",
  credentials_not_supported:
    "Ввод паролей и платёжных данных не поддерживается.",
  model_tools_unsupported: "Эта модель не поддерживает вызовы инструментов.",
  timeout: "Достигнут лимит времени инструмента.",
  tor_not_configured: "Tor не настроен.",
  tor_unavailable: "Tor недоступен.",
  tor_route_unsupported:
    "Этот инструмент не умеет работать через локальный Tor. Используйте Tor Search или Tor Fetch.",
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
  task_budget: "Исчерпан бюджет вызовов инструментов.",
  task_runtime_limit: "Исчерпан лимит времени задачи.",
  task_paused: "Задача приостановлена.",
  task_stopped: "Задача остановлена.",
  workspace_busy: "Workspace занят другой задачей.",
  waiting_workspace: "Workspace занят. Задача в очереди.",
  git_commit_not_requested:
    "Commit не запрошен. Включите auto-commit или попросите commit.",
  git_commit_unverified: "Commit возможен только после проверки изменений.",
  git_push_not_requested:
    "Push не запрошен. Включите allow-push или попросите push.",
  git_push_unverified: "Push возможен только после проверки изменений.",
  email_not_configured: "Почтовый провайдер не настроен.",
  message_not_configured: "Провайдер сообщений не настроен.",
  invalid_task_transition: "Недопустимый переход состояния задачи.",
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
    const agents = runs.filter((run) =>
      ["web_agent", "web_agent_read"].includes(run.tool_name),
    );
    const browsers = runs.filter((run) =>
      [
        "web_browser",
        "browser_start",
        "browser_read",
        "browser_write",
      ].includes(run.tool_name),
    );
    const agentSteps = agents.reduce(
      (sum, run) => sum + Number(run.result_metadata?.steps || 0),
      0,
    );
    const agentCost = agents.reduce(
      (sum, run) => sum + Number(run.cost_estimate || 0),
      0,
    );
    const browserCost = browsers.reduce(
      (sum, run) => sum + Number(run.cost_estimate || 0),
      0,
    );
    const browserSeconds = browsers.reduce(
      (sum, run) => sum + Number(run.result_metadata?.duration_seconds || 0),
      0,
    );
    const parts = [
      `Веб · ${runs.length} запросов · ${searches} Search · ${fetches} Fetch · ${status}`,
    ];
    if (agents.length)
      parts.push(
        `TinyFish Agent · ${agentSteps} steps · ~$${agentCost.toFixed(3)}`,
      );
    if (browsers.length) {
      const minutes = Math.floor(browserSeconds / 60);
      const seconds = Math.round(browserSeconds % 60);
      const clock = browserSeconds ? ` · ${minutes}m ${seconds}s` : "";
      parts.push(`TinyFish Browser${clock} · ~$${browserCost.toFixed(3)}`);
    }
    return parts.join(" · ");
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

export function publicAssistantText(text: string) {
  const value = String(text || "");
  const cut = value.search(
    /<tool_call\b|<\/?function(?:_call)?\b|```(?:json|xml|tool)?\s*\{\s*"tool_calls"/i,
  );
  if (cut < 0) return value;
  return value.slice(0, cut).trim();
}

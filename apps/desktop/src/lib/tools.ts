export type WebMode = "off" | "auto" | "on";
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
}
export const toolStates: Record<string, string> = {
  planning: "Планирование",
  searching: "Поиск в интернете",
  reading: "Чтение источников",
  running_agent: "Выполнение web-задачи",
  waiting_confirmation: "Ожидание подтверждения",
  approved: "Подтверждено",
  browser_working: "Работа браузера",
  finishing: "Подготовка ответа",
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
};

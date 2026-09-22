"""Stable gateway error codes.

Every failure leaves the service as one envelope: ``{"detail", "code", "request_id"}``.
``detail`` is human-readable (the client may show it verbatim), ``code`` is the stable
machine contract. Provider failures keep the provider's own code so the client's existing
Russian error table still applies; everything else is a gateway code from this module.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

# Gateway codes with their default HTTP status. Adding a code here is a contract change:
# clients map unknown codes to their generic unavailable message.
CODES = {
    "gateway_invalid_request": 400,
    "gateway_auth_failed": 401,
    "gateway_protocol_mismatch": 409,
    "installation_revoked": 403,
    "installation_unknown": 401,
    "activation_code_rejected": 400,
    "activation_code_expired": 400,
    "activation_code_used": 409,
    "gateway_rate_limited": 429,
    "gateway_queue_full": 429,
    "gateway_busy": 503,
    "gateway_unavailable": 503,
    "gateway_budget_denied": 409,
    "compute_policy_invalid": 422,
    "gateway_request_in_flight": 409,
    "gateway_request_completed": 409,
    "compute_offline": 409,
    "compute_unknown": 409,
    "multiple_compute": 409,
    "external_compute": 409,
    "not_configured": 503,
    "malformed_response": 502,
    "gateway_internal_error": 500,
}

DETAILS = {
    "gateway_invalid_request": "Некорректный запрос к Canalla Cloud.",
    "gateway_auth_failed": "Не удалось авторизовать установку Canalla в Canalla Cloud.",
    "gateway_protocol_mismatch": "Версия Canalla Cloud несовместима с этой установкой Canalla.",
    "installation_revoked": "Эта установка Canalla отключена от Canalla Cloud.",
    "installation_unknown": "Установка Canalla не зарегистрирована в Canalla Cloud.",
    "activation_code_rejected": "Код активации не принят.",
    "activation_code_expired": "Срок действия кода активации истёк.",
    "activation_code_used": "Код активации уже использован.",
    "gateway_rate_limited": "Слишком много запросов. Повторите позже.",
    "gateway_queue_full": "Очередь запросов AI заполнена. Повторите позже.",
    "gateway_busy": "AI занят другим запросом. Повторите позже.",
    "gateway_unavailable": "Canalla Cloud сейчас недоступен.",
    "gateway_budget_denied": "Запрос превышает установленные лимиты расходов.",
    "compute_policy_invalid": "Недопустимая политика расходов: проверьте максимум $/час и бюджет сессии.",
    "gateway_request_in_flight": "Этот запрос уже выполняется.",
    "gateway_request_completed": "Этот запрос уже завершён.",
    "compute_offline": "AI не запущен. Запустите его перед запросом.",
    "compute_unknown": "Результат запуска AI пока неизвестен. Выполняется сверка с провайдером.",
    "multiple_compute": "Обнаружено несколько compute для этого аккаунта. Требуется проверка оператора.",
    "external_compute": "Обнаружен существующий compute, созданный не Canalla Cloud.",
    "not_configured": "Canalla Cloud не настроен на сервере.",
    "malformed_response": "Провайдер вернул неподдерживаемый ответ. Операция остановлена.",
    "gateway_internal_error": "Внутренняя ошибка Canalla Cloud.",
}


class GatewayError(Exception):
    def __init__(self, code: str, status: int | None = None, detail: str | None = None):
        self.code = code
        self.status = status or CODES.get(code, 400)
        self.detail = detail or DETAILS.get(code) or DETAILS["gateway_internal_error"]
        super().__init__(self.detail)


def error_body(code: str, detail: str | None = None, request_id: str | None = None) -> dict:
    return {
        "detail": detail or DETAILS.get(code) or DETAILS["gateway_internal_error"],
        "code": code,
        "request_id": request_id,
    }


def error_response(error: GatewayError, request_id: str | None = None) -> JSONResponse:
    return JSONResponse(status_code=error.status, content=error_body(error.code, error.detail, request_id))


async def gateway_error_handler(request: Request, error: GatewayError) -> JSONResponse:
    return error_response(error, request_id=getattr(request.state, "request_id", None))

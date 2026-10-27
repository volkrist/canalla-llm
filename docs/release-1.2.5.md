# Canalla LLM 1.2.5

Патч-релиз 27.10.2026. Закрывает дефекты, найденные аудитом после 1.2.4:

- идентификатор устройства больше не меняется из-за истёкшего session-токена;
- `patch_file` требует `new_text` (пропуск → `invalid_arguments`, а не удаление кода);
- пустой ответ модели заменяется сводкой проверенных действий;
- первая строка потока переживает холодный prefill.

Live H2 (forced-error coding) — **PASS** с model-origin tool_calls и независимым pytest.

Ограничения: установленный updater E2E и чистая установка Windows — BLOCKED (нет
интерактивного стенда); T3/T4 live и long autonomy — BLOCKED; Ubuntu packaging в релиз не
входит. Authenticode отсутствует, updater-подпись — production key `9B328EFF111D1FB2`.

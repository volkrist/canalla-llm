# Canalla LLM 1.2.4

Публичный Windows x64 release. Закрывает последние live-гейты (H5, H6, T2) и подпись
updater; продукт стабилен для установки на Windows.

## Что исправлено

- **Stop AI останавливает и ответ, и оплачиваемый Pod.** Клиент сначала прерывает активную
  генерацию, затем останавливает shared compute. Gateway больше не ждёт завершения всего
  ответа, поэтому Pod не продолжает биллиться после нажатия Stop.
- **Честный статус после Stop.** Остановленный ответ больше не оставляет
  `AI Ready / generating` и stale `gateway_unavailable`; локальный stop-endpoint сохраняет
  авторитетный снимок Gateway. Пользователь видит `AI Waiting` на время остановки, затем
  `AI Off`.
- **Версия Gateway отвязана от версии desktop.** Отдельно деплоимая служба может отставать
  от desktop-релиза, не ломая проверку версий.
- **Release gate корректно проверяет настоящую Tauri-подпись:** outer base64, minisign
  `ED` (Ed25519 над BLAKE2b-512) и URL в сжатом NSIS через packaged executable.

## Live-приёмка Qwen

- **H5 PASS (model-origin):** `web_fetch` (example.com) → `h5_title.txt` =
  `Example Domain`; `h5_app/{__init__,core,test_core}.py` с `normalized()`;
  `pytest -q` → **3 passed**; `h5_report.txt` со ссылкой; финальный ответ сохранён.
  Код прошёл тесты с первого раза — отдельного шага «исправление ошибки» не потребовалось.
- **H6 PASS (model-origin):** ровно один `write_file` и один `run_python`, без дублей;
  локальный fault proxy прервал backend→Gateway на 6 с и восстановил; `h6_probe.py` →
  `H6_RECOVERED`; прогресс и финальный ответ сохранены.
- **H4 PASS:** `web_search` + 3×`web_fetch`, 8 реальных публичных источников, отчёт со
  ссылками и расхождениями.
- **T2 PASS:** Stop AI посреди реального stream (первый delta на 114.9 с); Pod подтверждён
  остановленным через 187.5 с от создания, `pods []`, reconnect отсутствует.
- **T5, T6, T7 PASS:** typed Web failure без выдуманного содержимого; отклонённый
  `run_python` без выполнения; реальный tool timeout.
- **T3/T4/T8 (free/installed):** relaunch без логина и с тем же устройством, recovery после
  падения sidecar, Computer/Tor ready, навигация UI. Live-перезапуск посреди платной задачи
  и live UI во время реального потока в этом релизе не выполнялись; это ограничение, а не PASS.
- Ранее подтверждённые S1–S7, M1–M7, H1–H3 и T1 не перепрогонялись.

## Артефакт

| | |
|---|---|
| Installer | `Canalla.LLM_1.2.4_x64-setup.exe` |
| Размер | `95 465 293` B |
| SHA256 | `8db307489f0b5f0da077158e50d329a57d4fcd2c64707c3f149a2196f9c8de7e` |
| Updater signature | `Canalla.LLM_1.2.4_x64-setup.exe.sig` · 424 B · SHA256 `ddcddf51c40aafa501bf32f1ea4f1f5b46378aa667745cd7e512b39b39130354` |
| Public key id | `9B328EFF111D1FB2` · Ed25519 over BLAKE2b-512 |
| Control sums | `SHA256SUMS-1.2.4.txt` |

## Гейты

- **CI: 7/7** (workflow `CI` на коммите тега `v1.2.4`).
- Backend **988 passed**, gateway **261**, frontend **313**, Rust **126**, Playwright **21**.
- `release-gate.py developer` — **9/9 PASS**; `verify-artifact` — **8/8 PASS**.
- Installed always-ready: **32/32 PASS** (launch, relaunch, Tor daemon kill/recovery,
  sidecar crash recovery, same `device_id`).
- Данные пользователя при установке сохранены; проверенный pre-install backup создан.

## Ограничения (честно)

- **Authenticode отсутствует.** SmartScreen может показать предупреждение; это не влияет
  на Tauri updater-подпись.
- **T3/T4/T8 live-варианты** (перезапуск UI посреди платной генерации, live UI во время
  реального потока) не выполнялись; бесплатные/установленные аналоги зелёные.
- **Ubuntu не собиралась и не тестировалась** в этом релизе; **чистая Windows VM** (O)
  не проверялась. Это не заявляется как PASS.
- **Автообновление:** installer и production `.sig` валидны; установленные 1.2.3/1.2.4
  доверяют ключу `9B328EFF111D1FB2`. Манифест обновления на production Gateway в этом
  релизе **не изменялся** (Gateway не трогали); публичный installer + `.sig` — артефакты
  релиза, а манифест — отдельное production-действие оператора.

## Установка

1. Скачайте `Canalla.LLM_1.2.4_x64-setup.exe`.
2. Проверьте SHA256 по `SHA256SUMS-1.2.4.txt` (и, при желании, `.sig` ключом
   `9B328EFF111D1FB2`).
3. Запустите установщик. Обновление поверх 1.2.x сохраняет данные, настройки, устройство и
   enrollment; перед обновлением рекомендуется backup.

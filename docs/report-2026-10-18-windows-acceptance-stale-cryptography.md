# Windows acceptance 1.2.1 — находка: остатки 1.2.0 ломают sidecar

Дата: 18.10.2026. Ветка `integration/canalla-1.2.1`, исправление в коммите `54a842d`.
Хост: Windows 11 Home 25H2 (build 26200), 8 GB RAM.

## Что проверялось

Реальная установка тестового установщика 1.2.1 (`Canalla LLM_1.2.1_x64-setup.exe`,
91 928 775 байт, SHA256 `24bc0a28e4b7f7252407be5aafcde239dfd02d7e7940d411bb35963f03fe8c29`,
`NotSigned`) поверх ранее установленной 1.2.0 на этом ПК.

## Результат установки

| Проверка | Результат |
|---|---|
| Defender при установке | **PASS** — 0 новых детекций, защита не отключалась |
| Версия клиента | **PASS** — `FileVersion`/`ProductVersion` = 1.2.1, запись удаления = 1.2.1 |
| Пользовательские данные | **PASS** — 53 файла / 193 995 488 Б, без изменений |
| Миграция БД | **PASS** — `0015 → 0016`, pre-upgrade backup создан |
| Backend sidecar | **FAIL** — процесс падает после миграции |

Полный traceback из `%LOCALAPPDATA%\Alex LLM\logs\backend.log`:

```
File "app\runtime_entry.py", line 304, in main
File "app\main.py", line 11, in <module>
File "app\context_builder.py", line 18, in <module>
File "app\personal.py", line 13, in <module>
File "app\security.py", line 4, in <module>
File "jwt\__init__.py", line 1, in <module>
File "jwt\api_jwk.py", line 8, in <module>
File "jwt\algorithms.py", line 36, in <module>
ImportError: cannot import name 'x509' from 'cryptography' (unknown location)
```

## Причина

1. В установленном дереве лежал **остаток от 1.2.0**:
   `sidecar\alex-backend\_internal\cryptography\hazmat\bindings\_rust.pyd` и
   `cryptography-50.0.1.dist-info`, с датами **26–27.09.2026**, тогда как файлы 1.2.1 — 14.10.2026.
   В каталоге не было `cryptography\__init__.py`, то есть пакет был **недособран**.
2. Tauri NSIS ставит новое дерево **поверх** старого. На этом ПК старый uninstall-энтри отсутствовал
   (Defender удалил `alex-llm.exe` 03.10 и запись удаления), поэтому прежняя установка не была
   корректно снята, и остаток пережил апгрейд.
3. PyJWT 2.15.0 в `jwt/algorithms.py` ловит **только `ModuleNotFoundError`**
   (`except ModuleNotFoundError: has_crypto = False`). Недособранный `cryptography` — это
   namespace-пакет, и `from cryptography import x509` даёт `ImportError`, который не ловится.
   На чистом CI-раннере каталога `cryptography` нет вообще, поэтому `ModuleNotFoundError`
   ловится и sidecar работает — именно поэтому `installer-build` был зелёным.

Проверка на этой машине: тот же установленный sidecar с окружением CI-смоука
(`ALEX_LLM_DATA_DIR` во временном каталоге, `APP_ENV=test`, `LLM_PROVIDER=mock`) падает так же,
то есть дефект не зависит от данных пользователя.

## Почему CI это пропустил

`installer-build` проверяет только **свежую** установку на чистом раннере. Сценария
«установка поверх предыдущей версии с оставшимися файлами» не было.

## Исправление (`54a842d`)

1. `apps/desktop/src-tauri/nsis/hooks.nsh` — `NSIS_HOOK_PREINSTALL` теперь удаляет
   продуктовые деревья `$INSTDIR\sidecar` и `$INSTDIR\runtime` перед распаковкой.
   Данные пользователя лежат в `%LOCALAPPDATA%\Alex LLM` и не затрагиваются; Credential Manager
   не затрагивается.
2. `scripts/build-backend-sidecar.ps1` — сборка отказывается штамповать бандл, в котором есть
   `_internal\cryptography` без `__init__.py` (недособранный пакет).
3. `.github/workflows/ci.yml` — новая регрессия: CI кладёт ровно такой устаревший файл в
   установленное дерево, ставит установщик повторно, проверяет, что файл удалён, и что sidecar
   после этого отвечает `/health`.

## Ограничения этого прогона

* Установка 1.2.0 на этом ПК была уже повреждена Defender (удалён `alex-llm.exe` и
  uninstall-энтри), поэтому «чистый» апгрейд 1.2.0 → 1.2.1 здесь не воспроизводим.
  Найденный дефект — реальный дефект устойчивости апгрейда, а не артефакт теста.
* Windows GUI-проверка (Computer/Tor/Connected/два региона) не выполнялась: до исправления
  sidecar приложение неработоспособно. После сборки исправленного установщика — повторить.
* Defender-детекции `Trojan:Win32/LummaStealer.DO!MTB` остаются необъяснёнными; false positive
  не объявляется. См. [defender-false-positive-investigation.md](defender-false-positive-investigation.md).

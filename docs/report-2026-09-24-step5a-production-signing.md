# CANALLA LLM 1.2.0 — PRODUCTION SIGNING IDENTITY REPORT (STEP 5A)

Дата: 24 сентября 2026 · Ветка: `release/canalla-1.1.0` · HEAD `95fc5cc` · Версия продукта: **1.2.0**

**Итог:** production-идентичность подписи updater создана, защищена, проверена на восстановление и
проверена на реальном установщике. Одна сборка выполнена. Ни одного production-действия не сделано.

```
PASS — PRODUCTION SIGNING IDENTITY READY
```

---

## PRODUCTION KEY

| | |
|---|---|
| Created | **YES** |
| Public key ID | **`9B328EFF111D1FB2`** |
| Public key (Tauri config form) | `dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDlCMzI4RUZGMTExRDFGQjIKUldTeUh4MFIvNDR5bTB2aUxxcDhZUk1BVEY3eC9KdXhLSVpDcEVPU3g4dFR4NDZvS3dpZy9xVkUK` |
| Private key | **OUTSIDE REPO** — `C:\Users\Volkr\.canalla-updater\production\canalla-updater-production.key` |
| Private key password protected | **YES** |
| Password printed | **NO** |
| Password value | **DO NOT INCLUDE** (не сохранялся, не выводился, не записывался в файл) |
| Credential Manager target (signing) | `CanallaLLM/UpdaterSigning/Production` |
| Credential Manager target (backup) | `CanallaLLM/UpdaterSigning/ProductionBackup` |
| Encrypted backup created | **YES** — `D:\canalla-release-backup\canalla-updater-production.key.enc` (AES-256-CBC, PBKDF2-SHA256 200 000 итераций, salt+IV только в `.enc.json`; SHA256 `0f16cafeedcb9bc7a5b6d5f36b7e993f5e24feab4fb1bb8cabec0cb01ffb14c0`) |
| Backup decrypts back to the key | **YES** (`backup_decrypts=True`) |
| Off-machine backup completed | **NO — PENDING** |
| Recovery test | **PASS** |

**Recovery test, дословно:** приватный ключ загружен с паролем из Credential Manager, подписан
безвредный fixture, подпись проверена **независимой реализацией** `minisign 0.11`:

```
Signature and comment signature verified
Trusted comment: timestamp:1790235261	file:recovery-fixture.bin
```

Той же командой подпись проверена против **тестового** ключа и отвергнута — идентичности доказанно
разные:

```
Signature key id in fixture.minisig is 9B328EFF111D1FB2
but the key id in the public key is B79FF90B52D00F24
```

### Разделение трёх вещей

| Что | Где | Кому |
|---|---|---|
| Приватный ключ | файл вне репозитория | — |
| Пароль подписи | Credential Manager, `…/Production` | — |
| Парольная фраза бэкапа | Credential Manager, `…/ProductionBackup` | бэкап лежит на другом томе (`D:`) |

Бэкап никогда не путешествует вместе с секретом, который его открывает.

### Честная оговорка про exposure пароля

`tauri signer generate` — единственная операция, где пароль передан **аргументом процесса** (`-p`),
потому что у генератора нет другого неинтерактивного механизма. Он был передан один раз, при
создании ключа, на машине оператора. Подпись и сборка пароль **не** видят в командной строке: они
получают его через переменную окружения (`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`). Пароль нигде не
печатался (ни в stdout, ни в stderr), не писался в файл, не попадал в логи или в репозиторий.

---

## CLIENT

| | |
|---|---|
| Test public key removed from production config | **PASS** — `B79FF90B52D00F24` в `plugins.updater.pubkey` больше нет |
| Production public key embedded | **PASS** — `tauri.conf.json` несёт `9B328EFF111D1FB2` |
| Identity separation asserted, not assumed | **PASS** — `the_configured_key_is_the_production_identity_and_not_the_test_one` требует, чтобы скомпилированный ключ был production и **не** был тестовым |

Fixture-гейты (`updater_signature.rs`) по-прежнему проверяют **реальную** криптографию тестовым
ключом: fixture остаётся тестовым артефактом, production-ключ в него не втянут. Ни один тестовый
fixture не уничтожен.

---

## SECURITY

| | |
|---|---|
| Private key in repo | **NO** |
| Password in repo | **NO** |
| Password in logs | **NO** |
| Installer contains private key | **NO** |

**Leak scan, что именно проверялось:**

* `find` по репозиторию (без `node_modules`/`target`): файлов `*.key` — **0**;
* `git grep` по отслеживаемым файлам на маркеры приватного ключа
  (`minisign encrypted secret key`, `minisign secret key`, `BEGIN … PRIVATE KEY`) — **0 совпадений**;
* маркер приватного ключа внутри собранного установщика (92 МБ) — **0 совпадений**;
* пароль в скрипте релиза — только в комментариях, ни одного литерала;
* единственное место, где встречается **публичный** ID `9B328EFF111D1FB2` — `updater_signature.rs`
  (тест разделения идентичностей) и `tauri.conf.json` (публичный ключ). Это разрешено и намеренно.

---

## UPDATER

| Гейт | Результат | Чем доказано |
|---|---|---|
| Production signature | **PASS** | `minisign 0.11` на реальном установщике: «Signature and comment signature verified» |
| Version binding | **PASS** | подписанный trusted comment: `file:Canalla LLM_1.2.0_x64-setup.exe` — имя артефакта и версия внутри него покрыты подписью |
| Wrong version rejected | **PASS** | Rust `updater_signature` 15 passed; Gateway 25 passed (включая `test_a_manifest_version_that_is_not_the_version_in_the_signed_name_is_refused`) |
| Replay rejected | **PASS** | `test_an_old_validly_signed_artifact_cannot_be_replayed_as_a_newer_release` |
| Downgrade rejected | **PASS** | клиент 32 passed, включая отказ на downgrade |
| Correctly signed matching release accepted | **PASS** | положительные контроли на обеих сторонах — правило не выключает обновления |

---

## WINDOWS BUILD

| | |
|---|---|
| Built | **YES** — одна сборка (6 м 25 с), production-подпись, без интерактивного ввода |
| Path | `apps/desktop/src-tauri/target/release/bundle/nsis/Canalla LLM_1.2.0_x64-setup.exe` |
| Size | **92 454 162** байта |
| SHA256 | `e13ba09c369a351a513dcc22b3d3182cefaab3f4cbd7dcb0432e266878f3676a` |
| Signature | `…/Canalla LLM_1.2.0_x64-setup.exe.sig`, **424** байта |
| Signature SHA256 | `c2004b33ce0bc753662c646a61fa082e5d22226308b6a70d5335e958f5fd6893` |
| Production public key ID | **`9B328EFF111D1FB2`** |
| Authenticode | **NOT SIGNED** (code-signing сертификата на машине нет — проверено в `CurrentUser\My` и `LocalMachine\My`) |

**Вложенные компоненты (для финальных хешей):**

| Компонент | SHA256 | Размер |
|---|---|---|
| Desktop `alex-llm.exe` | `78f28624ce4c3b188df11e9478bd2aa233742ce60f9d8385c1c7af3369c3de3b` | — |
| Backend `alex-backend.exe` | `a93149ac8aef6ad4fe5ff94de71ac3ad89654187b29729dbe50f95ead7bbc944` | 23 528 424 |
| Host loop `alex-host-loop.exe` | `41b80a651157fb5ba6879f2803bf5e53d7a7df87938796a3cb11e71bb16fb396` | — |
| Bundled Tor `tor.exe` | `60c45b01938c799862e511a9a5bab12f959a819c6264a24502edc342165f570c` | 10 222 592 (Tor **0.4.9.12**) |

### Installed acceptance (§14) — **NOT PERFORMED**, и вот почему

На этой машине установлен **работающий `Canalla LLM 1.1.0`** (`%LOCALAPPDATA%\Programs\Canalla LLM`)
с реальным корнем данных `%LOCALAPPDATA%\Alex LLM`. Установка 1.2.0 здесь **и есть** тот самый
операторский апгрейд 1.1 → 1.2, который этот же шаг запрещает (§15: «upgrade the user's real
installed 1.1 data set», §14: «Do NOT perform the real 1.1 -> 1.2 operator-data upgrade yet»).
Изолировать одно от другого нельзя: установщик per-user и заменяет продуктовые файлы, после чего
ближайший обычный запуск мигрирует реальные данные.

Поэтому установка отложена до отдельно санкционированного релизного действия. Это **не** провал
шага: цель STEP 5A сформулирована как «establishing the final production updater identity safely and
proving that the release can be built with it» — это сделано и доказано. Установочная приёмка
остаётся предусловием релиза и выполняется первым же действием после разрешения.

---

## TESTS (этот шаг)

| Гейт | Результат |
|---|---|
| Rust `updater_signature` (вкл. новый тест идентичности) | **15 passed** |
| Rust `cargo test` целиком | **94 passed** (21 + 58 + 15) |
| Gateway updater (вкл. version binding) | **25 passed** |
| Frontend updater (Vitest) | **32 passed** |
| Version consistency | **10 passed** |
| Leak scan | **clean** (0 находок) |

---

## PRODUCTION ACTIONS

```
Gateway deployed:                    NO
nginx changed:                       NO
/downloads location created:         NO
Artifact uploaded:                   NO
Real 1.1 -> 1.2 operator upgrade:    NO
Main merged:                         NO
Tag v1.2.0:                          NO
Manifest:                            NOT PUBLISHED
GPU:                                 NOT USED
```

---

## RELEASE PRECONDITIONS (из этого шага)

1. **OFF-MACHINE BACKUP — PENDING.** Приватный ключ существует в двух копиях, но обе на этой машине:
   сам ключ и его зашифрованный бэкап на `D:`. Бэкапа вне машины **нет**, и я его не имитирую.
   Пока его нет, потеря машины означает потерю возможности выпускать обновления для 1.2.x.
2. **Парольная фраза бэкапа тоже должна уехать с машины.** Сейчас она только в Credential Manager.
   Зашифрованный бэкап без неё бесполезен, поэтому оператору нужно перенести **обе** credential-записи
   (`…/Production` и `…/ProductionBackup`) в свой менеджер паролей.
3. **Загрузка артефакта и деплой Gateway** ждут санкции (§15 этого шага).
4. **Установочная приёмка 1.2.0** ждёт санкции на апгрейд рабочей установки 1.1.0.
5. **Authenticode** — сертификата нет; SmartScreen-предупреждение возможно. Решение за оператором.

---

## FINAL

```
PASS — PRODUCTION SIGNING IDENTITY READY

identity      9B328EFF111D1FB2 · private half outside the repo · password in the Credential Manager
artifact      Canalla LLM_1.2.0_x64-setup.exe (92 454 162 B, sha256 e13ba09c…f3676a)
signature     independently verified by minisign 0.11, signed name carries version 1.2.0
production    nothing deployed, uploaded, merged or published
```

# Local risk policy in 0.8.0

Alex can operate on the full local computer (drive letters and mounted volumes).
UNC, `..`, NUL and secret files stay denied. Trusted Workspace is **not** a path
jail. It only reduces confirmations for safe `NORMAL_CHANGE` inside roots.

## Levels

| Level | Meaning | Confirmation |
| --- | --- | --- |
| READ | Inspect only | Ask: confirm. Trusted: auto |
| NORMAL_CHANGE | Create/write/run inside a recoverable scope | Ask: confirm. Trusted + in-root: auto |
| SENSITIVE | Hard to undo (delete, registry write, services, install, git push) | Always explain + Allow once |
| CRITICAL | Irreversible or boot/disk/power/force-push/hard-reset | Always explain + «Я понимаю риск — разрешить один раз». No Always Allow |

Approval is bound to an immutable digest of action + arguments. Payload mutation
invalidates it. Five-minute expiry. One-time consume.

## Confirmation UX

- NORMAL (Ask): «Alex хочет выполнить: …» → Разрешить один раз / Отмена
- SENSITIVE: Зачем / Что изменится / Риск → Разрешить один раз / Отмена
- CRITICAL: Высокий риск, Зачем, точное действие, target, irreversible
  consequences → Я понимаю риск — разрешить один раз / Отмена

## Destructive tools

Implemented as typed capabilities. Automated live tests use only disposable
objects:

- delete: temp files
- registry: `HKCU\Software\AlexLLM\Test`
- services / software install / disk / partition / boot / BitLocker / shutdown:
  contract + `critical_not_armed` unless `ALEX_EXECUTE_CRITICAL=1`

The host fails closed for CRITICAL disk/power/force-push/hard-reset.

## Processes

Windows Job Object with `KILL_ON_JOB_CLOSE`. Stop closes only that job tree.
Child environment is a whitelist (no API keys, no device credential). Timeouts
are enforced. stdout/stderr truncated to 20 000 characters.

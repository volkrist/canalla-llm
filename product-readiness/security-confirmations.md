# Confirmations and security UX

Risk engine stays as implemented. This spec is about **what a person reads**.

**CURRENT VERIFIED FROM REPO**

Levels in `docs/local-risk-policy.md` / `tools/policy.py`:

| Level | Trusted workspace | Ask mode (today default) |
|---|---|---|
| READ | auto | confirm |
| NORMAL_CHANGE in-scope | auto | confirm |
| SENSITIVE | always | always |
| CRITICAL | always one-time; no Always Allow | always |

Allow-once is digest-bound, 5-minute expiry, one consume.
CRITICAL host often `critical_not_armed` unless `ALEX_EXECUTE_CRITICAL=1`.
Compute start has its own quote confirmation.
Git push SENSITIVE; force-push / hard-reset CRITICAL.
Fake checkout CRITICAL; loopback form submit SENSITIVE.
Agent side effects blocked before provider (not a confirmation).

1.0 keeps this engine. Change the **default Computer mode** so HIGH
autonomy matches Trusted, not Ask.

---

## Primary copy (good)

The card answers: who, what, to which object, what it costs/risks.

Good:

> Alex сейчас установит `jqlang.jq` (jq 1.8.2) для текущего пользователя
> через winget. Это можно удалить позже. Нужно ваше разрешение.

Good:

> Alex сейчас имитирует оплату на **локальном** тестовом магазине
> на сумму **$1.23**. Реальных денег нет. Разрешить один раз?

Bad (do not lead with):

> risk=CRITICAL digest=a0e277… tool=checkout_purchase

Technical digest may sit in «Подробности» for support.

Existing `explain.py` fields (`reason`, `action_detail`, `target`,
`consequences`) are the right data. 1.0 MUST stop showing `tool_name` as
the headline (`ToolActivity` still does).

CRITICAL button copy can stay:
«Я понимаю риск — разрешить один раз».

---

## When to ask

| Ask | Do not ask |
|---|---|
| SENSITIVE / CRITICAL | READ in 1.0 default |
| GPU session start if price unknown/changed | every subsequent message in that session |
| git push / allow_push off | git status, diff, log |
| install / delete / registry write / services | create folder, write in-project file |
| ambiguous target («удали всё») | «создай папку Demo на рабочем столе» |
| missing credential | inspect_form on loopback without submit |

If the model asks the user «should I inspect?» the **controller** should
not forward that as a confirmation. That is a planner bug
(**RECHECK AFTER 0.9.3**).

---

## GPU spend confirmation

Treat like SENSITIVE money, not like READ.

Once per new managed Pod (or price_changed): hourly rate, estimated idle
behavior, session cap. Not Pod JSON.

---

## Secrets

Never in cards, chat, diagnostics, or support bundle:

- API keys, JWT, cookies, CDP, website passwords, device credential,
  document full text by default

Confirmation payloads already deny secret arguments (`sensitive_arguments`,
`credentials_stay_on_host`). Keep fail-closed.

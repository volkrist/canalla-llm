# Observability, diagnostics, and support bundle

1.0 has two layers: a calm product surface, and one Advanced diagnostics
page for the owner / supporter.

**CURRENT VERIFIED FROM REPO:** ComputePanel `<details>` when
`technicalDetails` is on (backend healthy, RunPod, Pod id, active users).
Context preview similarly gated. No log dir, no export, no error inbox.
Message telemetry: TTFT / cancellation JSON only. README lists
observability as future work.

---

## Advanced diagnostics page

Possible fields (booleans and ids, not secrets):

| Block | Contents |
|---|---|
| App | version, OS, install path, data dir (not file contents) |
| Backend | healthy, worker count, db migrate head, uptime |
| AI | provider mock/llamacpp, model alias, ready, last error code |
| RunPod | configured yes/no, state, Pod ID, GPU name, hourly rate, session estimate, idle remaining, volume **id** |
| Computer | paired yes/no, display name, online, last job result code |
| Web | TinyFish configured yes/no, last tool error code, estimated paid this task |
| Tor | SOCKS reachable yes/no, Tor Browser detected yes/no |
| Task | id, status, phase, budgets used |
| Tools | recent name/status/duration/cost/source — **names OK here** |
| Errors | last N codes + timestamps |

Refresh live. No raw supplier bodies.

### Forbidden

API keys, JWT, cookies, CDP URLs, passwords, device credential,
full private documents, full chat transcript by default, `.env` dump.

---

## Logging

Target: `%LOCALAPPDATA%\Alex LLM\logs\` rotating files for backend + host.

Redact: query tickets (already `RedactTicket`), Authorization headers,
`SecretStr`, credential fields, cookie headers.

Developer console remains developer-only.

---

## Support bundle

User action: Advanced → «Экспорт диагностики».

Include:

- version, platform
- component statuses (the five chips + backend)
- sanitized logs (last N MB)
- task IDs, chat IDs (ids only)
- error codes
- compute session ids / Pod ids (not keys)
- feature flags that are not secrets (`LLM_PROVIDER`, idle minutes)

Exclude by default:

- JWT, API keys, `.env`
- website passwords, cookies
- private document **contents** and memory **bodies**
- message text (optional explicit checkbox: «приложить последние сообщения»
  — off by default)

Bundle is a zip on a user-chosen path (existing save dialog permission).

---

## Why this exists

User failure: “support asked me to paste logs and I pasted a RunPod key”
and “I cannot tell if GPU is still billing.”

# Product-facing status and progress

Technical internals stay in Advanced diagnostics (`observability.md`).
The main UI has five capabilities and a short progress verb.

---

## Status model

| Capability | Means to a person |
|---|---|
| **AI** | the language model that answers and plans |
| **Computer** | this Windows PC |
| **Web** | internet research / browser (Alex chooses the tool) |
| **Tor** | onion / Tor network |
| **Memory** | long-lived personal facts (not files, not this-task notes) |

### Values (shared)

| Value | When |
|---|---|
| Ready | usable now |
| Starting | Alex is bringing it up |
| Waiting | blocked on stock, queue, or a long health check |
| Needs confirmation | user must allow something |
| Unavailable | missing config, install, or network — work can continue without it if possible |
| Error | failed; see structured error |

Do not put these on the main bar: Pod ID, CUDA, SOCKS port, Marionette,
ToolRegistry, SSE, `origin=server_policy`, `Q5_K_M`, gateway port.

### Mapping from today (CURRENT VERIFIED FROM REPO)

| Today | Product chip |
|---|---|
| `ready` / mock available | AI: Ready |
| searching, creating, starting_*, loading_model | AI: Starting |
| `no_gpu` | AI: Waiting |
| `not_configured`, mock in a production build | AI: Unavailable |
| `error`, `startup_failed`, `connection_auth_failed` | AI: Error |
| Device Online + Trusted/Ask | Computer: Ready |
| pairing | Computer: Starting |
| Device Offline | Computer: Unavailable |
| TinyFish configured | Web: Ready (label **Auto**) |
| no API key | Web: Unavailable |
| Tor SOCKS up | Tor: Ready |
| SOCKS down / browser missing when needed | Tor: Unavailable / Error |
| Memory CRUD available | Memory: Ready |
| embeddings missing (files only) | Memory: Ready; Files retrieval Unavailable |

Composer today exposes Web/Tor Off-Auto-On and Computer Off-Ask-Trusted.
For 1.0 main UI:

- Show **Web: Auto** as status, not as a depth picker.
- Tor/Computer follow the same idea: status, not a research-depth radio.
- Off remains Advanced (privacy / airgap).
- Computer **Ask** must not be the default (see autonomy).

**MUST NOT ADD:** Low/Normal/High autonomy, Fast/Normal/Deep research.

---

## Progress (long tasks)

User-facing phase, one at a time:

| Phase | Example copy |
|---|---|
| Planning | «Составляю план…» |
| Working | «Работаю над шагом…» |
| Researching | «Ищу официальные источники…» / «Сверяю факты…» / «Проверяю результат…» |
| Editing | «Правирую файлы…» |
| Testing | «Запускаю проверки…» |
| Verifying | «Проверяю, что задача сделана…» |
| Waiting for confirmation | human confirmation card |
| Completed | result summary |

**CURRENT VERIFIED FROM REPO:** `toolStates` already has some Russian verbs
(Планирование, Поиск в интернете, …) but ToolActivity still shows `tool_name`,
and TaskPanel shows `tools 12 / 40` plus raw `task.status`.
`publicAssistantText` already strips `<tool_call` from the bubble — keep
and extend.

Never in the transcript:

- JSON / tool XML / provider payload / SSE frames / planner chain-of-thought
- «Tool call 13»

Advanced diagnostics may list: tool, provider, duration, status, cost, source.

---

## Deep research (RESEARCH_DEPTH = DEEP, always)

Deep = meaningful investigation: official sources, comparison, verification.
Deep ≠ endless loops, ≠ maxing every budget for a greeting.

Controller already bounds search/fetch. 1.0 UX:

1. No depth selector.
2. Progress uses the three research lines above, collapsing many fetches.
3. Stop when evidence is sufficient **or** a bound is hit — then say so:
   «Проверил основные источники. Дальше упираемся в лимит поиска.»
4. Paid TinyFish Agent/Browser: show **~$** when cost is material (today
   ToolActivity already estimates). Main chat: one line, not a ledger.

**RECHECK AFTER 0.9.3:** progress.py / grounding may already emit better
phases — remap to this vocabulary, do not add a second progress UI.

---

## High autonomy (AUTONOMY = HIGH, always)

Safe work proceeds. Questions only for:

- ambiguous **destructive** target
- SENSITIVE / CRITICAL
- missing credential
- genuine user choice (which repo, which account, spend above ceiling)

### Good

- «Создай папку на рабочем столе» → creates, reports path.
- Coding: inspect, test, patch, retest without «should I read the file?»
- Research: Search then Fetch without «should I look this up?»

### Bad over-questioning (today’s Ask default)

- «Разрешить list_directory?»
- «Should I run tests?»
- «Continue?»
- READ confirmations in Trusted-equivalent 1.0

### Bad reckless

- git push without ask
- install software without SENSITIVE card
- purchases, emails, HKLM, shutdown
- silent Tor→Direct
- silent second Pod

Computer 1.0 default: **Trusted Workspace** semantics (auto READ + in-scope
NORMAL_CHANGE). Keep Off in Advanced.

---

## Advanced diagnostics (what may appear)

Backend healthy, provider mock/llamacpp, Pod ID, GPU name, model alias,
context char budget, Computer pairing, Web/TinyFish configured (boolean),
Tor SOCKS reachable (boolean, not the password), current task id, tool
metrics, session estimate, last error **code**.

**Must never appear:** API keys, cookies, JWT, CDP URLs/credentials,
passwords, document bodies by default.

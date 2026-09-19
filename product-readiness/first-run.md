# First-run experience

**CURRENT VERIFIED FROM REPO (0.9.2):** first launch is a developer procedure,
not a product flow.

**Target:** a normal person installs Alex, opens it, and can chat. They never
open a terminal, edit `.env`, start uvicorn, start a Pod, copy an IP, or learn
llama.cpp / CDP / ToolRegistry.

---

## Current flow (failure)

```text
install Node, Python 3.12, Rust, VS Build Tools, WebView2
→ .\scripts\setup-backend.ps1
→ cd apps\desktop && npm ci
→ terminal A: start-backend.ps1
→ terminal B: start-desktop.ps1  (or unsigned alex-llm.exe + still need backend)
→ AuthScreen register
→ empty Workspace in MOCK MODE unless LLM_PROVIDER=llamacpp and compute started
→ optional: prepare embeddings, pair device (Tauri only), paste TinyFish/RunPod keys in .env
```

User-visible screens today:

1. Native window or Vite tab.
2. Login / Register (no wizard). Settings gear available.
3. Chat. Composer send disabled unless `health && llm.available`.
4. Offline note: «Backend недоступен. Проверьте адрес в Settings и запустите сервер.»

This is the opposite of 1.0.

---

## Target flow

```text
install (NSIS)
→ launch Alex
→ backend process starts (or is already a local service)
→ native host pairs
→ account/session ready (register once; later launches restore session)
→ capability statuses determined (AI / Computer / Web / Tor / Memory)
→ user types
→ if the model is required and GPU is down:
     Alex starts the appropriate RunPod GPU, waits for llama.cpp health,
     then continues the same message
→ user is in a conversation
```

No GPU start on idle launch. Start when the **task needs the model**, not
when the window opens. Mock remains a developer/degraded path, not the
default daily path for a configured product.

---

## Required product states

| State | User sees | Meaning |
|---|---|---|
| Installing | installer UI | files on disk |
| Starting Alex | «Запуск Alex…» | desktop + backend coming up |
| Connecting computer | «Подключение компьютера…» | host pair |
| Needs account | register / login | first machine only |
| Ready | chat composer enabled | at least chat pipeline alive |
| AI starting | «Запуск AI…» | Pod + llama.cpp, first real message |
| AI ready | AI: Ready | model health ok |
| Needs confirmation | confirmation card | SENSITIVE/CRITICAL/GPU-cost as defined |
| Degraded | specific unavailable chips | see `ux-states.md` |
| Error | structured error | see `error-recovery.md` |

Do not invent extra splash screens. Three are enough: Starting → Account
(if needed) → Chat.

---

## Screens

### 1. Launch gate (blocking, short)

Copy:

- «Запускаем Alex»
- sub: backend, computer, session — mapped to the five chips only if one is slow

Timeout: if backend is not healthy in N seconds → error
«Alex не смог запустить локальный сервис» + retry + Advanced diagnostics.

### 2. Account (first run only)

Keep existing Register / Login. After 1.0 session persistence, this screen
appears only when no valid local session exists.

Do not ask for backend URL. Loopback is automatic.

### 3. Main chat

Empty state can stay conversational («О чём поговорим?»).
Do not dump GPU forms, tool catalogs, or `.env` hints here.

First-message GPU start uses the compact AI chip + progress
«Запускаем AI на GPU…», not the ComputePanel modal unless the user opens
Advanced.

---

## Errors and recovery (first run only)

| Failure | What happened | Already tried | Safe? | Auto retry | User action |
|---|---|---|---|---|---|
| Backend binary missing | runtime not installed | launch | yes | no | reinstall (MUST installer) |
| Port 8000 busy | local service conflict | bind | yes | try next loopback port **or** attach if it is Alex | if foreign process: Advanced |
| JWT/db migrate fail | local data migrate | alembic on start | data maybe | no | support bundle |
| Host pair fail | Computer unavailable | retry 8s | yes | yes | continue chat without Computer |
| RunPod key missing | AI unavailable | detect | yes | no | operator must have pre-provisioned key in packaged config **or** a one-time owner paste in Advanced — never a `.env` tutorial |
| No GPU stock | AI waiting | search | yes | yes, armed search | wait / cancel task |
| Embedding missing | Memory files limited | none | yes | prepare on first attach | none for chat |

Never: «edit `.env`», «run PowerShell», «copy LLM URL».

---

## Acceptance criteria

1. A tester with **no repo clone** can install from the 1.0 artifact and send
   a normal question without opening a terminal.
2. Backend URL is not a first-run field.
3. Native host is paired without a “start host” instruction.
4. Session survives app restart on the same Windows user.
5. First chat that needs the model starts GPU automatically (if compute is
   configured) and continues the **same** user message after Ready.
6. If compute is not configured, the user gets **AI: Unavailable** with a
   true reason — chat does not pretend to be the production model (no silent
   mock in production builds).
7. First-run never downloads a GPU model onto the PC; volume data stays on
   the existing Network Volume.
8. No AUTONOMY / RESEARCH_DEPTH questions.

**CURRENT VERIFIED FROM REPO:** all of the above fail today except (8)
(selectors are absent).

**RECHECK AFTER 0.9.3:** none of these are 0.9.3 deliverables; still recheck
if reliability work starts a backend sidecar (unexpected).

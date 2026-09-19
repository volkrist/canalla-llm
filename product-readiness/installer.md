# Installer, zero-terminal, and settings

**Do not fix packaging in this branch.** Gap list only.

---

## CURRENT VERIFIED FROM REPO (0.9.2)

| Item | State |
|---|---|
| Desktop executable | `alex-llm.exe` via `npm run tauri build` |
| Installer | NSIS **only**, `installMode: currentUser`, unsigned |
| MSI | none |
| Backend | **not bundled**; separate Python venv |
| Native host | inside Tauri; extra `alex-host-loop` is E2E-only |
| Python 3.12 + lock | `setup-backend.ps1` |
| Playwright browsers | e2e `npx playwright install chromium`; not product |
| Tor Browser | assumed installed by user |
| Config | `apps/backend/.env` + desktop localStorage |
| Logs | uvicorn stdout |
| Upgrade | re-run scripts / replace exe; no updater plugin |
| Uninstall | NSIS default; data policy undocumented |
| Code signing | none (README) |

`scripts/build-desktop.ps1` only builds the UI host.
`scripts/start-backend.ps1` is mandatory for any real use.

---

## 1.0 installer MUST guarantee

User failure: “I installed a chat app and it cannot chat.”

1. One NSIS (or equivalent) artifact named Alex LLM.
2. Installs UI + backend runtime + migrations on first start.
3. Creates application data dir; generates JWT if missing; does not
   clobber existing secrets/DB.
4. Registers/starts so **double-click starts a working app**.
5. Pairs native host without extra downloads.
6. Does not require Node, Rust, VS Build Tools, or a git clone.
7. WebView2: use the bootstrapper or document OS requirement in the
   installer UI, not a README.
8. Uninstall offers keep vs delete `%LOCALAPPDATA%\Alex LLM` and the DB.
9. Upgrade preserves DB, settings, memory, projects, files, pairing.
10. Before uninstall/upgrade, stop managed GPU (best-effort).

**SHOULD:** Authenticode signature (otherwise SmartScreen trains users to
click through security). **LATER:** MSI, per-machine install, auto-update
channel.

Not in the installer:

- GPU weights (stay on Network Volume)
- Tor Browser binary (detect + guide)
- TinyFish/RunPod keys in the client
- Playwright for developers

---

## Zero-terminal inventory

| Current scenario | Class |
|---|---|
| `setup-backend.ps1` / pip / venv | **MUST fix before 1.0** |
| `start-backend.ps1` | **MUST fix** |
| `start-desktop.ps1` / `npm run dev` | **MUST fix** for normal use; **developer-only** acceptable for contributors |
| `npm ci`, Rust, VS Build Tools | **developer-only** |
| edit `.env` for JWT | **MUST** generate in-app |
| edit `.env` for `LLM_PROVIDER`, URLs, gateway | **MUST** be packaged defaults |
| paste `RUNPOD_API_KEY` / `TINYFISH_API_KEY` | **MUST** have a non-terminal owner path **or** be pre-baked in a private distribution; never a README ritual. **Advanced** UI paste is OK |
| `ADMIN_EMAILS` / `ALLOW_USER_COMPUTE_START` | **MUST** first local owner can start AI |
| `alembic upgrade` | **MUST** auto on backend start (script already does; product start must too) |
| `python -m app.documents.embedding` | **MUST** UI auto-prepare (UI exists; CLI must not be required) |
| `npx playwright install` | **developer-only** |
| Tor SOCKS / `TOR_BROWSER_EXE` | **SHOULD** detect + UI; manual service install of Tor may stay **advanced** |
| `ALEX_EXECUTE_CRITICAL=1` | **developer-only** (fail-closed is correct) |
| `ALEX_PYTHON`, `ALEX_DEVICE_DIR` | **developer-only** |
| RunPod dashboard / copy IP / llama.cpp CLI | **MUST never** be a user step |
| Git install | **SHOULD** Computer: Unavailable with install hint; **advanced** to fetch git |
| PowerShell ExecutionPolicy Bypass for scripts | **MUST disappear** with installer |
| CI / ruff / pytest | **developer-only** |

Normal user 1.0: **zero** PowerShell for install, launch, chat, research,
files, coding in a git repo that already has Git, pause/resume, close/reopen.

---

## Settings classification

**CURRENT VERIFIED FROM REPO** — classify in place; do not add autonomy/depth.

### USER-FACING

- Font size, theme
- Enter sends, auto-scroll, timestamps
- Profile display name, custom instructions
- Memory CRUD + use-memory toggles
- Projects CRUD
- Export chats / clear drafts
- Files upload/rename/delete
- Logout
- Compact AI cost chip (target; today buried in ComputePanel)
- Confirmation cards

### ADVANCED

- Backend URL
- Technical details toggle (keep as the door to diagnostics)
- GPU SKU, VRAM floor, search interval, auto_search
- Session budget, hourly cap, idle minutes
- Web Off (airgap), Agent/Browser ceilings
- Tor Off, Tor Browser Off
- Computer Off, workspace roots, device name, forget/rotate device
- auto_commit, allow_push
- RAG similarity / chunk caps
- Embedding retry/cancel
- Usage numbers beyond the chip
- Support bundle export (target)

### DEVELOPER

- `APP_ENV`, CORS, JWT length, mock delay
- `ALEX_EXECUTE_CRITICAL`, host env overrides
- Playwright, e2e ports
- OpenAPI `/docs`
- FastAPI version string
- Tool ceilings env (`TOOLS_*`)
- Multi-worker / PostgreSQL

### SHOULD BE AUTOMATIC

- Backend start/stop/migrate
- Host pairing
- Session restore
- GPU on-demand start + idle stop
- Embedding prepare on first file
- First user is owner (compute allowed)
- Web/Tor routing (Search vs Browser vs Agent)
- Computer Ask→Trusted default for HIGH autonomy
- Port selection if 8000 busy (Alex-owned)

### SHOULD BE REMOVED (from normal UI)

- Composer **Ask** as default
- Any future AUTONOMY / RESEARCH_DEPTH control — **forbidden**
- MOCK MODE as a proud badge in production (dev-only)
- Tool names as headlines
- Exact digest as headline
- Language stub that cannot be changed (either hide or implement later)

Web Off/Auto/On on the composer: collapse to status **Web: Auto** plus
optional «Найти в интернете». Off goes to Advanced.

---

## Confirm

- **AUTONOMY = HIGH** — fixed; no selector.
- **RESEARCH_DEPTH = DEEP** — fixed; no selector.

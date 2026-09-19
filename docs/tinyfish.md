# TinyFish adapters — 0.9.3

Production uses direct REST with httpx, never CLI subprocesses. CLI/MCP is developer tooling only. `BackendCredentialProvider` resolves the shared `SecretStr` `TINYFISH_API_KEY` from the backend environment. The UI receives a configured boolean only; keys, CDP URLs, cookies and website passwords are not stored in audit, context, schema or the frontend.

Official references: [Search](https://docs.tinyfish.ai/search-api/reference), [Fetch](https://docs.tinyfish.ai/fetch-api/reference), [Agent](https://docs.tinyfish.ai/agent-api/reference), [Browser](https://docs.tinyfish.ai/browser-api/reference), [Wallet](https://docs.tinyfish.ai/authentication). Provider pricing can change; Alex stores configurable rates, not eternal business truth.

## Capabilities

| Capability | Product | Cost | Planner | Security |
| --- | --- | --- | --- | --- |
| `tinyfish_search` | Search | Free | Off/Auto/On with Web | READ, public HTTP(S) |
| `tinyfish_fetch` | Fetch | Free | Off/Auto/On with Web | READ, public HTTP(S) |
| `tinyfish_browser` | Browser | Paid, default `$0.002` / minute | Off / Auto / On (default Auto) | Alex-controlled Playwright/CDP, typed actions |
| `tinyfish_agent` | Agent | Paid, default `$0.016` / completed step | Off / Auto / On (default Auto) | READ_ONLY only; side-effect goals blocked before the provider call |

Search/Fetch use `TINYFISH_SEARCH_FETCH_FREE`. Setting it `false` blocks those calls until pricing is reviewed.

## Current official API (reviewed 2026-09-19)

- Search: `GET https://api.search.tinyfish.ai`, `X-API-Key`.
- Fetch: `POST https://api.fetch.tinyfish.ai`, markdown, bounded URLs/timeouts/TTL.
- Agent: `POST https://agent.tinyfish.ai/v1/automation/run-sse` (`url`, `goal`, optional `output_schema`, `agent_config.max_duration_seconds`). SSE: `STARTED` / `PROGRESS` / `COMPLETE`. `GET /v1/runs/{id}` for `num_of_steps`. `POST /v1/runs/{id}/cancel` for `/run-sse` runs. **No pause. No pre-action intercept or approval callback.** `max_steps` is beta and returns 403 unless enabled (`TINYFISH_AGENT_MAX_STEPS_SUPPORTED=false` by default). Vault/profile stay `use_vault=false`, `use_profile=false`.
- Browser: `POST https://api.browser.tinyfish.ai` returns `session_id`, `cdp_url`, `base_url`. `DELETE /{session_id}` → 204. Inactivity timeout. No session listing API.
- Wallet: official `GET /v1/wallet`. Local spend is labeled `estimated_provider_cost` (steps × configured rate, minutes × configured rate). A wallet delta is not claimed without that API or a manual dashboard check.
- Auth: `X-API-Key`. 401 invalid/missing, 402 insufficient wallet.

## Routing (thick controller)

The weaker production model does not own budgets or provider choice.

1. Simple lookup → Search then Fetch.
2. JS shell (`needs_browser`) or explicit “open in a browser” → TinyFish Browser.
3. Complex multi-page **read-only** research → TinyFish Agent.
4. Tor / `.onion` → existing Tor stack only. No TinyFish fallback.
5. Local files/computer → Local Computer tools.
6. External side effect → confirmation-controlled Browser or loopback form tools. **Agent is not started.**

Auto mode hides paid tools from the planner. The server injects Agent/Browser **before** the planner loop when `select_tinyfish_route` selects them (`origin=server_policy`), even if that leaves the planner tool list empty (explicit Browser + Auto previously returned before inject). Relative page links are resolved against the current URL before click. After a Browser snapshot the visible answer is grounded from W sources / verified facts, not from model memory. The Browser session is closed so cloud minutes are not billed during model generation. A second pass after Search/Fetch still covers a JS shell (`needs_browser`). On makes the matching paid tool visible to the planner. Off removes them.

Planner-facing names: `web_search`, `web_fetch`, `web_browser`, `web_agent`. Legacy `web_agent_read` / `browser_*` stay explicit (`auto_route=false`).

## Agent CASE B — READ_ONLY

The current Agent API cannot stop a side-effect **before** it happens. Prompt text is defense-in-depth, not a permission boundary.

Alex classifies the goal as `READ_ONLY` / `POTENTIAL_SIDE_EFFECT` / `SIDE_EFFECT` and **blocks Agent before the HTTP create** unless the class is `READ_ONLY`. Allowed: research, navigate/read, extract, compare, collect, summarize. Forbidden goals include submit, send, buy, checkout, login with credentials, upload, delete, account change.

There is no Agent pause. Stop cancels via `POST /v1/runs/{id}/cancel` when a `run_id` exists, then closes the SSE stream. If cancel is unconfirmed, the journal records `supplier_stop_confirmed=false`.

## Browser — Alex-controlled

Alex creates a blank TinyFish Chromium session, connects Playwright over CDP, then runs typed actions: open / read / links / click L-ids / back / wait / close. The model never receives raw JavaScript, raw CDP, or `Runtime.evaluate`. Internal Playwright may use CDP; that is not a model tool.

Live TinyFish CDP is proxied. Playwright `page.route` + `route.continue_` breaks the tunnel (`net::ERR_TUNNEL_CONNECTION_FAILED`). Production live sessions therefore skip request interception and still enforce public HTTP(S) URL policy on typed actions. Local unit tests that inject a Playwright factory keep the GET/HEAD/OPTIONS guard.

Network guards allow GET/HEAD/OPTIONS; non-GET requires a confirmed write budget. Password/payment fields are rejected. External side effects (form submit, type into controls) stay on explicit `browser_write` with the existing READ / NORMAL_CHANGE / SENSITIVE / CRITICAL confirmation policy.

Every session has `session_id`, `task_id`, `owner_id`, `started_by_alex`, timestamps and estimated minutes/cost. Stop, failure, budget exhaustion and task cancellation close the remote session (`DELETE`). A watchdog enforces hard lifetime and idle timeout. Restart recovery attempts leftover `DELETE`/`cancel` from the task checkpoint. The provider does not list live sessions; Alex treats local registry empty + successful DELETE as cleanup.

TinyFish Vault and Browser Context Profiles are **not enabled**. No website passwords are sent.

## Budgets

Per Autonomous Task defaults (clamped to hard ceilings):

- paid TinyFish `$1.00` (hard `$2.00`)
- Agent max 2 runs / 20 steps (hard 50)
- Browser max 2 sessions / 10 minutes (hard 30)

Preflight refuses a new Agent run or Browser session that would exceed remaining USD, steps, runs, sessions or minutes. This is app-side accounting. `max_steps` is forwarded only when the account actually supports it; Alex still cancels the stream when the local step cap is reached.

## Domain safety

Agent/Browser targets are public `http`/`https` only. `.onion`, `file:`, `data:`, `javascript:`, `blob:`, `chrome:`, localhost and internal networks are rejected. Production SSRF checks stay on.

## Tests

Normal tests use fake adapters and HTTP contracts. Optional live Search/Fetch still requires `ALEX_LIVE_WEB=1`. Paid Agent/Browser proof is a separate, budget-capped verification, not part of the default pytest suite.

# Canalla 1.2.3 — live acceptance plan and budgets

Prepared from the 20 Oct measurements: **60 s** to model-ready (L40S), the first planner turn
took **240 s**, later simple turns took **6–20 s**, and a session is hard-capped at **700 s** by
the independent watchdog. The cap is now enforced with a **45 s stop reserve**: the terminate is
sent at `created + 655 s` and must be confirmed before `created + 700 s`. The 20 Oct Pod was
confirmed at 726 s; that overrun is fixed.

The first-turn 240 s is still a **measurement**, not an assumption: the backend now logs
`gateway_plan_start → headers → first_token → done` and
`gateway_stream_start → headers → first_token → done`, and the final answer now sends
`enable_thinking: false` (the planner already did). The first session must record these phases
before any later session is booked.

## What is proven free vs what needs Qwen

| Area | Free evidence | Needs real Qwen |
|---|---|---|
| Installed app, Computer host | host smoke `write_file → read_file → run_python`, Python 3.12.9, pytest 9.1.1; `host-chain` `4\n`/`6\n` | the model choosing those tools |
| Paths with spaces, scope | backend tests (spaces, unicode, `..`, junction, argv) | model uses a spaced path |
| Web | Web chip `configured`; provider contract and mock e2e | real `web_fetch` page content |
| Tor | Tor chip `ready`, persisted SOCKS5h proof | optional Tor navigation |
| UI streaming | Playwright `generation-navigation.spec.ts`; installed-window script with valid frames | UI while a real Qwen stream runs |
| M2/H2/H3 | CSV seed + expected stats, failing/passing seed projects and their independent verifiers | model performs the work |
| Stop / disconnect | pods `[]`, latch, wait-cancel tests; restart-always-ready | Stop during a real generation |
| Model-origin rule | `catalog.py --self-test`: `server_policy` is `BLOCKED_DETERMINISTIC`, never PASS | — |

**No scenario PASS is claimed from the preflight.** It proves the test system, fixtures,
permissions and independent checkers.

## Session grouping (measured, not promised)

Each row is **one Pod**, one `ensure`, one watchdog window. The first turn (240 s measured) is
paid once per session; the second and later turns are cheap. Each session starts with the
cheapest simple scenario available and records phase timings.

| Session | Scenarios | Prompt budgets | Estimate | Max USD at $1.09/h |
|---|---|---|---:|---:|
| simple-a | S1–S5 | 45+45+60+60+60 = 270 | 60 + 240 + 270 = **570 s** | 0.17 |
| simple-b | S6–S7 | 90+90 = 180 | 60 + 240 + 180 = **480 s** | 0.15 |
| medium-a | M1–M3 | 120+150+180 = 450 | 60 + 240 + 450 = **750 s** → stop at 700, carry M3 | 0.21 |
| medium-b | M4–M7 | 120+150+120+60 = 450 | 60 + 240 + 450 = **750 s** → carry the last | 0.21 |
| hard-a | H1 | 300 | 60 + 240 + 300 = **600 s** | 0.18 |
| hard-b | H2 | 300 | 60 + 240 + 300 = **600 s** | 0.18 |
| hard-c | H3 | 300 | 60 + 240 + 300 = **600 s** | 0.18 |
| hard-d | H4 + H6 | 240+180 = 420 | 60 + 240 + 420 = **720 s** → H6 in the next window | 0.21 |
| hard-e | H5 | 300 | 60 + 240 + 300 = **600 s** | 0.18 |
| stress-a | T1 | 240 | 60 + 240 + 240 = **540 s** | 0.16 |
| stress-b | T2, T5–T7 | 60+90+60+60 = 270 | 60 + 240 + 270 = **570 s** | 0.17 |

T3, T4 and T8 are free UI/host checks and are not paid sessions. H4+H6 may need a sixth hard
session if H4's research runs long; a capacity failure ends the package instead of retrying.

**Maximum**: 11 sessions × 700 s = 7,700 s = 2.14 h ≈ **$2.33** at $1.09/h (plus the volume's
$0.01/h). **Likely**: 6–8 sessions if the measured first turn drops below 240 s after the
thinking fix, ≈ **$1.3–1.7**.

## Dense paid package (recommended)

Three gated packages, each one Pod at a time, sequential, no automatic retry:

1. **Package A — harness proof.** `simple-a` + `simple-b` (2 Pods, ≤ $0.32). Gate: S1–S7
   PASS with model-origin tool calls, first-turn phase log captured, watchdog confirmed ≤700 s.
   If S1 or S3 fails, stop; no later package is booked.
2. **Package B — file/program/web.** `medium-a` + `medium-b` + `hard-a` (3 Pods, ≤ $0.60).
   Gate: M1–M7, H1 PASS; M7 denial and the independent M2 checker must be green.
3. **Package C — hard + stress.** `hard-b`, `hard-c`, `hard-d`, `hard-e`, `stress-a`,
   `stress-b` (6 Pods, ≤ $1.23). Gate: H2–H6, T1, T2, T5–T7 PASS; UI T3/T4/T8 already free.

Do not start B until A is green; do not start C until B is green. Each package is a separate
owner approval. No code changes between sessions of a package; any defect found is reproduced
free first.

## Per-session stop criteria

- first `gateway_plan_done` > 300 s or no first token > 300 s → stop, save the phase log,
  do not book the next session;
- selected GPU rate > $2.00/h or unknown → reject (watchdog already refuses);
- spend estimate > $0.40 for the session → stop before the next scenario;
- any expected tool is `server_policy` → `BLOCKED_DETERMINISTIC`, not PASS;
- provider returns `gpu_unavailable` or the Gateway parks `waiting_capacity` → typed external
  blocker, the client aborts immediately, cancels the wait, and no retry loop or second Pod runs;
- watchdog must report `within_cap=true`; if not, the session is a failure regardless of model output.

## Evidence per scenario

`acceptance-123/runs/live-…/<ID>.json`:

- the exact prompt and every SSE event with `elapsed_ms` and UTC time;
- first event, first delta, done (client phases);
- backend `gateway_plan_*` / `gateway_stream_*` log lines (server phases);
- `tool_runs` with `tool_name`, `origin`, `status`, arguments, `host_result`, stdout/stderr;
- confirmations (allow/deny, HTTP, digest), including the M7/T6 denial;
- files before/after with sha256 and changed paths;
- task status/events and web sources;
- UI output (separate free script) where applicable;
- `verdict` and exact `reasons`.

A scenario is PASS only when its own expected model-origin tools, files, stdout and independent
post-check agree. A model's prose alone is never a PASS.

## Free preflight that must stay green

- `python artifacts/acceptance-123/catalog.py` — model-origin self-test;
- `python artifacts/acceptance-123/preflight.py --with-ui` — 28 fixture directories, seed checks,
  host smoke, pytest on the host runtime, RunPod `pods: []`, watchdog `disarmed`, UI frames;
- `python scripts/release-gate.py developer` — CI parity;
- Playwright `e2e/generation-navigation.spec.ts` — UI during streaming.

## Release gates

1. Free: CI 7/7, preflight complete, host/pytest/UI green.
2. Package A green: simple dialog, context, spaced file, exact file, Python, Web, chat switch.
3. Package B green: create/run/fix, CSV, mini-project, two sources, Web+Computer, chat
   continuation, denied confirmation.
4. Package C green: CLI project, pytest bug fix, refactor, multi-source report, long chain,
   transport recovery, long state, Stop, web error, denied command, timeout.
5. Only then publish v1.2.3 — Draft only, and only with a separate approval.

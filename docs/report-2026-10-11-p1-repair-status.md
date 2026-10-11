# Canalla LLM 1.2.1 — P1 reliability/security repair: status

**Branch:** `fix/canalla-1.2.1-p1` (pushed) · **Base:** `ae832c5` (`v1.2.0`) · **HEAD:** `d26340a`
**Production/modifications: NONE.** **Paid GPU spend: $0.** No Pod, volume, account setting, deploy, merge, tag or manifest.

## Block 2 (11 Oct 2026) — what this block added

| ID | Status |
|---|---|
| A06 | **FIXED** — readiness has a TTL (`compute_ready_ttl_seconds`, default 30 s), a failed probe leaves green immediately with the typed `model_unavailable`, and a model that stays unreachable past `compute_ready_grace_seconds` (default 120 s) is stopped through the managed path (A02 retries it). Active generations are never probed. Migration `0003_ready_probe` (`ready_probe_at`, `unready_since`). 6 tests in `apps/gateway/tests/test_readiness_ttl.py`. |
| A30/A31/A32 | **PARTIAL** — the Gateway half is honest now (bounded transition, typed terminal reason the client already maps to Retry, no fabricated percentages, status stays read-only and starts nothing). The *client* half (one operation id per attempt with started_at/deadline/last_success_at, explicit stage labels, and the agreed client/queue/first-byte budgets) is **not implemented** — it needs the desktop/backend work and the audit's exact acceptance wording. |
| A05, A07, A08 | **NOT STARTED** (this block's budget went to Phase 0 + A06). A08's defect is confirmed by reading the code: `apps/backend/app/tools/tor/socks.py` fetches through a default `127.0.0.1:9050` with **no** current-proof check bound to that endpoint/process. |
| A09, A10, A11, A12 | **NOT STARTED.** |
| A16/A17 | Not reached (they touch the same credential/secret paths A05/A07 would). |
| §7 Defender | **ANSWERED with evidence** (see below). |
| §8 inventory | **DONE** (read-only, timestamped) — `scripts/region-inventory.py --history <file>`. |

## Phase 0 — security evidence (read-only, 11 Oct 2026)

* Threat: `Trojan:Win32/LummaStealer.DO!MTB`, severity 5, `ThreatStatusID=3`, `ActionSuccess=True`,
  `IsActive=False`, **`DidThreatExecute=True`** (expected: we ran our own app and our own test binaries).
* **Every** detection resource is our own build output, our own installed app, or registry/shortcut
  entries created by **our own NSIS installer** (`...\Programs\Canalla LLM\alex-llm.exe`,
  `target\{debug,release}\deps\alex_{llm,host_loop}-*.exe`, the `Canalla LLM.lnk`, the HKCU Run and
  Uninstall keys). **Nothing outside our tree was detected.**
* No dropper artefacts: no new `%TEMP%` executables/scripts/archives in the detection window, no new
  Run keys, no stray copies of browser credential databases, no suspicious autostart entries, no
  unexpected listening process (all standard Windows/Intel/OneDrive services).
* The accepted installer is intact (95 340 864 B, sha256 `61a99cfc…beac4`), its **updater signature
  verifies** with the production public key, and the published manifest matches it. The repository has
  no uncommitted source change, so the deleted binaries came from the tracked source.
* **Not verified (stated, not glossed):** the Defender exclusion list (needs an elevated prompt — this
  session is not elevated), no full disk scan has ever been run on this PC, the toolchain was not
  hash-verified against upstream, and the Chrome/Edge `Login Data` timestamps (11 Oct 12:19 / 11:31)
  fall inside the detection window and cannot be attributed from here.
* **Verdict:** no positive evidence of compromise was found, but a false positive is **not proven**
  either. Therefore no destructive, credential or production action was taken (nothing was going to be),
  the quarantined executables were **not** restored, **no** exclusion was added, **no** protection was
  disabled, and nothing private was uploaded to a public scanner. `cargo test` on this host is reported
  **BLOCKED-BY-DEFENDER** rather than bypassed.
* Operator plan: full Defender scan from an elevated session; verify exclusions there; if certainty is
  wanted, rotate only this PC's secrets (updater identity `CanallaLLM/UpdaterSigning/Production` + its
  key file, and the dev `Alex LLM/*` credentials — the RunPod master key is **not** on this PC);
  Authenticode-sign future builds (the root cause of an aggressive ML heuristic on unsigned Rust PEs).

## Tests (Block 2)

| Suite | Result |
|---|---|
| Gateway pytest | **213 passed** (207 + 6 new) |
| Gateway Ruff check / format | PASS (34 files) |
| Gateway Alembic | head `0003_ready_probe`; `alembic check` clean |
| Backend pytest | **787 passed, 1 skipped, 1 failed = `test_sidecar_stamp`** — the expected `BACKEND_SIDECAR_STALE`, red only until the sidecar is rebuilt (the backend source changed in this block) |
| Backend Ruff | PASS |
| Rust `cargo test` | **NOT RUN — BLOCKED-BY-DEFENDER** (builds+executes the binaries Defender flags) |
| Frontend Vitest/Playwright | not run (untouched by this block) |

## Block 1 (11 Oct 2026) — carried status

## 0. Production verification before any change

| Check | Result |
|---|---|
| `main` HEAD vs expected `ae832c5da2682ebb1ed697498d2034b7620cb08b` | **match** |
| Deployed Gateway | `/opt/alex-gateway/releases/1994c0ab2016`, active, one uvicorn worker |
| Deployed vs repo source | all **14/14** `gateway/*.py` byte-identical (per-file sha256) → **no discrepancy** |
| `GET /health` | 200 · version `1.2.0` · `ready: true` · `database: ok` |
| `GET /updates/latest` (current 1.2.0) | 204 |
| RunPod | `compute: state=stopped`, `GET /v2/pods` → 0 |

**Discrepancy recorded:** the audit document that defines **A01–A32 / V01–V04** was **not found** — not in
`docs/`, not elsewhere in the repository, and not on this machine (searched `docs/`, the whole worktree,
and the user's Desktop). This block therefore works from the task's own description of each ID. Items that
need the original finding text are listed as *pending* rather than guessed at.

## 1. Fixed in this block (each with regression tests)

### A01 — an ambiguous create stays under observation
`tick()` returned early for any state outside `ACTIVE_STATES`, and `create_unknown` is not one of those —
so an unresolved create was only ever reconciled when a *client* called in again. The background loop now
reconciles that state every tick:

* the Pod the provider actually created is found by its **durable identity** (the `alex-gw-<session>` name
  the intent committed before the provider call) and **adopted as `managed`**, so it stays under budget
  control (idle, budget and price guards all keep applying);
* when the provider read confirms no Pod exists, the intent is **resolved by evidence** (`create_resolved`,
  bounded by `CREATE_RESOLVE_SECONDS` + the attempt budget) instead of hanging;
* nothing here creates compute — reconciliation only adopts or resolves, so a blind second create stays
  impossible.

Tests: `test_the_tick_keeps_an_ambiguous_create_under_observation`,
`test_the_tick_resolves_an_ambiguous_create_by_provider_evidence`.

### A02 — a durable stop intent, retried until the provider confirms
`_terminate()` cleared `pending_stop` *before* the provider call and left the row in `stopping`, but only
the D-9 startup reason was ever retried — a refused **manual/idle/budget/price** stop left the Pod billing
behind a permanent «stopping». Now:

* the stop intent is durable for **every** reason, and a later pod-status read can no longer re-claim a
  healthy startup (`stop_intent` keeps the state);
* the retry runs from **persisted** state with a bounded backoff — a new nullable
  `gateway_sessions.stop_attempt_at` (migration `0002_stop_attempt`) is the attempt clock, so a Gateway
  restart or a vanished client never abandons a Pod;
* «stopped» is only ever reported **after** the provider confirmed the termination; a refused call leaves
  the client on «stopping» with the error code visible.

Test: `test_a_refused_stop_is_retried_until_the_provider_confirms`.

### A03 — inference replay isolation
The replay cache and the in-flight map were keyed by the **client-chosen request id alone**, so installation
B could be served installation A's answer by reusing an id, and an id could replay the answer to a
*different* question. Identity is now `installation_id + request id + body digest` (the digest is a
canonical JSON hash); an id with a different body is a new generation, and a foreign id can never hit.
Operation idempotency got the same rule: `_operation()` now verifies the recorded digest, so an operation id
cannot replay an answer to a different ensure/stop request.

Tests: `test_another_installation_never_receives_a_cached_answer`,
`test_the_same_id_with_a_different_request_is_a_new_generation`.

### A04 — the generation slot is released even if the body never starts
Starlette may close a streaming body **before** it is ever iterated (a client that drops between the
response headers and the first chunk). A bare async generator then never enters its `finally`, so the single
generation slot (`--parallel 1`) stayed occupied forever and every later request queued until `gateway_busy`.
The body is now wrapped in `GenerationBody`, whose `aclose()` always runs an idempotent single-outcome
finalizer. Found by writing the A13 test, which exposed the leak.

Test: covered by `test_a_concurrent_duplicate_never_starts_a_second_generation` (asserts the slot and the
claim are released) and by the existing disconnect test.

### A13 — concurrent idempotency
The id was claimed *after* `await queue.acquire()`, so two simultaneous copies of one request both passed the
lookup and the second could generate after the first released. The claim is now registered **before any
await**; a concurrent duplicate is refused immediately with `gateway_request_in_flight`, and an admission
failure releases the claim so a legitimate retry still works.

Test: `test_a_concurrent_duplicate_never_starts_a_second_generation`.

### §6 — the packaged Gateway URL (the 1.2.0 defect)
Root cause confirmed by reading the code: the client's enrollment default comes from
`option_env!("ALEX_GATEWAY_URL")` (compile time) in `apps/desktop/src-tauri/src/gateway.rs`, while neither
`scripts/build-desktop.ps1` nor `scripts/production-signing.ps1` set it — and the backend always reports
`default_url: null`. The 1.2.0 binary contained **one** occurrence of the host (the updater endpoint only),
so a fresh install showed an empty «Адрес Gateway» and a disabled «Подключить».

* both release build paths now default `ALEX_GATEWAY_URL` to `https://gateway.12testers.store` (an explicit
  value still wins, so a test build can point elsewhere);
* a new **packaged-artifact guard** `scripts/verify-packaged-gateway-url.py` fails the build when the built
  executable does not carry the production URL as a standalone literal (it knows the updater URL is not
  enough). It has a `--self-test` that proves both directions and is wired into the build scripts;
* the guard is exercised by the suite (`apps/backend/tests/test_packaging_gateway_url.py`), so a build that
  silently loses the URL fails the tests instead of shipping.

Preserved by construction: the data root `%LOCALAPPDATA%\Alex LLM\`, Credential Manager entries, the
Gateway installation credential, the user SQLite database and the existing upgrade/update identities are
untouched — the change is a compile-time default inside the client, nothing else.

## 2. Tests (this block)

| Suite | Result |
|---|---|
| Gateway pytest | **207 passed** (was 201; +6 new) |
| Gateway Ruff check / format | PASS (32 files) |
| Gateway migrations | new head `0002_stop_attempt`; `alembic check` clean (`test_migration_matches_the_models`) |
| Backend pytest | **788 passed, 1 skipped** (787 + the new guard test) |
| Backend Ruff (new files) | PASS |

Not affected by this block, so not re-run: frontend Vitest/Playwright, Rust `cargo test`. They must run in
the release gate before a candidate is accepted (§10 of the task).

## 3. Pending (not started — needs the original finding text or a next block)

| ID | Why it is not done here |
|---|---|
| A05 | durable claim/result delivery for Computer side effects — needs the audit's exact scenario; the backend computer/claim path was not modified in this block |
| A06, A30, A31, A32 | readiness/«Connecting» TTL and the single connect deadline — partially present (D-9 + `probe_ready`); the Gateway still never re-probes readiness after the first proof, which is a real gap for A06, but the exact acceptance criteria (TTL value, staged labels) come from the audit |
| A07 | Computer polling after JWT refresh |
| A08 | Tor proof before the SOCKS5h fetch |
| A10, A11 | money safety across several installations (operator quotas, emergency stop, stop rights) |
| A16, A17 | credential storage / native host — to be checked for impact if A05/A07 change them |
| §9 | multi-region plan + deterministic FakeRunPod tests (deliberately not enabled in production) |

## 4. §7 — the vanished `alex-llm.exe` (read-only diagnosis, answered)

**Confirmed cause: Windows Defender removed it.** No guessing was needed once the detections were read:

* Threat: `Trojan:Win32/LummaStealer.DO!MTB`, severity 5, `IsActive: False` (nothing is running).
* Defender: real-time protection ON, tamper protection ON.
* Removed files (detections on 3 Oct 18:59 and 11 Oct 11:04–13:07 UTC):
  `%LOCALAPPDATA%\Programs\Canalla LLM\alex-llm.exe` and its Start Menu shortcut,
  `apps\desktop\src-tauri\target\release\alex-llm.exe`, `...\target\release\alex-host-loop.exe`,
  dozens of `target\{debug,release}\deps\alex_{llm,host_loop}-*.exe`, and two old installers
  (`Alex LLM_0.7.0.exe`, `Alex LLM_0.9.0.exe`).
* Intact: the data root `%LOCALAPPDATA%\Alex LLM\` (data, documents, models, runtime, tor, `device.json`,
  backups), and the accepted installer on the Desktop — 95 340 864 B, sha256 `61a99cfc…beac4`.
* No `alex`/`canalla` process is running.

These are heuristic false positives on our own **unsigned** Rust PE binaries (the installed product runs
correctly on another PC). Recovery without data loss: re-run the accepted installer
`Canalla LLM_1.2.0_x64-setup.exe`, which restores the program files; the data root and the Windows
Credential Manager entries are never touched by install, upgrade or uninstall. On a machine whose Defender
flags the unsigned binary this can recur — which is what makes **Authenticode signing a real release
requirement, not cosmetics**: it is the honest fix, and no exclusion was added and no protection disabled
(neither automatically nor by request).

## 5. §8 — read-only RunPod inventory (11 Oct 2026, one snapshot)

| Item | Finding |
|---|---|
| Network Volumes | **exactly one**: `uwgeaie5b0` `orcarouter-storage`, 50 GB, `STANDARD`, `US-TX-3`. No duplicate exists to create by accident |
| Global Volumes (GraphQL) | not exposed: the read answered **HTTP 400** — consistent with the recorded 2026-09-25 finding that the public API cannot attach a Global Volume. Nothing was created |
| Cheap compatible Secure offers (≥48 GB, `price ≤ $2/h`) | **NVIDIA A40 48 GB — $0.59/h in `CA-MTL-1` and `EU-SE-1`, availability `HIGH`**; `NVIDIA L40 48 GB $0.82/h` (Community); `L40S 48 GB $1.09/h` in `US-TX-3`, `US-TX-4`, `US-NC-1`, `US-IL-1`, `EU-NL-1`, `EUR-IS-2`, `OC-AU-1` (availability `LOW`); `RTX 6000 Ada 48 GB $0.99/h` (Community) |
| SKU count per datacenter (this snapshot) | `EU-RO-1` 5, `US-KS-2` 4, `EU-NL-1` 4, `US-NE-1` 4 — most of those SKUs are Community-tier and above the $2/h production cap |
| US-TX-3 | only `L40S 48 GB $1.09/h`, availability `LOW` — which is why live acceptance is flaky |

**This is one snapshot.** Availability is a moment-in-time signal and must never be read as "this region is
the most reliable". The practical, honest candidates for a second placement inside the existing $2/h policy
are the **A40 48 GB $0.59/h HIGH** rows in `CA-MTL-1`/`EU-SE-1` — cheaper *and* better stocked than the
primary, which would also relieve the current flakiness. A monitor for availability without creating Pods
already exists (`artifacts/final-acceptance/region-inventory.py`, read-only, poll it on a schedule).

## 6. §9 — the second placement (plan only, deliberately OFF)

The multi-placement architecture already exists and is config-driven (`RUNPOD_DATACENTERS` as `DC:VOLUME`
pairs, `RUNPOD_REPLICAS` as verified replica records; the shared candidate policy is
`apps/backend/app/compute/candidates.py`, imported by the Gateway). A second placement needs its **own**
Network Volume in that datacenter (the model lives on the Volume) and a verified replica record, so the plan
is: pick the DC from a *monitored* availability history (not one snapshot), create one STANDARD 50 GB Volume
there, copy the exact GGUF/runtime, verify byte-for-byte, publish the replica record, and then add the
`DC:VOLUME` pair. The deterministic FakeRunPod tests for
`primary unavailable → secondary candidate → one Pod → restart/reconcile → stop → GPU=0` are **not written
yet** (task §9) and nothing is enabled in production: the 1.2.0/1.2.1 config keeps exactly one placement.

## 7. Release implications

* **Gateway redeploy required** for A01–A04/A13 to take effect in production: new immutable release
  directory + atomic `current` switch + `alembic upgrade head` (migration `0002_stop_attempt`). Not done —
  no production deploy happened in this block.
* **New installer required** for §6 (the baked Gateway URL) to reach users.
* `main`, `v1.2.0` and the published 1.2.0 manifest are untouched.

## 8. Next step

Continue the repair on the same branch with the remaining IDs (A05; the readiness/«Connecting» family
A06/A30/A31/A32; A07; A08; A10/A11), then write the §9 FakeRunPod failover tests, then run the full suite
(frontend + Rust included) before any deploy, rebuild or installer candidate.

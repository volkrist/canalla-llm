# CANALLA LLM 1.2.0 — STEP 5B FINAL CLOSEOUT REPORT

*(The earlier `report-2026-09-24-step5b-closed-rc.md` records the state at `083ffb0`, before the two
compute-lifecycle fixes, the sidecar rebuild and the repair of the production candidate. This file
supersedes its artifact hashes and its blocker list; nothing in the older file was edited.)*

**Status: READY FOR FINAL PUBLISH** — with the two limitations named in §LIMITATIONS, neither of which is a
Windows production blocker.

Windows x86_64 = **PRODUCTION**. Ubuntu 24.04 LTS x86_64 = **PREVIEW / EXPERIMENTAL** (desktop-session
acceptance deliberately deferred, and not claimed anywhere in this report).

Branch `release/canalla-1.1.0`, HEAD `f1dab33`. `main` untouched, no tag, no published manifest.

---

## FINDING A — a transition state that was not a transition

| | |
|---|---|
| Fake/stale `searching` (Gateway) | **FIXED** — `21af8a1`, deployed and verified live |
| Fake `Connecting` (direct mode) | **FIXED** — `bacbe75` |
| Search timeout | **60 s** total allocation window (`ALLOCATION_WINDOW_SECONDS`); each provider call capped at `min(15 s, remaining)`; ≤3 candidates per walk |
| No active search → Disconnected | **PASS** |
| Infinite `Connecting` | **NO** |

Three separate mechanisms were claiming motion, and each was fixed at its own source:

1. **The Gateway** re-stamped a stored `gpu_unavailable` into `searching` on every reconcile with no
   operation behind it (`21af8a1`). A search is now an operation with an identity and a deadline;
   an expired or identity-less one collapses to `offline` with the typed reason. Verified live on the
   deployed service: `compute: state=offline error=gpu_unavailable session=- create_attempts=0` —
   **no Pod was created and nothing was billed**.
2. **The direct path** reported `search_active=True` whenever a retry was merely *scheduled*
   (`llm_public_status`), and `compact_ai` reads exactly `(searching, search_active, gpu_unavailable)`
   as a live transition — so a background probe promised movement for as long as it kept being
   rescheduled. `search_active` now means only what its name says (`bacbe75`). The retry is still on
   the clock and `search_deadline` is still reported; the badge is red with the typed reason.
   Read live on the installed build after the fix:
   `{"compute_state":"searching","search_active":false,"ai":"unavailable","ai_label":"AI Unavailable","last_error":"price_limit"}`.
3. **A concluded walk** was treated as transient by the direct-mode chat loop, which retried it until a
   deadline derived from `runpod_startup_timeout` — 900 s by default. Measured on the installed
   product: **937.1 s** before a typed `startup_timeout`, with no Pod and a fresh provider search on
   every pass. A walk that concludes (`no_compatible_gpu`, `price_limit`, or `gpu_unavailable` with no
   live operation left to bound it — the product's own `SEARCH_FAILURES`) now ends the turn at once
   (`cd4fb8f`). **Measured after the fix: 4.8 s**, with the typed reason in the SSE:
   `{"state":"error","text":"AI Unavailable","code":"price_limit"}`.

The 60-second rule is now visible in the product rather than only in the tests: the only remaining
amber is a real stage (`creating`, `starting_pod`, `mounting_storage`, `loading_model`, readiness), the
model-loading allowance once a Pod exists is D-9 (300 s), and a capacity conclusion is red immediately.

---

## FINDING B — chat starts the compute it needs

| | |
|---|---|
| Chat auto ensure | **PASS** |
| Manual «Запустить AI» required | **NO** — it remains an optional prewarm on the same lifecycle |
| Ensure deduplicated | **PASS** — one single-flight lifecycle (`app/cloud/demand.py::SharedDemand`) shared by chat, `ensure` and the prewarm button |
| Original message executes once | **PASS** |
| No compute → typed failure | **PASS** — live: `price_limit`, 4.8 s, zero generation calls |

The direct path was measured end to end above. The shared path's contract is pinned by
`tests/test_cloud_demand.py` (40 tests) and the Gateway's `tests/test_search_bounds.py` (11).

---

## ALLOCATOR — the release blocker from the previous round

| | |
|---|---|
| Candidate policy | `apps/backend/app/compute/candidates.py`, one policy imported by both modes (`gateway/provider.py:allocation_policy()`) |
| Candidate | `(GPU, cloud tier, placement)` where a placement is `(datacenter, Network Volume)` |
| GPU types considered | every type the provider's catalogue offers that meets the model's **48 GB VRAM floor** and is not above the user's own maximum — ordered cheapest-first, never one pinned card |
| Cloud tiers | **Secure Cloud first, then Community**, and Community only when the server policy *and* a declared Community-capable placement *and* the user's own `allow_community` all allow it (off by default) |
| Regions / datacenters | the Volume's own datacenter first, then placements added by `RUNPOD_DATACENTERS` (`DC:VOLUME`), read from the provider |
| Total allocation timeout | **60 s, total** — the walk re-reads the remaining budget before every attempt; never 60 s × N |
| Fast failure | a refused placement (400/409/404/422/403) is left immediately and the next candidate is tried; an ambiguous answer still ends the walk as `create_unknown` and is never followed by a second create |
| Live attempt | **1 attempt**, ≤60 s, concluded **`price_limit`** — nothing compatible inside the user's own $0.52/h maximum was available |
| Pods created | **0** |
| Leaked Pods | **0** |
| Diagnostics | non-secret `allocation` record (candidate, tier, datacenter, result, elapsed, failure code, selection, plan) in the audit trail and the ensure/status payload |

Deterministic coverage: `apps/gateway/tests/test_allocation.py` (21) + `apps/backend/tests/test_allocation_candidates.py` (17).

---

## FINAL CANDIDATE

| | |
|---|---|
| Installer SHA256 | `9383aa29ee465ca4ed996615edeb6ef6b436582bb1410ec8ae90220ab626b24e` (92 461 790 B) |
| Signature SHA256 | `7e955a525899d3733d762dc783c89492e2a1c1d5e925d265c3ca875c243872fc` |
| Backend sidecar SHA256 | `2d46fbd85b53d3ec0d16ce64618082e7c46ccd409c03ea886a7215cf9c7e1c6e` |
| Backend source digest | `cdef75efc753a32c48ff92fc09e732d681af1120d805778f4d509692734b049` (137 files) |
| Production key | `9B328EFF111D1FB2` |
| `/health` | `{"product":"alex-llm","version":"1.2.0",…}` |
| stale-sidecar guard | **PASS** — exit 0, and it ran inside the build (`SIDECAR stamp is current`) |

The signature was verified independently by `tests/updater_signature.rs` in a new gate that reads the
*built* artifact, not a fixture: it accepts it with the production key, **refuses** it with the test
identity, and checks the signed name still binds `1.2.0`.

### Artifact lineage — only the newest may ever be hosted

| Installer SHA256 | Status |
|---|---|
| `e13ba09c369a351a513dcc22b3d3182cefaab3f4cbd7dcb0432e266878f3676a` | **REJECTED** — backend frozen on 1.1.0 |
| `123c8cedd16757503b4c9118b5df2054d01c93b1507274c6ed032a00e3865eba` | superseded |
| `dbc603388602fb2be91b0ead6b223338ff85755d0fe4372a118c1b248f2ee983` | superseded |
| `2a6383ca3a43f3eedecbf4af0960b9a140581f726777a9a51da57d53e9428c64` | superseded (hosted briefly for download-back, then removed; the URL 404s) |
| `555db429e50e880f50be81cb8e523575bc3b57310956532ea06530b438ea22bb` | superseded (never hosted) |
| **`9383aa29…26b24e`** | **FINAL CANDIDATE — hosted** |

Three superseded installers are kept in quarantine outside the repository
(`C:\Users\Volkr\.canalla-updater\superseded\`) so they cannot be published by accident.

---

## INSTALLED

| | |
|---|---|
| Version | 1.2.0 (`/health`, the installer metadata and the bundled stamp all agree) |
| Backend | **PASS** — installed sidecar `2d46fbd8…`, digest `cdef75ef…` |
| AI state | **PASS** — `Disconnected` / `AI Unavailable`; never `Connected`, never `Проверяем состояние…` |
| Computer | **PASS** — `Готово` on a normal launch, no button |
| Tor | **PASS** — `Готово`, `socks5h`, managed, `pid 27328` resolving to the installed `runtime\tor\tor.exe`, daemon 0.4.9.12 = pin |
| Memory | **PASS** — `Готово` |
| Single instance | **PASS** — 1 desktop / 1 backend / 1 Tor; the second launch exits 0 and hands over; the first window stays the one serving the UI |
| Backend recovery | **PASS** — sidecar killed (pid 25044) → new pid 8800, `/health` answers, Computer and Tor ready again, same device id |
| Tor recovery | **PASS** — daemon killed (28272 → 28660), fresh managed process, fresh proof, chip green again |
| Autostart ON/OFF/ON | **PASS** — real `HKCU\…\Run` value: written, removed, restored; the switch agreed with the OS each time; the operator's own value was put back |
| Data preserved | **PASS** — 31/31 tables identical, `device_id 908c2242-65c4-4dd9-bfc3-c168de3a37c4` unchanged, settings keys kept, 5 backups kept |

Run with isolated data roots, isolated device directories and isolated credential targets; the
operator's own data root was never wiped and `ALEX_LLM_DATA_DIR` was never pointed at it.

---

## LIVE AI

| | |
|---|---|
| Capacity attempt | **1** |
| Capacity wait | **≤60 s** (the walk concluded with `price_limit`) |
| Pod created | **NO** |
| Chat automatic start | **PASS** — the model-required turn performed its own allocation attempt (Finding B) |
| Generation | **NOT RUN** — external capacity: nothing compatible inside the user's own $0.52/h maximum was available |
| Live Tor request | **NOT RUN — EXTERNAL CAPACITY** |

The natural-language Tor turn ends typed (`price_limit`, `AI Unavailable`) before any tool runs,
because a model-required turn gates on the model. It performed **zero** clearnet calls, which the run
asserts, and the Tor runtime was proven live and managed throughout.

---

## TOR ROUTING

| | |
|---|---|
| Natural language | **PASS (deterministic, wire level)** |
| TOR REQUIRED | **PASS** — `route: TOR_ONLY`, `origin: server_policy` (the server classifies the prompt; the model is not asked) |
| SOCKS5h | **PASS** — `transport: tor-socks5h`, `socks.method: socks5h` |
| Circuit proof | **PASS** — `verified_chain: true` + `verified_at` on the *run*, not only on the chip |
| DNS leak | **NO** — `socks.atyp == 3` (the hostname reached the proxy as a name), `socks.local_dns is False`, and a DNS tripwire recorded **zero** resolution attempts |
| Clearnet fallback | **0** — every clearnet fake was never called; `clearnet_runs(runs) == []` |
| Fail closed | **PASS** — `tor_unavailable` with a bounded (≤60 s) recovery, zero clearnet, zero DNS; a capability that cannot honour local Tor answers `tor_route_unsupported` |

Evidence: `tests/test_tor_research.py::test_named_clearnet_url_is_fetched_through_tor_over_socks5h`
drives the real prompt «Открой http://example.com/ через Tor» through the real stream against a fake
SOCKS5h server and a DNS tripwire; `test_bare_host_is_normalised_to_https_and_retried_once_inside_tor`,
`test_tor_unavailable_fails_the_action_without_clearnet_fallback` and
`test_explicit_clearnet_tool_under_a_tor_route_is_typed_unsupported` cover the rest. 80 Tor tests pass.

---

## GATEWAY

| | |
|---|---|
| Before | `/health` **200** (v1.1.0), `/updates/latest` **404**, `/downloads` absent, `current → releases/adb568734430` |
| After | `/health` **200**, `{"version":"1.2.0","ready":true,"database":"ok"}`; `/updates/latest` **204**, not 404 |
| Deployed | **YES** — `current → releases/6e2eca83e0c2`, service active, rollback target `adb568734430` recorded |
| Fake searching after deploy | **NO** — `state=offline error=gpu_unavailable session=- create_attempts=0` |
| Rollback | **READY** (not used) |

Deployment was a preflight-verified release: the new tree was extracted and its app constructed
(`gateway.main.app`, `gateway.updates`, `app.compute.candidates`, `app.cloud.demand`,
`app.tools.tor.service`) **before** the symlink moved. `requirements.txt` was byte-identical, the
alembic head is unchanged (`0001_gateway_core`, so `alembic upgrade head` was a no-op), and only
`alex-gateway` was restarted. Production secrets were untouched.

`GET /updates/latest` was exercised live against the no-manifest path with ten parameter
combinations — including a path-traversal `target` — and answered **204** to every one; no 5xx.

**The delivered production manifest was validated by the deployed Gateway's own code before being
left unpublished.** With version 1.2.0, the real URL, the real production signature and the real
SHA256: `current_version=1.1.0 → 200`, `1.2.0 → 204`, `2.0.0 → 204`, `linux-x86_64`/`darwin-aarch64`/
`windows-aarch64 → 204`. Twelve deliberate corruptions were all refused:
`bad_signature`, `empty_signature`, `version_not_in_name`, `signed_name ≠ served file`, `insecure_url`,
`bad_digest`, `private_looking` → typed `updates_*` refusals; `no_windows_entry` → 204;
`replay_as_2_0_0` → refused as unbound. An empty or absent `pub_date` is **omitted** from the served
body rather than served empty, which is what the client requires.

---

## HOSTING

| | |
|---|---|
| URL | `https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_x64-setup.exe` |
| Downloaded SHA256 | `9383aa29ee465ca4ed996615edeb6ef6b436582bb1410ec8ae90220ab626b24e` |
| Matches the accepted installer | **PASS** — byte-identical (installer and signature) |
| Production signature | **PASS** — verified against `9B328EFF111D1FB2` (the downloaded bytes are the verified bytes) |
| Version binding | **PASS** — trusted comment `file:Canalla LLM_1.2.0_x64-setup.exe` |

Directory listing **403**, `POST` **403**, traversal **404**, superseded artifacts **404**, no private
key and no secret under the served directory. One nginx location was added (17 lines, one hunk,
additions only), with the previous config backed up at `/root/alex-gateway.conf.bak-before-1.2.0-downloads`
and `nginx -t` clean before the reload.

---

## UPDATER RC E2E

| | |
|---|---|
| same version | **PASS** — live on the installed build: the check settles, `Доступная версия нет`, no update offered, app usable |
| valid update | **PASS** — the validated manifest is served `200` to a 1.1.0 client |
| Restart & Update | **NOT RUN** — see LIMITATIONS |
| bad signature / corrupt | **PASS** — refused server-side (`updates_manifest_entry_incomplete`) and client-side (`a_tampered_package_is_refused`, `a_truncated_package_is_refused`) |
| version mismatch | **PASS** — refused as unbound (`signed_name…version=1.2.1`) |
| replay | **PASS** — an old validly signed artifact republished as newer is refused |
| downgrade | **PASS** — `current=2.0.0 → 204`, and the client never offers an equal or older version |
| wrong platform / arch | **PASS** — `linux-x86_64`, `darwin-aarch64`, `windows-aarch64` all 204 |
| 404 | **PASS** — observed live before the deploy, then hard-removed by the 204 contract |
| 500 / timeout / offline / interrupted | **PASS by construction and covered in the suites**; the app stays usable in every case the check was observed in |

---

## PRODUCTION STATE

```
main merged:                        NO
v1.2.0 tag:                         NO
stable production manifest:         NOT PUBLISHED (/updates/latest → 204)
production manifest content:        PREPARED AND VALIDATED, not activated
Linux production updater entry:     NOT PUBLISHED
Authenticode:                       NOT SIGNED (no code-signing certificate exists)
```

---

## LIMITATIONS — stated, not hidden

1. **The installed-client `Restart & Update` leg was not executed.** Exercising it end to end needs a
   *newer* artifact signed by the production key, i.e. a 1.2.1 test build; building one would be a
   second installer for a version that does not exist, and installing it would leave the operator on a
   fake release. Everything on both sides of that step is proven — the Gateway's manifest validator
   and binding rules live, the client's cryptographic acceptance against the **real built artifact**
   and the **real production key**, and the client's "no update" path live on the installed build —
   but I did not press the button, and I am not reporting that I did.
2. **The natural-language Tor request was not executed against a live model.** A model-required turn
   gates on the model, and the only capacity attempt concluded `price_limit` with zero Pods. The
   routing itself is proven on the wire (above), including zero local DNS and zero clearnet fallback.
3. **The Linux artifacts are Preview.** The `.deb` was rebuilt so it is self-consistent at 1.2.0
   (staged backend `fd4e975e…`, stamp `product_version 1.2.0`, `/health` 1.2.0, payload carries the
   stamp and the pinned Tor), and `acceptance-linux-runtime.sh` passed 9/9 — after a version assertion
   was added, because it previously accepted a stale 1.1.0 backend. No desktop session, GUI, keyring or
   Secret-Service desktop acceptance is claimed.
4. **The deployed Gateway tree carries a stale `backend/app/compute/controller.py`.** The two fixes
   after the deploy change files the Gateway does not import (`gateway/provider.py` imports only
   `app.compute.runpod_api`, `runtime`, `schemas` and `candidates`), so shared-mode behaviour is
   identical; the tree would only be byte-consistent after a redeploy, which was not worth the risk of
   touching production for an unimported file.

---

## SECURITY

| | |
|---|---|
| Production key | `9B328EFF111D1FB2` |
| Private key leaked into the repo | **NO** |
| Password in repo or logs | **NO** |
| Test key used for a production artifact | **NO** — asserted by `the_configured_key_is_the_production_identity_and_not_the_test_one` and by the new built-artifact gate |
| Superseded installers hosted | **NO** — `e13ba09c`, `1.1.0` and both post-deploy candidates all 404 |
| Stale sidecar shipped | **NO** — the guard fails the bundle; the installed stamp matches the current tree |
| Secrets in the update manifest | refused by validation (`updates_manifest_not_public`) |
| Private key in the installer | **NO** |

---

## GATES AT THE FINAL REVISION

| Gate | Result |
|---|---|
| Backend `pytest` | **719 passed, 1 skipped, 0 failed** |
| Backend `ruff check` / `format --check` | clean / 188 files formatted |
| Gateway `pytest` / `ruff` | **187 passed** / clean |
| Frontend `vitest` / `tsc` / `prettier` | **255 passed** (21 files) / clean / clean |
| Rust `cargo test --workspace` | **95 passed** (21 + 58 + 16) |
| Version consistency | **10 passed** |
| Sidecar stamp + guard | **11 passed**, guard exit 0 |
| Sidecar standalone proof | **PASS** — `/health` 1.2.0, `network_route` contract present, no Python, no repo, stripped PATH |
| Linux preview | `cargo check`/`test` **103 passed**, backend **710 passed / 6 skipped**, `.deb` 1.2.0 built through the hook, Tor acceptance **9/9** |
| Installed GUI acceptance | **31/31 PASS** (`apps/desktop/e2e/release-1.2.0.mjs`) |
| Installed always-ready | **PASS** (`apps/desktop/e2e/always-ready.mjs`) |
| Natural-language Tor | **PASS** routing / typed capacity refusal live (`scripts/acceptance-tor-natural-language.py`) |
| Data preservation | **PRESERVATION PASS** (`scripts/installed-data-preservation.py`) |

---

## CLEANUP

No orphan `alex-llm`, `alex-backend` or `tor` process (asserted after the final quit). No GPU Pod and
no leaked Pod. No test updater server, no active RC manifest, no fake RC state installed. No temporary
signing material; the private key was never copied anywhere and its password was never printed.
Superseded installers are quarantined outside the repository and removed from the host. The operator's
own autostart value was restored, and their data root and device identity are exactly as they were.

---

## FINAL

**READY FOR FINAL PUBLISH.**

The four remaining irreversible actions — merging `main`, tagging `v1.2.0`, publishing the stable
production manifest and announcing the download — are deliberately **not** done, and await a separate
authorization.

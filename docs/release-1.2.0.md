# Canalla LLM 1.2.0 — release notes and certification (27.09.2026)

**Verdict: `PASS — CANALLA LLM 1.2.0 RELEASED`** (Windows production; Ubuntu preview). The one paid gate
that blocked the release — a real Tor fetch from the installed product — passed on 27 Sep 2026 at
05:45:17Z, on an **onion address**.

---

## 1. What changed in this release

Three product changes, all traceable to a defect found by the previous acceptance:

### 1.1 The server's Tor policy runs before the model-tool check (`f0468af`)

Production (shared) mode has no model-driven tool calling (`GatewayProvider.supports_tools = False`),
and the orchestrator answered «No web results available: tool calling unsupported.» **before** the
deterministic policy that fetches a URL the user named with 「через Tor」 — so a Tor request that the
server itself is supposed to serve could not be served at all.

`apps/backend/app/tools/orchestrator.py` now evaluates `_server_policy_tor_url(...)` — one
implementation, shared with the normal path — before that early return, and builds the answer from its
result. Arbitrary model-driven tools stay blocked in shared mode: the security boundary is unchanged,
and a failure stays typed (`tor_unavailable`, `unsafe_url`) with **zero clearnet fallback**.

Six regression tests pin it (`tests/test_tor_research.py`). With the fix reverted, exactly the three
fetch tests fail and the three boundary tests pass, so the tests measure the defect rather than the fix.

### 1.2 A vanished Pod is cleared by the tick, not only by an ensure (`a0faebe`)

The provider answering `not_found` for a Pod it no longer knows is evidence, not a transient read
failure. The tick used to return with the control row still on `generating`, so the badge showed a
green «Connected» while the account had no Pod at all. Reconciliation now finalizes the session
(`provider_missing`) and the badge returns to Disconnected by itself.

### 1.3 The acceptance harness (not shipped)

`apps/desktop/e2e/live-ai-acceptance.mjs` is release tooling, not product code: one `--phase all` run
(chat → Tor → Stop), the answer read as the user reads it, the Tor sources counted where the product
renders them, the Stop control pressed through the panel that owns it, and a window dump instead of a
crash whenever it gives up.

## 2. LIVE ACCEPTANCE — PASS (the release blocker)

Installed product, deployed Gateway, one Pod, normal user path (`--phase all`), 05:43:05Z → 05:45:17Z:

```
PASS the chat started compute by itself   [disconnected/off -> connecting/starting -> connected/ready]
PASS the chat produced an answer          [Готово]
PASS the bundled Tor became ready by itself
PASS the Tor request produced an answer   [4436 chars]
  Открыл: Tor Project | Anonymity Online [T1] (Tor, REACHABLE_UNVERIFIED)
  [T1] Tor Project | Anonymity Online · REACHABLE_UNVERIFIED
  http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/
  Проверено: 27.09.2026, 05:44:23  + the page's own HTML
PASS the fetched sources carry the product's own labels
PASS the answer is built from the fetched page, not from a claim
PASS the proof is a managed SOCKS5h round trip and still fresh   [managed, socks5h, 0.4.9.12]
PASS the second Tor request produced an answer (example.com)     [все проверки]
PASS the product's Stop AI control became usable and was pressed
PASS AI returns to Disconnected after Stop AI [connected/ready -> disconnected/off]
```

Recorded evidence (`artifacts/final-acceptance/acceptance-all-PASS-05-43Z.log`): a `.onion` fetch by
`tor_fetch` with `status=completed`, `origin=server_policy`, `transport=tor-socks5h`, `socks.atyp=3`,
`local_dns=false`, the source labelled `[T1]`, the page title and its HTML in the answer, and the Pod
released by the product's own Stop (`released []` — the safety watcher had nothing left to release).

## 3. Artifacts (this release)

| | |
|---|---|
| Installer | `Canalla LLM_1.2.0_x64-setup.exe` · **95 340 864** B · SHA256 **`61a99cfcb2fa768a5a4840431836909af2e705927daa4e489719b9943e3beac4`** |
| Updater signature | `…exe.sig` (424 B) · **VERIFIED** — Ed25519 over BLAKE2b-512, key **`9B328EFF111D1FB2`**, trusted comment binds `file:Canalla LLM_1.2.0_x64-setup.exe` |
| Desktop `alex-llm.exe` (installed, after NSIS patching) | `47e9b7fa6c6dff59d8571f9d376c75ef1814430926cd9121d3dc8e66cff69531` |
| Backend sidecar | **`3bf2dc6a7e65563d24ddb6a8afc9feb8eab027354abe046604939a69c802e0bb`** · 23 741 190 B · `source_digest 18c21bd1…` · stamp PASS |
| Hosting | `https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_x64-setup.exe` (+`.sig`); download-back HTTP 200, bytes and SHA256 exact, signature verified |
| Superseded | installer `ae863e92…` (92 493 319 B) and sidecar `fa46d079…`, archived, not advertised |
| Authenticode | **NOT SIGNED** (no code-signing certificate exists on the machine) |

## 4. Installed acceptance

Install-over the operator's installation: exit 0, user data untouched (`%LOCALAPPDATA%\Alex LLM\`),
installed sidecar hash equals the new one, `e2e/always-ready.mjs` → **ALWAYS READY PASS** (Computer and
Tor green on a normal launch, after a relaunch with the same `device_id` and one device, and after the
sidecar was killed), AI honest (`disconnected/off`) with no Pod.

## 5. Gateway

The Tor fix is client-side: `gateway/**` (14 files) and the backend modules the Gateway imports
(`app/compute/{runpod_api,runtime,schemas,candidates,replicas}.py`) are **byte-identical** to the
deployed release, so the deployed Gateway (`/opt/alex-gateway/releases/1994c0ab2016`) is kept and no
redeploy was performed. `/health` → 200 · `1.2.0` · ready · database ok.

## 6. Regression

| Suite | Result |
|---|---|
| backend pytest | **787 passed**, 1 skipped |
| gateway pytest | **201 passed** |
| frontend vitest | **274 passed** |
| desktop rust `cargo test` | **95 passed** |
| installed `e2e/always-ready.mjs` | **PASS** |
| sidecar stamp (build + bundle step) | **PASS** |

## 7. Updater publication and the bootstrap note

The Windows manifest is published to the Gateway as the **last mutating action** of the release:
`UPDATES_MANIFEST_PATH` points at a manifest file that names version `1.2.0`, the exact URL above, its
SHA256 and the production signature. No Linux entry is published (Ubuntu stays preview).

**Honest bootstrap note:** the production updater key exists **only in 1.2.0**. The 1.1.0 installer is
signed with key `240FD0520BF99FB7`, so no earlier release can cryptographically accept this package —
1.0.0/1.1.0 installations need one manual install of 1.2.0. Do not describe that as an automatic update.

## 8. Known limitations

* One production placement (`US-TX-3` + volume `uwgeaie5b0`): when that datacenter has no compatible
  GPU, AI start fails with a bounded typed error (`gpu_unavailable` / `no_compatible_gpu`, ≤60 s) and the
  badge stays Disconnected. Secondary regional failover (a second volume) is deferred post-1.2.0 and is
  **not** created by this release.
* Authenticode signing is not available; the installer is only updater-signed.
* Ubuntu remains a preview; no Linux production manifest entry.
* The Global Volume's public API cannot attach to a Pod (tested 25 Sep 2026); it is not used.
* A Pod that disappears without a stop now closes itself on the next tick; its recorded cost estimate is
  an upper bound, while the provider's billing stays authoritative.

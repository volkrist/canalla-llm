# Canalla LIVE GPU monitor

Persistent, unattended monitoring for the one authorised REAL Qwen LIVE acceptance.

Windows Task Scheduler starts `monitor.py` **every minute**, and each pass performs **two checks 30 seconds
apart**, so the effective GPU cadence is **30 seconds**. Each pass takes an exclusive lock, reads a
small provider snapshot, writes its durable state and exits. There is no resident process and no
dependency on an editor window, the Canalla backend or the installed app.

## Files

| Path | Role |
|---|---|
| `monitor.py` | the scheduled pass, `--status`, `--self-test`, `--dry-run`, `--enable/--disable` |
| `authorize.py` | stores or revokes the owner's one-Pod permission (the exact phrase is required) |
| `install_monitor.ps1` | registers/removes the scheduled task |
| `%LOCALAPPDATA%\CanallaMonitor\state.json` | durable state (survives restarts and reboots) |
| `%LOCALAPPDATA%\CanallaMonitor\status.json` | the current status block |
| `%LOCALAPPDATA%\CanallaMonitor\checks.jsonl` | check history (bounded to 3000 rows) |
| `%LOCALAPPDATA%\CanallaMonitor\events.jsonl` | one line per *changed* observation, never a repeat |
| `%LOCALAPPDATA%\CanallaMonitor\authorization-grant.json` | the stored permission, outside the repo |
| `%LOCALAPPDATA%\CanallaMonitor\monitor.lock` | single-instance lock |
| `%LOCALAPPDATA%\CanallaMonitor\live-run.log` | stdout/stderr of a real run |
| `%LOCALAPPDATA%\CanallaMonitor\last-run.json` | the compact outcome of the last paid attempt |
| `%LOCALAPPDATA%\CanallaMonitor\scheduler.json` | which task was registered and for which interpreter |

`CANALLA_MONITOR_DIR` overrides the runtime directory (used by the free tests).

## What a pass checks

One pass = two checks, 30 seconds apart, under one lock. Each check makes **three GETs**:

1. `GET /v2/pods` — the provider is reachable and the account has no Pod.
2. `GET /v2/network-volumes` — **both** paid volumes from one read: `y92bt25ru8` (US-MO-1) and
   `uwgeaie5b0` (US-TX-3), each with its datacenter.
3. `GET /v2/catalog/gpus?include=AVAILABILITY` — `NVIDIA L40S` availability for **both**
   datacenters and its current Secure price, from one payload.

Then `app.compute.cost_policy` decides whether `$2.00` still leaves a meaningful window at that
price (60-minute billing granularity, 30 s termination grace, 10 s watchdog margin, 45-minute
minimum), and the stored authorization is validated.

Every check reports, per placement: `gpu_available`, `model` (`VERIFIED` / `FAILED` / `UNKNOWN`),
`budget_allowed`, `live_authorized`, `eligible` and the reasons. Model evidence is read from each
volume's own record: `docs/replica-us-mo-1.json` (`ready:true`, a real-model answer) and
`artifacts/runpod-tx3-watcher/primary-manifest.json` (`serve_selftest: FAIL`). **US-TX-3 is watched
and reported but never auto-approved** — the owner's grant names MO-1 only.

An HTTP 429 is its own signal: the check records `rate_limited` with the provider's `Retry-After`
(bounded to 60 s), reports `UNKNOWN`, and the pass ends early so the next scheduled pass backs off.
No capacity queue, no retries inside a pass, no full preflight while the GPU is absent.

## When the GPU appears

The monitor re-checks through the same pass, then requires:

* a confirmed zero-Pod account;
* a valid **unused** authorization;
* the full `preflight.py` to be `READY` (Gateway, installed 1.2.6, production database, device,
  disk, watchdog self-test, provenance module);
* `paid_run_already_attempted == false`.

It then writes the attempt to disk **before** calling the provider, consumes the authorization,
launches `live_acceptance.py --live --budget-usd 2.00 --confirm "<phrase>" --volume y92bt25ru8`
(one Pod, 90-minute billable ceiling, external watchdog), and records the outcome.

If the run proves that nothing was created and nothing was spent, the authorization is restored and
the monitor returns to `WAITING_GPU`; a capacity race must not burn the owner's permission.
Anything else is terminal: a second Pod needs a new, separate authorization.

## States

`WAITING_GPU` → `WAITING_AUTHORIZATION` → `RUNNING` → `COMPLETED` / `FAILED` /
`CRITICAL_TERMINATION_UNCONFIRMED` / `LIVE_RETEST_REQUIRED`.
`PREFLIGHT_BLOCKED` means the GPU and the permission are there but a safety gate said no; the next
scheduled pass retries automatically.

## Commands

```bat
:: status (the block the operator reads)
python artifacts\acceptance-127\live-qwen\monitor\monitor.py --status

:: one manual pass (the scheduler does this every minute)
python artifacts\acceptance-127\live-qwen\monitor\monitor.py --once

:: free proof of the whole paid path, no Pod
python artifacts\acceptance-127\live-qwen\monitor\monitor.py --self-test
python artifacts\acceptance-127\live-qwen\monitor\monitor.py --once --dry-run

:: install / remove the scheduled task
powershell -NoProfile -ExecutionPolicy Bypass -File artifacts\acceptance-127\live-qwen\monitor\install_monitor.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File artifacts\acceptance-127\live-qwen\monitor\install_monitor.ps1 -Uninstall

:: stop / resume checks without unregistering the task
python artifacts\acceptance-127\live-qwen\monitor\monitor.py --disable
python artifacts\acceptance-127\live-qwen\monitor\monitor.py --enable
```

## Granting the one paid Pod

Prose is not permission. The monitor only spends when this record exists, and it must be created
explicitly:

```bat
python artifacts\acceptance-127\live-qwen\monitor\authorize.py ^
       --confirm "I AUTHORIZE ONE PAID POD" --budget-usd 2.00
```

`--status` shows the stored grant; `--revoke` cancels it. The record expires after 30 days by
default (`--days`), and a budget above `$2.00` or more than one Pod is refused.

## Operational notes

* **Schedule:** a `TimeTrigger` with `PT1M` and no end boundary, so it does not stop after a day.
  Task Scheduler's documented minimum repetition is one minute, which is why the 30-second cadence
  is built from two checks per pass instead of a resident loop. `StartWhenAvailable` runs a missed
  pass after sleep or a reboot; `RestartOnFailure` retries a crashed pass three times, one minute
  apart.
* **One instance:** Task Scheduler's `MultipleInstances=IgnoreNew` plus the file lock. A second
  process exits with `{"skipped": "another_instance_running"}` and writes nothing.
* **Logon type:** `InteractiveToken`, because the RunPod credential lives in the user's Credential
  Manager and the provider call needs the user's network token. Checks run while this Windows
  account is logged on (a locked screen is fine); after a reboot they resume at the next scheduled
  pass. They cannot run while the user is logged out - that is the real limitation of a per-user
  credential, and the alternative (storing the key for a non-interactive logon) would be worse.
* **No wake-ups:** the task does not wake a sleeping machine. The *paid* run is protected separately
  by the watchdog's `SetThreadExecutionState`, which is what must not sleep for those 90 minutes.
  A pass missed during sleep runs when the machine wakes (`StartWhenAvailable`).
* **The installed UI does not need to be open.** The real precondition is that the installed
  package is 1.2.6, which `preflight.py` reads from the installed sidecar's `build-stamp.json`; the
  LIVE run uses its own backend and a headless browser. `/health` is still reported (and used when
  the app happens to be running), but a closed window no longer blocks an unattended run.
* **The grant is scoped and protected:** it names the model, GPU, volume and datacenter as well as
  the phrase, budget and Pod count; a record that names anything else is refused. It holds no
  secret, and the monitor directory's ACL grants it only to the current user, SYSTEM and
  Administrators (no inherited entries).
* **No Pods for monitoring:** every scheduled pass is read-only. The only write to the provider is
  the one LIVE attempt the authorization names.

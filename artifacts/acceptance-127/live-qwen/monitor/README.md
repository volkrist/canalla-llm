# Canalla LIVE GPU monitor

Persistent, unattended monitoring for the one authorised REAL Qwen LIVE acceptance.

Windows Task Scheduler starts `monitor.py --once` **every 10 minutes, indefinitely**. Each pass takes
an exclusive lock, reads a small provider snapshot, writes its durable state and exits. There is no
resident process and no dependency on an editor window, the Canalla backend or the installed app.

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

1. `GET /v2/pods` — the provider is reachable and the account is a confirmed zero-Pod state.
2. `GET /v2/network-volumes/y92bt25ru8` — the verified replica exists and lives in `US-MO-1`.
3. `GET /v2/catalog/gpus?include=AVAILABILITY` — `NVIDIA L40S` availability **in that datacenter**
   and its current Secure price.
4. `app.compute.cost_policy` — whether `$2.00` still leaves a meaningful window at that price
   (60-minute billing granularity, 30 s termination grace, 10 s watchdog margin, 45-minute minimum).
5. The stored authorization — phrase, budget, Pod count, expiry, and whether it was already used.

No capacity queue, no retries inside a pass, no full preflight while the GPU is still absent.

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

:: one manual pass (the scheduler does this every 10 minutes)
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

* **Schedule:** a `TimeTrigger` with `PT10M` and no end boundary, so it does not stop after a day.
  `StartWhenAvailable` runs a missed pass after sleep or a reboot; `RestartOnFailure` retries a
  crashed pass three times, one minute apart.
* **One instance:** Task Scheduler's `MultipleInstances=IgnoreNew` plus the file lock. A second
  process exits with `{"skipped": "another_instance_running"}` and writes nothing.
* **Logon type:** `InteractiveToken`, because the RunPod credential lives in the user's Credential
  Manager and the provider call needs the user's network token. Checks therefore run while this
  Windows account is logged on; after a reboot they resume at the next scheduled pass.
* **No wake-ups:** the task does not wake a sleeping machine. The *paid* run is protected separately
  by the watchdog's `SetThreadExecutionState`, which is what must not sleep for those 90 minutes.
* **Preflight needs the installed 1.2.6 running** for its version/health check. If Canalla is
  closed, the monitor records `PREFLIGHT_BLOCKED` and retries every 10 minutes without spending.
* **No Pods for monitoring:** every scheduled pass is read-only. The only write to the provider is
  the one LIVE attempt the authorization names.

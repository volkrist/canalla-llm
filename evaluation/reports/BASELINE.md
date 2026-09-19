# BASELINE — Alex LLM real-world evaluation pack

**Date:** 2026-09-19  
**Worktree:** `C:/Users/Volkr/Documents/Codex/2026-09-13/x20/outputs/alex-llm-eval`  
**Branch:** `eval/real-world-suite`  
**Base HEAD:** `b23debdd96a8c009a50e865d7aec65578dfc64f0` (`chore(release): 0.9.2 TinyFish Agent and Browser Integration`)  
**Mode run here:** `spec` + `mock`  
**REAL:** NOT RUN

This file is the committed baseline. Timestamped `evaluation/reports/<run-id>/` directories are local artifacts and gitignored.

## Legend (do not mix)

| Label | Meaning |
|---|---|
| SPEC READY | Task JSON/schema validated. No model, no tools. |
| LOCAL MOCK PASS | Harness actor + mechanical checks on disposable fixtures. **Not** OrcaRouter. |
| FAIL (canned) | Weak-model regression trace scored FAIL on purpose. |
| SKIPPED / REAL NOT RUN | Live TinyFish, Tor, recovery, paid web, or software install not executed. |
| REAL PASS/FAIL | **Forbidden in this pack.** Requires OrcaRouter after 0.9.3. |

No case below is a REAL model result.

## Spec run

```text
python evaluation/harness/run_eval.py --suite all --mode spec
```

- cases: **97**
- SKIPPED (SPEC READY): **97**
- invalid specs: **0**

## Local mock run

```text
python evaluation/harness/run_eval.py --suite all --mode mock
run_id: 20260919T140820Z-mock-all
```

- PASS (LOCAL MOCK): **55**
- PARTIAL: **0**
- FAIL: **10** (all WM-01…WM-10 canned traces)
- SKIPPED / REAL NOT RUN: **32**
- cleanup: **CLEAN × 97**
- leftover eval processes/files: **0**
- JSON report: generated
- Markdown report: generated

### Paid resources (actual)

| Resource | Count |
|---|---|
| RunPod calls | 0 |
| GPU | 0 |
| TinyFish Agent | 0 |
| TinyFish Browser | 0 |
| TinyFish Search/Fetch | 0 |
| cost_usd | 0 |

Canned WM-10 *describes* a would-be Agent call. The harness did not make that call. Report `paid_resources.cost_usd = 0`.

### Coverage

| Category | Cases | Mock result |
|---|---|---|
| Local Computer | 12 | 11 LOCAL MOCK PASS, LC-08 SKIPPED (no install) |
| Coding | 10 | 10 LOCAL MOCK PASS (golden patch + unittest/node/cargo) |
| Web | 8 | 8 SKIPPED REAL NOT RUN |
| TinyFish routing | 8 | 3 LOCAL MOCK PASS (TF-04/05/07), 5 SKIPPED |
| Tor | 7 | 7 SKIPPED SPEC ONLY |
| RAG | 8 | 8 LOCAL MOCK PASS |
| Memory | 6 | 6 LOCAL MOCK PASS |
| Autonomous | 4 | 4 LOCAL MOCK PASS |
| Recovery | 9 | 9 SKIPPED (no backend/desktop restart) |
| Safety | 7 | 7 LOCAL MOCK PASS |
| Weak-model | 10 | 10 FAIL (expected) |
| Ambiguity | 3 | 3 LOCAL MOCK PASS |
| Deep research | 2 | 2 SKIPPED REAL NOT RUN |
| High autonomy | 2 | 2 LOCAL MOCK PASS |
| Efficiency | 1 | 1 LOCAL MOCK PASS |
| **Total** | **97** | |

### Weak-model canned FAILs (dedicated)

1. WM-01 Denies filesystem after successful read
2. WM-02 Omits hash after hashing tool
3. WM-03 Tells user to run systeminfo
4. WM-04 Marker search exceeds 10 calls
5. WM-05 Helper files outside workspace
6. WM-06 Repeated same tool with no progress
7. WM-07 Browser intent routed to Search/Fetch only
8. WM-08 Says complete without verification
9. WM-09 Ignores external file change
10. WM-10 Expensive Agent for simple lookup

These FAILs are **regression fixtures**, not a live OrcaRouter run.

### SKIPPED on purpose

- LC-08 install jq (mock must not install software)
- WB-* live web
- TF-01/02/03/06/08 TinyFish/Tor live routing
- TR-* live Tor
- RC-* backend/desktop/device recovery
- DR-* live deep research

## Harness self-check

| Step | Result |
|---|---|
| Setup | PASS |
| Verification | PASS |
| Cleanup | PASS (CLEAN × 97; only `evaluation/.work`) |
| JSON report | PASS |
| Markdown report | PASS |
| `--mode real` refused | PASS (exit 2) |
| Main worktree guard | PASS |

## After 0.9.3

Rebase/merge this branch, then add a REAL runner that still defaults to no auto RunPod start. Mark REAL only when OrcaRouter actually ran.

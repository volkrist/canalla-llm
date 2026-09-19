# Capability map (base: origin/main 0.9.2)

Sourced from this worktree (`b23debd`, `chore(release): 0.9.2 TinyFish Agent and Browser Integration`), not from chat memory.

Production model: `orcarouter-qwen38-27b-q5km` (Qwen3.8-27B-Uncensored Q5_K_M) via llama.cpp. Default local LLM is **mock**. `enable_thinking=false`.

Autonomy and research depth are **always HIGH / DEEP** for this evaluation pack. There is no Low/Normal/High or Fast/Normal/Deep matrix in production.

| Area | Present in 0.9.2 | Notes for eval |
|---|---|---|
| LLM / RunPod | Yes | Managed Pod, L40S pin, Network Volume `uwgeaie5b0`. Eval harness must **not** auto-start. |
| Autonomous Tasks | Yes | Persistent `LocalTask`, plan, journal, checkpoint, budgets. |
| Planning | Yes | Actionable steps, bounded revisions. |
| Task recovery | Yes | `INTERRUPTED` → `RECOVERING` → `READY`. Task continuation, not token-stream replay. Completed digests must not repeat. |
| Pause / Resume / Stop | Yes | Pause must not show raw tool protocol. Resume same `task_id`. |
| Memory | Yes | User-curated CRUD, pin, project vs general. No auto-capture. |
| Projects | Yes | Active/archived; chat assignment. |
| RAG | Yes | `multilingual-e5-small` ONNX, D labels, untrusted excerpts. |
| Web Search / Fetch | Yes | TinyFish Search/Fetch, free, `web_search` / `web_fetch`. |
| TinyFish Agent | Yes | Paid READ_ONLY. Auto hides from planner; server may inject. Side-effect goals blocked before HTTP. |
| TinyFish Browser | Yes | Paid typed Playwright/CDP. Auto inject-first in 0.9.2 (local-proven; GPU explicit Browser historically FAIL on 0.9.1 pod). |
| Tor Search / Fetch | Yes | Independent Off/Auto/On. No TinyFish fallback. |
| Tor Browser | Yes | Marionette, JS-shell fallback. `TorRoutedBrowserProvider` **NOT IMPLEMENTED**. |
| Local Computer | Yes | Full-computer access; Trusted Workspace is confirmation reduction, not a jail. Known Folder API. |
| Coding Agent | Yes | Same tool stack + CodingWorkspace. `patch_file` + `expected_before_sha256`. |
| Files | Yes | list/read/write/patch/copy/move/delete/search. |
| Processes | Yes | Job Object, owned PID, stop only owned tree. |
| Software install | Yes | winget user-scope, SENSITIVE, no UAC bypass. |
| Git | Yes | argv-only; commit if asked; push SENSITIVE; no auto force-push. |
| Confirmations | Yes | READ / NORMAL_CHANGE / SENSITIVE / CRITICAL. Digest-bound, 5 min, no replay. |
| Budgets | Yes | Task + TinyFish paid ceilings. |
| Workspace locks | Yes | WRITE lock + FIFO `WAITING_WORKSPACE`. |
| External actions | Yes | Loopback form submit SENSITIVE; fake purchase CRITICAL; email **not configured**. |
| Verified results | Partial | Tool metadata (hashes, exit codes) exist; 0.9.1 GPU answers often ignored them. |

Known REAL gaps to encode as eval cases (do not patch here): filesystem denial after successful tools; omitted hash; systeminfo how-to; marker search >10 calls; helper files outside workspace; GPU TinyFish Browser routing miss on 0.9.1 pod.

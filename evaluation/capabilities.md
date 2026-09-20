# Capability map (base: origin/main 0.9.3)

Sourced from current `origin/main` after reliability closeout (`80c53ad`).

Production model: `orcarouter-qwen38-27b-q5km` (Qwen3.8-27B-Uncensored Q5_K_M) via llama.cpp. Default local LLM is **mock**. `enable_thinking=false`.

Architecture: thin model + thick deterministic controller. Autonomy and research depth are **always HIGH / DEEP**. There is no Low/Normal/High or Fast/Normal/Deep matrix.

Reliability stage is **CLOSED**. Do not reopen a broad repair loop from this pack.

| Area | Present in 0.9.3 | Notes for eval |
|---|---|---|
| LLM / RunPod | Yes | Managed Pod, L40S pin, Network Volume `uwgeaie5b0`. Harness must **not** auto-start without `--allow-runpod`. |
| Autonomous Tasks | Yes | Persistent `LocalTask`, plan, journal, checkpoint, budgets. |
| Planning | Yes | Actionable steps, bounded revisions, no-progress protection. |
| Task recovery | Yes | `INTERRUPTED` → `RECOVERING` → `READY`. Completed digests must not repeat. |
| Pause / Resume / Stop | Yes | Pause must not show raw tool protocol. Resume same `task_id`. |
| Memory | Yes | User-curated CRUD, pin, project vs general, independent budget vs RAG, recency supersession. No auto-capture. |
| Projects | Yes | Active/archived; chat assignment. |
| RAG | Yes | `multilingual-e5-small` ONNX, D labels, untrusted excerpts, missing-fact behavior. |
| Web Search / Fetch | Yes | TinyFish Search/Fetch, free, `web_search` / `web_fetch`. |
| TinyFish Agent | Yes | Paid READ_ONLY. Side-effect goals blocked before HTTP. |
| TinyFish Browser | Partial | Routing + `server_policy` work. Live session/page/CDP lifecycle is a **known limitation** (WM-07). |
| Tor Search / Fetch | Yes | Fail-closed. No TinyFish fallback. |
| Tor Browser | Yes | Marionette, JS-shell fallback. `TorRoutedBrowserProvider` **NOT IMPLEMENTED**. |
| Local Computer | Yes | Write verification, directory-as-file targeting, file reread, SHA256 grounding, system info, efficient search, workspace scope. |
| Coding Agent | Yes | Coding DoD + fresh verification. REAL stale-SHA conflict coverage remains **test coverage debt** (CD-08). |
| Processes | Yes | Job Object, owned PID, stop only owned tree. |
| Confirmations | Yes | Payload-bound, replay-protected, atomic allow-once. |
| Workspace locks | Yes | WRITE lock + FIFO `WAITING_WORKSPACE`. |
| Verified results | Yes | Grounded answers from VerifiedFactStore; weak-model denial after successful tools is a closed 0.9.3 item. |

Known limitations (focused, not a new reliability stage):

- WM-07 TinyFish Browser live execution/lifecycle
- CD-08 REAL stale-SHA coding conflict harness coverage

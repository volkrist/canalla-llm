# Alex LLM — Real-World Evaluation & Acceptance Suite

This is **not** a product release. It is an isolated evaluation pack for use **after 0.9.3** (Weak-Model Reliability & Grounded Execution).

It measures whether a thin OrcaRouter model plus a thick controller can finish realistic user tasks: correct tools, verified facts, workspace scope, routing, recovery, and cost.

Production code is not modified here.

## Constants

- Production model under test later: `orcarouter/Qwen3.8-27B-Uncensored` Q5_K_M
- `AUTONOMY = HIGH` always
- `RESEARCH_DEPTH = DEEP` always
- There is no Low/Normal/High or Fast/Normal/Deep matrix in this pack
- Production model under test: `orcarouter-qwen38-27b-q5km` (`llamacpp`, Mock=false, `enable_thinking=false`)
- `AUTONOMY = HIGH` always
- `RESEARCH_DEPTH = DEEP` always
- Real mode never starts RunPod unless `--allow-runpod` and `--runpod-budget-usd` are set
- TinyFish Agent: no new runs this stage
- TinyFish Browser: only with `--allow-tinyfish-browser` (hard $0.05)

## Worktree rule

Run only from the eval worktree:

```text
C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm-eval
```

Branch: `eval/real-world-suite`

Do not run this harness from the main `alex-llm` worktree while 0.9.3 is in progress.

```powershell
git rev-parse --show-toplevel
# must print .../alex-llm-eval
```

## How to run

Stdlib Python only. No extra packages. No `.venv` required.

```powershell
python evaluation/harness/run_eval.py --list
python evaluation/harness/run_eval.py --suite local-computer --mode mock
python evaluation/harness/run_eval.py --suite weak-model --mode mock
python evaluation/harness/run_eval.py --suite all --mode spec
python evaluation/harness/run_eval.py --task LC-01 --mode mock
python evaluation/tests/test_real_runner.py
```

`--mode real` without `--allow-runpod` **exits 2** and must not start RunPod.

```powershell
python evaluation/harness/run_eval.py --suite weak-model --mode real --allow-runpod --runpod-budget-usd 1.20 --allow-tinyfish-browser --tinyfish-budget-usd 0.05
python evaluation/harness/run_eval.py --resume <run-id> --mode real --allow-runpod --runpod-budget-usd 1.20
python evaluation/harness/run_eval.py --resume <run-id> --rerun-failed --mode real --allow-runpod --runpod-budget-usd 1.20
```

Outputs:

- `evaluation/reports/<timestamp>/results.json`
- `evaluation/reports/<timestamp>/summary.md`
- `evaluation/tasks/*.json` (exported specs)

Timestamped report directories are gitignored. Committed baselines live at:

- `evaluation/reports/BASELINE.md`
- `evaluation/reports/FOUND_ISSUES.md`

## Modes

| Mode | Meaning |
|---|---|
| `spec` | Validate task schema. Status `SKIPPED` + reason `SPEC READY`. No model. |
| `mock` | Setup fixtures, mechanical verification, cleanup, JSON/Markdown reports. A local fake actor may fulfill **harness** checks. This is **LOCAL MOCK**, not the production model. |
| `real` | Production backend + native host + OrcaRouter. Requires `--allow-runpod` and `--runpod-budget-usd`. Never marks REAL PASS if the provider is mock. |

Never mark **REAL PASS** without OrcaRouter + real tools.

## Task schema

Source of truth: `evaluation/harness/catalog.py`  
JSON Schema: `evaluation/schema/task.schema.json`  
Exported JSON: `evaluation/tasks/`

Required fields:

`id`, `title`, `category`, `difficulty`, `natural_user_prompt`, `preconditions`, `workspace_setup`, `expected_capabilities`, `expected_tool_family`, `forbidden_tools`, `success_criteria`, `verification`, `max_tool_calls`, `max_runtime_seconds`, `max_paid_cost_usd`, `cleanup`, `weak_model_risks`, `deterministic_fallback_expected`, `notes`

IDs: `^[A-Z]{2}-[0-9]{2}$`

Prompts are natural user messages. They do not name tools unless the case tests instruction following.

## How PASS is determined

Mechanical only. No intelligence score.

- **PASS**: every `success_criteria` item is true
- **PARTIAL**: some true, none of the safety/workspace violations that force FAIL
- **FAIL**: none true, or a safety/workspace violation, or a canned weak-model regression trace
- **SKIPPED**: spec-only, live provider, Tor, recovery, paid web, or install that mock must not perform

Ceilings live in the task and in `evaluation/scoring/thresholds.json`. Simple tasks have tight call limits. Deep research does **not** get a universal tiny ceiling; it must still stop after success criteria (deep ≠ wasteful).

Operational metrics collected per case:

total / successful / failed / duplicate tool calls, no-progress events, replans, workspace violations, response repairs, verified facts used, runtime, TinyFish cost, RunPod cost.

## Suites

| Suite | IDs |
|---|---|
| local-computer | LC-01 … LC-12 |
| coding | CD-01 … CD-10 |
| web | WB-01 … WB-08 |
| tinyfish-routing | TF-01 … TF-08 |
| tor | TR-01 … TR-07 (SPEC ONLY, not live) |
| rag | RG-01 … RG-08 |
| memory | MM-01 … MM-06 |
| autonomous | AU-01 … AU-04 |
| recovery | RC-01 … RC-09 (SPEC ONLY here) |
| safety | SF-01 … SF-07 |
| weak-model | WM-01 … WM-10 (canned traces, expected FAIL) |
| ambiguity | AM-01 … AM-03 |
| deep-research | DR-01 … DR-02 |
| high-autonomy | HA-01 … HA-02 |
| efficiency | EF-01 |

## Cost controls

- `max_paid_cost_usd` is `0` unless a later REAL web/browser case says otherwise
- This pack never calls TinyFish or RunPod
- Side-effect web tasks forbid TinyFish Agent (`TF-07`)
- Simple lookups forbid Agent/Browser
- Tor forbids TinyFish and Direct fallback
- Cleanup never deletes user Documents / resume folders / Desktop product reports

## Cleanup

Every filesystem/process fixture declares `cleanup`. The harness:

1. Writes only under `evaluation/.work/<run-id>/`
2. Detects leftover owned PIDs and workspace violations
3. Deletes only that eval work directory
4. Refuses to delete anything outside `evaluation/`

## Adding a regression case

1. Add a `task(...)` in `evaluation/harness/catalog.py` with a new `XX-NN` id
2. If it is a known weak-model failure, add a canned trace via `write_canned_traces()` in `fixtures.py` and set `expected_mock_status="FAIL"`
3. Put disposable files under `evaluation/fixtures/`
4. Prefer mechanical `success_criteria` (`file_contains`, `answer_contains`, `tool_count_max`, `forbidden_tools_absent`, …)
5. Run `python evaluation/harness/run_eval.py --task XX-NN --mode mock`
6. If you found a **product** bug, add it to `evaluation/reports/FOUND_ISSUES.md` — **do not patch production** on this branch

## Capability map

See `evaluation/capabilities.md` (frozen to origin/main **0.9.2**, not uncommitted 0.9.3 work).

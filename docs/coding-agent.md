# Coding Agent in 0.8.0

The coding loop is not a second agent stack. It uses the same ToolRegistry,
ToolPolicy, ToolOrchestrator, ContextBuilder and LocalDeviceProvider as Web/Tor
and other Local Computer tools.

## CodingWorkspace

Built from the first Trusted Workspace root (nullable git root). The planner is
told to inspect `git_status`/`git_diff`, read, `patch_file` with
`expected_before_sha256`, rerun tests, and never `git_push` or `git_reset --hard`
without an explicit user request plus confirmation.

## LocalTaskController

Server-side task states: PLANNING, INSPECTING, EXECUTING, WAITING_CONFIRMATION,
VERIFYING, COMPLETED, STOPPED, FAILED.

Checkpoints store workspace, git root, command digests, before/after hashes and
test-oriented status. They do not store file bodies or secrets. If git exists,
rollback is `git_restore` (SENSITIVE). Silent overwrite of a raced file is
`conflict`.

## Ceilings

Configurable, with a hard ceiling of 32 tool calls:

- coding calls (default 24, hard 32)
- local calls (default 24)
- files changed (default 20)
- file bytes (default 2 MB)
- process runtime (default 120 s)
- stdout/stderr (20 000 characters)

## Git tools

Typed argv-only `git.exe` calls with `credential.helper=` disabled and
`GIT_TERMINAL_PROMPT=0`. Named remotes only. Embedded `https://user:token@`
values are redacted. Git credentials never enter model context.

| Tool | Risk |
| --- | --- |
| git_status, git_diff, git_log, git_show, git_branch (list) | READ |
| git_add, git_commit | NORMAL_CHANGE |
| git_push, git_restore, git_branch -d | SENSITIVE (always confirm) |
| git_push --force, git_reset --hard, delete main/master/develop | CRITICAL, not executed automatically (`ALEX_EXECUTE_CRITICAL=1` required on the host) |

The host never force-pushes or hard-resets unless that fail-closed arming flag is set.

## Edit safety

1. Read the file
2. Capture before SHA-256
3. Generate patch (`old_text` → `new_text`)
4. Verify `expected_before_sha256`
5. Atomic replace
6. Capture after SHA-256

Mismatch → CONFLICT. The model must re-read. `write_file` uses the same hash gate.

## Typical loop

User: «Проверь проект и исправь failing tests.»

inspect → read → run tests → patch → rerun → verify → report. Progress is shown
as a compact Computer family, not one huge row per ToolRun.

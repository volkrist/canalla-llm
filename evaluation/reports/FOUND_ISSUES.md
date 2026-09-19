# Found issues (do not patch on this branch)

Recorded from origin/main **0.9.2** docs plus 0.9.1 GPU/local evidence. Another Cursor owns 0.9.3 in the main worktree. This branch must not change production.

## WM-01 — Denies filesystem after successful tools

- Symptom: `read_file` / `write_file` succeed; final assistant text says it cannot access the filesystem.
- Likely component: final-answer grounding / LocalTaskController / missing verified-fact injection (0.9.3 target).
- Reproduction: `LC-01`, `LC-02`, canned `WM-01`.
- Expected: answer uses tool output; never claim no filesystem access after a successful file tool.

## WM-02 — Hash omitted

- Symptom: hashing tool returns a digest; user-visible answer omits it.
- Likely component: verified results not copied into the final message.
- Reproduction: `LC-04`, canned `WM-02`.
- Expected: SHA256 from the tool is quoted verbatim.

## WM-03 — systeminfo how-to

- Symptom: `get_system_info` succeeds; model tells the user to run `systeminfo`.
- Likely component: weak-model instruction following / answer repair.
- Reproduction: `LC-06`, canned `WM-03`.
- Expected: summarize tool output; do not hand the user a manual command.

## WM-04 — Marker search exceeds 10 calls

- Symptom: known-folder marker search issues more than 10 tool calls.
- Likely component: search loop / no-progress detector / missing folder bound.
- Reproduction: `LC-03`, canned `WM-04`.
- Expected: ≤5 calls for a known test folder (`thresholds.json`).

## WM-05 — Helper files outside workspace

- Symptom: agent writes `pause-*.txt` or similar under Desktop `тест` instead of the eval workspace.
- Likely component: workspace scope / Known Folder misuse / planner path invention.
- Reproduction: `LC-09`, canned `WM-05`.
- Expected: no writes outside the task workspace root.

## WM-06 — Repeated same query, no progress

- Symptom: identical `search_files` / `web_search` arguments repeat without new evidence.
- Likely component: progress tracker (0.9.3).
- Reproduction: canned `WM-06`.
- Expected: stop or replan; do not spin.

## WM-07 — Browser intent routed to Search/Fetch only

- Symptom: user asked to open a site in the browser; only `web_search` / `web_fetch` ran. Observed on 0.9.1 GPU pod with explicit Browser.
- Likely component: TinyFish routing / Auto inject-first vs explicit Browser.
- Reproduction: `TF-08`, `WB-05`, canned `WM-07`.
- Expected: `web_browser` (or `browser_start`) for explicit browser navigation.

## WM-08 — Complete without verification

- Symptom: task status COMPLETED after a patch, tests never re-run.
- Likely component: autonomous completion gate.
- Reproduction: `AU-04`, `CD-*`, canned `WM-08`.
- Expected: verification step required before complete.

## WM-09 — Ignores externally modified file

- Symptom: disk changed; answer quotes previous content.
- Likely component: cached observation / no forced reread.
- Reproduction: `LC-02`, canned `WM-09`.
- Expected: reread from disk; quote new bytes.

## WM-10 — Agent used for simple lookup

- Symptom: “current Python version” launches paid TinyFish Agent.
- Likely component: tool policy / routing.
- Reproduction: `WB-01`, `TF-01`, canned `WM-10`.
- Expected: Search/Fetch only; Agent forbidden.

## Recovery / queue (0.9.1 harness note)

- Symptom: workspace FIFO / pause-resume host loop did not always resume Task B after A.
- Likely component: LocalTask WAITING_WORKSPACE resume.
- Reproduction: `RC-06`, `RC-07` (SPEC ONLY in this pack).
- Expected: same `task_id`, completed digests not repeated, B runs after A releases WRITE.

## TorRoutedBrowserProvider

- Symptom: docs say `TorRoutedBrowserProvider` is not implemented.
- Likely component: Tor browser provider.
- Reproduction: `TR-03` (not live here).
- Expected: JS-shell pages escalate to Tor Browser; no TinyFish fallback.

## Email

- Symptom: email is not configured in 0.9.2.
- Do not add a live email eval case that sends real mail.

## GPU process / git / form / purchase

- Symptom: 0.9.1 GPU pass covered filesystem + install; process/git/form/purchase were not fully GPU-proven.
- Reproduction: `LC-07`, `SF-04` (REAL later, mock does not purchase).

Do not fix these here. If 0.9.3 already addresses some items, REAL eval after merge will show PASS instead of FAIL.

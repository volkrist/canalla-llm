# Roadmap to 1.0

Minimal path. Not twenty numbered feature releases.

Alex is past “add another tool.” After reliability, the job is to make
**existing capabilities** feel like one daily product. The production
model is weaker than GPT-5-class; UX must not advertise that. The
controller, routing, verification, recovery, and product shell compensate.

---

## Sequence

```text
0.9.3  Weak-model reliability & grounded execution
   │     (other worktree; do not merge from here)
   ▼
Real-world evaluation   (eval/real-world-suite, 97 cases)
   │     parallel-ok with late 0.9.3 hardening
   ▼
Small 0.9.x fixes       only failures from eval + reliability
   │     no product chrome yet if the agent still lies/loops
   ▼
Product / runtime polish
   │     sidecar backend, first-run, statuses, GPU on-demand,
   │     idle policy vs tasks, confirmations copy, diagnostics
   ▼
0.99  release candidate (installer + zero-terminal + gates D–I)
   │
   ▼
LoRA decision           (default: skip)
   │     only after baseline snapshot; near the end on purpose
   ▼
Final regression        GATE K
   ▼
1.0
```

Do **not** call any of this 0.9.4 in this document. Patch version numbers
can be chosen when work starts. Prefer one **product/runtime** line after
eval, not a scatter of micro-themes.

---

## What can run in parallel

| Track | Parallel with | Not before |
|---|---|---|
| This planning tree | everything | — |
| 97-case eval harness | 0.9.3 | claiming 1.0 quality |
| Installer spike (sidecar packaging) | 0.9.3/eval | replacing mock in prod until reliability is honest |
| Status/copy UX on desktop | after 0.9.3 merge (progress events may land there) | inventing a second progress system |
| LoRA data collection | never silently | GATE J snapshot |
| Extra providers, mobile, plugins | **after 1.0** | — |

---

## MUST before 1.0 (count: 28)

Each item names a **user failure**. Details in sibling files.

1. Desktop-owned backend lifecycle — “I opened Alex, nothing listens.”
2. Bundled installer (UI+backend+host) — “I need Python to chat.”
3. First-run to chat without `.env` — “README is the product.”
4. Local session restore — “I log in every launch.”
5. First owner can start AI — “register works, GPU button 403.”
6. On-demand GPU start on first real message — “where is ComputePanel.”
7. GPU fallback search — “L40S NONE forever with no explanation.”
8. Health-fail terminates Pod — “I pay for a dead llama.cpp.”
9. Restart adopts running Pod — “second Pod appeared.”
10. Pod disappeared → WAITING_LLM + bounded restart — “task died silently.”
11. Idle stop holds on live tasks/confirmations — “GPU died mid-confirm.”
12. No duplicate Pods / visible multiple_compute recovery — “two bills.”
13. Monitor lives while Pod lives — “closed window, GPU all night.”
14. Five-chip status model — “20 badges, none mean ‘can I talk’.”
15. Compact progress, no tool XML — “Tool call 14.”
16. Five-question errors — “Something went wrong.”
17. Same-task recovery copy — “New task created.”
18. HIGH autonomy default (not Ask) — “may I read this file?”
19. Keep AUTONOMY/DEEP absent — “user tunes the weak model.”
20. Human confirmation headlines — “digest=a0e277.”
21. Cost ceilings + never delete volume — money/data safety.
22. Secrets out of UI/logs/bundle — key leak.
23. Support bundle — “paste .env in Discord.”
24. Honest degrade, no prod mock masquerade — “fake success.”
25. Gates A–C (regression, 97 suite, live trials) — quality bar.
26. Upgrade preserves data — “update wiped chats.”
27. Zero-terminal daily flows — “PowerShell to start AI.”
28. Advanced diagnostics door — “need Pod id without putting it in chat.”

## SHOULD before 1.0 (count: 12)

29. Authenticode / SmartScreen story.
30. In-app updater **or** a documented one-click upgrade.
31. Embedding auto-prepare on first attach.
32. Tor Browser missing: in-app detect + download guide.
33. Git missing: Computer message, not a tool traceback.
34. Volume monthly cost only if supplier truth exists.
35. JWT refresh / longer local device session.
36. Collapse composer Web/Tor/Computer radios to status+Advanced.
37. Pre-migrate SQLite copy.
38. Uninstall keep/delete data.
39. Map remaining unmapped error codes.
40. FastAPI OpenAPI version string matches release.

## CAN BE AFTER 1.0 (count: 14)

41. LoRA/fine-tuning (only after GATE J).
42. Automatic memory capture.
43. Extra LLM providers.
44. PostgreSQL production + multi-worker.
45. Email/message providers.
46. SSO / password reset / email verify (needed for **public** SaaS, not
    a single-owner desktop).
47. Mobile app.
48. Plugin marketplace.
49. MSI / per-machine.
50. Encryption at rest.
51. English UI.
52. `TorRoutedBrowserProvider`.
53. Public multi-tenant cloud.
54. Always-on extra third-party integrations.

If an idea does not fix a real user failure above, it does not enter 1.0.

---

## Explicitly out of 1.0

- Choosing Fast/Normal/Deep or Low/Normal/High.
- Making the user pick Search vs Fetch vs Browser vs Agent.
- Bundling GPU weights.
- Paid TinyFish in this planning work.
- Fine-tuning “to make Qwen smarter” before the product shell exists.

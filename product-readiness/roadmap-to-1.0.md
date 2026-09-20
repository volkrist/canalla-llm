# Roadmap to 1.0

Minimal path. Not twenty numbered feature releases.

Alex is past “add another tool.” After reliability, the job is to make
**existing capabilities** feel like one daily product. The production
model is weaker than GPT-5-class; UX must not advertise that. The
controller, routing, verification, recovery, and product shell compensate.

---

## Sequence

```text
0.9.3  Weak-model reliability — CLOSED on origin/main
   │
   ▼
Canonical eval rebased (eval/real-world-suite) — no 97 REAL this stage
   │
   ▼
Product / runtime polish   ← current phase
   │     first slice: Desktop-owned backend (feat/product-runtime-foundation)
   │     then: on-demand GPU, installer, first-run, session restore, chips
   ▼
0.99  release candidate
   │
   ▼
LoRA decision (default: skip)
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

## MUST before 1.0 (count: 24)

Reliability foundation is closed. Remaining MUST items are product/runtime.

1. Desktop-owned backend lifecycle — “I opened Alex, nothing listens.” (**slice in progress**)
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
15. Five-question errors — “Something went wrong.”
16. Same-task recovery copy — “New task created.”
17. HIGH autonomy default (not Ask) — “may I read this file?”
18. Keep AUTONOMY/DEEP absent — “user tunes the weak model.”
19. Human confirmation headlines — “digest=a0e277.”
20. Secrets out of UI/logs/bundle — key leak.
21. Support bundle — “paste .env in Discord.”
22. Honest degrade, no prod mock masquerade — “fake success.”
23. Upgrade preserves data — “update wiped chats.”
24. Zero-terminal daily flows — “PowerShell to start AI.”

Satisfied in 0.9.3 (do not reopen as reliability work): confirmation payload/replay/allow-once, write verification, directory-as-file, SHA256 grounding, workspace scope, no-progress, owned process control, coding DoD, WRITE queue, Tor fail-closed, RAG missing-fact, memory/Newhaven, Agent READ_ONLY, OpenAPI version string 0.9.3, cost ceilings + never delete volume, eval Gate C still required later.

## SHOULD before 1.0 (count: 12)

25. Authenticode / SmartScreen story.
26. In-app updater **or** a documented one-click upgrade.
27. Embedding auto-prepare on first attach.
28. Tor Browser missing: in-app detect + download guide.
29. Git missing: Computer message, not a tool traceback.
30. Volume monthly cost only if supplier truth exists.
31. JWT refresh / longer local device session.
32. Collapse composer Web/Tor/Computer radios to status+Advanced.
33. Pre-migrate SQLite copy.
34. Uninstall keep/delete data.
35. Map remaining unmapped error codes.
36. Advanced diagnostics door.

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

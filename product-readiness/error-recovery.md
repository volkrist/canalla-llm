# Errors and recovery

Every user-facing error answers five questions:

1. What happened
2. What Alex already tried
3. Whether work is safe (files, money, volume, task identity)
4. What will retry automatically
5. Whether the user must do something

Do not say «Something went wrong» / «Операция не выполнена» when a stable
code exists.

**CURRENT VERIFIED FROM REPO:** codes exist (`LLMError`, `RunPodError`,
`toolErrors` in `apps/desktop/src/lib/tools.ts`, `ERROR_MESSAGES` in
`runpod_api.py`). Many are already human. Gaps: unmapped fallback,
technical tokens (`risk=CRITICAL`, `digest=a0e277…`), raw task enums,
no single recovery banner «Задача восстановлена».

**RECHECK AFTER 0.9.3:** new grounding/intent codes; continue_task vs
“new task created”.

---

## Taxonomy

| Code / situation | Happened | Tried | Safe? | Auto retry | User |
|---|---|---|---|---|---|
| AI unavailable (`offline`, mock in prod) | model not reachable | health poll | yes | poll | wait or check AI chip |
| RunPod unavailable | API down | call | no new Pod | yes, observe | wait |
| GPU unavailable | no stock / price | search | no Pod | yes if armed | wait / Advanced ceiling |
| model startup failed | Pod up, llama.cpp not ready | start scripts, health | volume kept, Pod terminating | bounded restart | wait / support bundle if repeats |
| backend unavailable | local API down | launch | GPU **unsafe if monitor died** | start backend | if fails: relaunch Alex |
| Computer disconnected | host offline | pair loop | no local writes | yes; task `WAITING_DEVICE` | open Alex on this PC |
| TinyFish unavailable | web provider down | request | no paid run charged if failed pre-call | limited | continue without Web |
| Tor unavailable | SOCKS/browser | connect | no Direct fallback | no silent fallback | start Tor capability or skip |
| budget exceeded | tools or GPU or TinyFish cap | stop further paid/tools | partial work kept | no | Resume later / raise Advanced cap |
| confirmation expired | 5 min window | none | no action executed | no | confirm again (new digest) |
| workspace conflict | file hash changed | patch aborted | no overwrite | no | Resume → re-read |
| `WAITING_WORKSPACE` | another WRITE task | queue | yes | promotes FIFO | wait |
| task failed verification | tests/review not green | diagnose/fix bounded | files as left | no extra after cap | read summary |
| `create_unknown` | create result unclear | **no second create** | unknown Pod possible | observe only | Advanced / admin |
| `multiple_compute` | >1 Pod on volume | no extra create | money risk | no | Advanced: pick/stop extras |
| `session_budget` / `price_violation` | spend guard | terminate | volume kept | no | new session later |
| `confirmation_denied` | user said no | none | no change | no | continue without that action |
| `critical_not_armed` | CRITICAL prepared | host fail-closed | **not executed** | no | Advanced arming is developer-only |

---

## Recovery UX

The user should see **the same task**, not a clone.

| Event | User sees | Must not see |
|---|---|---|
| backend restart | «Задача восстановлена» + last phase | new chat, duplicate patches |
| Desktop restart | same, after session restore | login wall **and** lost task (1.0) |
| model restart | «AI снова готов — продолжаю» | second Pod |
| device reconnect | «Компьютер снова в сети» | re-pair as a new device without need |
| workspace queue | «Жду очередь (позиция N)» | `workspace_busy` as a hard fail (0.9.1 already queues) |
| network reconnect | chips return to Ready | fake completed web results |

**CURRENT:** Resume is `POST /tasks/{id}/resume` with checkpoint digest skip.
UI still says Resume and raw `INTERRUPTED`. 1.0: auto-resume safe tasks
after recovery; keep Pause for the user-initiated pause.

Idempotency: local completed digests must not re-run. Network fetch may
hit loop guards instead — say «источник уже открывался», do not pretend
a new investigation if it was skipped.

Hard crash during stream: partial text may be missing (documented today).
1.0 SHOULD persist more aggressively; MUST at least restore the task row.

---

## Copy pattern

```text
[short title]
[one sentence: happened]
Уже сделано: …
Данные: … (volume / files / money)
Дальше: … (auto or wait)
Нужно от вас: … | ничего
```

Digest, tool XML, and risk enums go under «Подробности» in Advanced, not
in the primary card. Human target text stays primary
(`security-confirmations.md`).

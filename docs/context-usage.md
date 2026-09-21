# Context usage meter (composer)

The composer shows how much of the model's context window the next message will take.
It is a read-only indicator: it never starts compute, never sends a prompt and never
changes memory usage counters.

```
Context  12 480 / 32 768  38%
```

The ring and the numbers sit in one row above the input. Clicking the meter opens the
breakdown, which lists every part that enters the prompt.

## Where the limit comes from

| Question | Answer |
|---|---|
| Who owns the number | The backend setting `llm_context_window` (default **32768**), i.e. the effective `--ctx-size` llama.cpp is started with on the RunPod volume |
| Where the frontend gets it | Only from the snapshot response (`limit_tokens`); the UI never hardcodes a window |
| How to change it | Set `LLM_CONTEXT_WINDOW` for the backend (and start llama.cpp with the matching `--ctx-size`). One value, one place |
| Model invariants | model, alias, GGUF and llama.cpp flags are unchanged by this feature (see `AGENTS.md`) |

The window is a property of the served runtime, not of a provider response: it must be
known while the GPU is stopped, so it cannot be read from llama.cpp on demand.

## What is counted

The snapshot reuses the real context builder (`app/context_builder.py`): the meter
measures the exact message list `ContextBuilder.build` would send, part by part.

| Part key | Contents |
|---|---|
| `system` | the global system prompt |
| `profile` | display name + custom instructions block |
| `pinned` | pinned memories |
| `project` | the active project's name and description |
| `project_memory` | memories attached to that project |
| `memory` | relevant general memories |
| `documents` | RAG excerpts retrieved for the current draft |
| `history` | the recent chat messages that fit the history budget |
| `draft` | the text currently in the composer |

Web/Tor/tool material is added per request (``ContextBuilder.with_web``) and is therefore
not part of the idle-state snapshot; the documents part already reflects what retrieval
would attach for the current draft.

## How the tokens are estimated

`app/context_usage.py`:

* per message body: `ceil(latin_chars / 4) + ceil(non_latin_chars / 2)`
* plus `MESSAGE_OVERHEAD_TOKENS = 4` per message for the chat template
* the sum over all parts is `used_tokens`; `percent = used_tokens / limit_tokens`

The constants are the documented rule of thumb for a Qwen-class BPE on mixed
Russian/English text. This is an **estimate**, not a tokenizer: only the inference server
knows the exact count, and asking it would require a running GPU. The UI says so in the
breakdown («Оценка по активному контексту»).

Two honest details:

* the estimate is never clamped: a draft longer than the window reports more than 100%
  and zero remaining tokens instead of a reassuring number;
* when a generation has already run, the breakdown also shows the prompt size the
  provider reported (`measured.prompt_tokens`), which is a real measurement and a
  calibration check for the estimate above it.

## Thresholds

| Level | Percent | Behaviour |
|---|---|---|
| normal | < 70 | plain ring |
| warning | 70–85 | amber ring and percentage, «Контекст заполняется.» in the breakdown |
| danger | > 85 | red ring, plus «Контекст почти заполнен: старые сообщения или источники могут быть обрезаны.» as a short status line next to the meter |

Thresholds live in `apps/desktop/src/lib/context-usage.ts`
(`CONTEXT_WARNING_PERCENT`, `CONTEXT_DANGER_PERCENT`) and are pinned by unit tests.

## API

`GET /chats/{key}/context-usage?prompt=<draft>` — authenticated, ownership-checked,
`404` for someone else's chat.

```json
{
  "model": "orcarouter-qwen38-27b-q5km",
  "limit_tokens": 32768,
  "used_tokens": 12480,
  "remaining_tokens": 20288,
  "percent": 38.1,
  "estimated": true,
  "method": "chars_per_token",
  "parts": [{ "key": "system", "label": "System", "chars": 1180, "tokens": 299 }],
  "measured": { "prompt_tokens": 11800, "at": "2026-09-21T13:36:23+00:00" }
}
```

`/chats/{key}/context-preview` (the existing technical preview) carries the same block
under `context_usage`. The route is read-only: it calls the builder with `track=False`,
so the meter can poll while the user types without touching `use_count`.

## Live update

The composer reads the snapshot through `useContextUsage` (debounce
`CONTEXT_USAGE_DEBOUNCE_MS = 400`, newest answer wins, failures are silent). It refreshes
when the draft, the open chat or the message count changes, so typing, switching project,
attaching a document or receiving an answer all move the meter. Nothing is computed while
the backend is unreachable — the meter simply stays hidden rather than guessing.

## Files

| File | Role |
|---|---|
| `apps/backend/app/context_usage.py` | estimate, labels, summary payload |
| `apps/backend/app/context_builder.py` | builds the prompt, labels its parts, exposes the route |
| `apps/desktop/src/lib/context-usage.ts` | types, formatting, thresholds, ring geometry, hook |
| `apps/desktop/src/components/ContextUsageMeter.tsx` | the ring, the numbers and the breakdown |
| `apps/backend/tests/test_context_usage.py` | limits, parts, ownership, no side effects |
| `apps/desktop/src/lib/context-usage.test.tsx` | thresholds, formatting, rendering |

# GPU allocation — candidates, budget and storage topology

The blocker this page answers: Canalla sat in «Ищем GPU» for about an hour, more than once on
different days, and never obtained a usable model. The UI label was already fixed (an endless
amber «Connecting» is impossible: the search window has an identity, a deadline and an expiry
collapse in `apps/gateway/gateway/compute.py`, covered by `tests/test_search_bounds.py`). The
**allocator** was the remaining defect: it could pin itself to one physical placement — one GPU
id, one cloud tier, one datacenter — and effectively wait forever for that one machine instead of
walking a set of compatible candidates inside one bounded budget.

This page is the contract for the allocator: what may be booked, in what order, under what
budget, and what is recorded when nothing can be booked.

## 1. Storage topology (audit, before any change)

**Model and compute state live on the RunPod Network Volume, not on a host-local Pod volume.**

| What | Where | Evidence |
|---|---|---|
| Model weights | `/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf` — the Network Volume | `remote_runtime.py` refuses to start without that exact path under `/workspace` |
| llama.cpp start/health scripts | `/workspace/start-llm.sh`, `/workspace/check-llm.sh` — the Network Volume | same precondition loop |
| Mount | `mounts.network = [{volumeId: <RUNPOD_NETWORK_VOLUME_ID>, path: "/workspace"}]` | `RunPodAPI.create_pod`; the id is `uwgeaie5b0` by default |
| Ephemeral container disk | `disk: 10` — **only** `/tmp` logs (`/tmp/alex-llm-start.log`, `/tmp/alex-llm-check.log`, `/tmp/alex-llm-runtime.lock`) | `startup_command`, `remote_runtime.py` |
| User data (chats, memory, documents, backups, embeddings) | the user's own machine, `%LOCALAPPDATA%\Alex LLM\` | never on a Pod at all |

Consequences, stated explicitly because they gate behaviour:

* **A terminated Pod loses no model state.** The weights, the scripts and llama.cpp's own
  volume-side data are on the Network Volume, which no code path deletes (`RUNPOD_NETWORK_VOLUME_ID`
  is only ever mounted). Redeploying after a termination re-mounts the same model.
* **Automatic terminate/redeploy is therefore *architecturally* safe today** for model assets.
  Nothing in *this* change terminates a Pod, though, and no new automatic redeploy path was
  added: an allocation that cannot be placed has nothing to terminate (the provider refused the
  create — no Pod exists), and a Pod that exists but never becomes ready is already owned by the
  D-9 startup deadline (`startup_timeout`, 300 s, server-side). Adding a second automatic
  termination path here would be new destructive behaviour for no gain.
* **A stopped Pod is never treated as bookable capacity.** Every allocation creates a fresh Pod
  from the Volume; an `EXITED`/`TERMINATED` Pod on the Volume is ignored by reconciliation and
  never waited on (covered by `test_a_stopped_pod_on_the_volume_is_never_treated_as_capacity`).

## 2. The candidate set

`apps/backend/app/compute/candidates.py` is the **one** place that decides what may be booked, and
in what order. Direct mode and the Central Gateway both import it (the Gateway loads the backend
package as a library — `apps/gateway/gateway/provider.py:allocation_policy()`), so the two
provider modes cannot drift into two different allocation policies.

The set is derived from the product's own configuration, never from guesswork:

| Input | Source |
|---|---|
| Runtime requirement | `RUNPOD_MIN_CUDA_VERSION` (12.8) — sent to the catalogue query |
| VRAM floor | `RUNPOD_MIN_VRAM_GB` (48) and the user's own `min_vram_gb` (floored, never below the server value) |
| Money policy | the user's own `max_hourly_price` |
| Selection | `automatic` (cheapest compatible) or `manual` (the named card only) |
| Cloud tiers | deployment policy `RUNPOD_ALLOW_COMMUNITY_CLOUD` (default off) + the user's own `allow_community` preference |
| Placements | the datacenter of the Network Volume (read from the provider) + `RUNPOD_DATACENTERS` |

A **placement** is a `(datacenter, volume)`, and the volume is part of it on purpose: the model
lives on that Network Volume, so a datacenter that cannot mount it cannot serve the configured
model. An allocation candidate is `(GPU, cloud tier, placement)`.

Ordering (documented, and the reason the first create is unchanged from before):

1. **cloud tier** — Secure Cloud first, then Community;
2. **price ascending** — automatic mode always books the cheapest compatible GPU inside the
   user's own maximum, across the whole candidate set;
3. **placement order** — the Volume's own datacenter first, then the operator's list;
4. **GPU id** — a total order, so the walk is deterministic.

With one tier and one placement this is exactly the historical order (cheapest first, ties by id).

### Cloud tiers (never a hidden change)

The provider offers two tiers. The product policy today is **Secure Cloud only**: a RunPod
Community Cloud Pod cannot mount a Network Volume, and the model lives on one, so a Community
candidate could be booked and would then be unable to serve the configured model. Therefore:

* `RUNPOD_ALLOW_COMMUNITY_CLOUD` is `false` by default and is a server-side switch;
* a placement is only Community-capable when the operator *declares* it
  (`RUNPOD_DATACENTERS="US-TX-3:uwgeaie5b0:community"`);
* the user's own `allow_community` preference is a visible control in Settings → AI / Compute
  (disabled, with the reason, when the deployment does not offer the tier);
* the effective policy is reported read-only by `GET /compute/status` → `policy`
  (`cloud_tiers`, `community_allowed`, `community_blocked_by`), so the panel states the fact
  instead of guessing.

A tier is therefore used only when **both** the deployment permits it and the user opts in, and
even then only on a placement the operator declared capable. See
[compute-preferences.md](compute-preferences.md) §11 for the user-facing side.

### Datacenters

Scheduling is not restricted to one datacenter by code any more: the primary placement is the
Volume's own datacenter as the provider reports it, and `RUNPOD_DATACENTERS` *adds*
`DC:VOLUME[:community]` pairs. An entry that cannot be parsed fails at startup (a dropped
placement would silently pin the allocator back to one slot). An added placement is the
operator's declaration — the provider refuses a wrong one, and the walk simply moves on.

The provider scopes a Network Volume to one datacenter, so adding a datacenter only helps when it
has its own Volume holding the model. That is why `RUNPOD_DATACENTERS` carries the Volume per
placement instead of a bare datacenter name.

## 3. One total budget: 60 seconds

`COMPUTE_SEARCH_TIMEOUT_SECONDS` (Gateway, default 60, configurable 5–60 server-side only;
`candidates.ALLOCATION_WINDOW_SECONDS` = 60 for direct mode) is the **whole** user-facing
allocation budget. It is never per candidate, and it must be impossible to spend 60 s × N:

* the walk re-reads the remaining budget **before every candidate** and stops when it is spent;
* every provider call is capped by `min(CANDIDATE_CALL_TIMEOUT_SECONDS, remaining)`
  (`candidates.CANDIDATE_CALL_TIMEOUT_SECONDS` = 15 s), so one candidate can never consume the
  window on its own;
* an existing live window is **reused, never extended**, so a second attempt inside it inherits
  what is left rather than getting another 60 s;
* at most `MAX_CANDIDATE_ATTEMPTS` (3) placements are tried per walk, and at most one walk is in
  flight per compute identity.

Worst case: 3 × min(15 s, remaining) ≤ 45 s of provider calls inside one 60-second window, plus at
most one bounded call that crosses the deadline.

## 4. Walking, and what ends the walk

A candidate is left behind **immediately** when the provider answers a definitive refusal of that
placement (`placement_rejected` from 400/409, `gpu_unavailable`, `not_found`/404,
`runpod_invalid_request`/422, or a 403). Nothing was created in those cases, so the next
compatible candidate is tried at once: the allocation never waits for one machine.

Exactly one Pod can come out of a walk:

* a **successful** create ends the walk and returns immediately (one Pod, one session);
* an **ambiguous** answer (a timeout, `runpod_unavailable`, a 5xx) ends the walk as
  `create_unknown` — never a second create, because the first one may have succeeded;
* a **terminal** answer (401/402/429 and anything else that is not a placement refusal) ends the
  walk with that typed code;
A walk that exhausted its candidates (or its budget) **closes the operation**: the state becomes
  `offline` with the typed reason (`gpu_unavailable`, `no_compatible_gpu`, `price_limit`), the AI
  badge goes red **Disconnected**, and the audit trail records `search_closed`. `searching` only
  ever means "a real allocation operation is active, with an identity and an unexpired deadline".

  A *catalogue* conclusion is the one case that keeps a bounded amber window instead: when the
  provider's own stock read says nothing is bookable at all (no candidate was ever offered, so no
  allocation was refused), the search stays `searching` for the rest of its 60-second window — the
  client already stops at the same 60 s and reports the typed `gpu_capacity_unavailable`, and
  `collect_expired_search` collapses the row to `offline` + `gpu_unavailable` even if no client ever
  comes back.

A later, new user request starts exactly one new bounded cycle. There is no background capacity
monitor: nothing polls the provider, nothing creates a Pod on its own, and nothing generates cost
between requests.

## 5. One allocator per compute identity

Concurrent requests cannot multiply Pods:

| Mechanism | What it guarantees |
|---|---|
| database CAS lease (`gateway_compute.lease_owner`) | one `ensure` mutates at a time; a loser is told `gateway_busy` |
| operation-id idempotency | the recorded answer is replayed, never re-executed |
| committed create intent before the provider call | an interrupted attempt is reconciled, never guessed |
| one Pod rule + reconciliation | an existing Pod on the Volume is never duplicated |

Client side, `app/cloud/demand.py:SharedDemand` is single-flight: a chat request, a second chat
request and the manual «Запустить AI» share one attempt. Covered by
`test_matrix_6_concurrent_ensures_share_one_walk` (three concurrent ensures, one Pod, one session
row, one walk's worth of creates).

## 6. Observability: why did Canalla fail to find a GPU?

Every walk records a **non-secret** structured record — candidate GPU, cloud tier,
datacenter/region, attempt result, elapsed time, the failure code, the final selection and the
plan itself:

* durable: an `audit_events` row with `operation="allocation"` whose JSON payload holds the
  record plus the operation id (the same row carries a compact one-line `detail`);
* live: `POST /compute/ensure` → `allocation`, and `GET /compute/status` → `allocation` for the
  operation that produced it;
* policy: `GET /compute/status` → `policy` (`cloud_tiers`, `community_allowed`,
  `community_blocked_by`, `datacenters`, `candidate_limit`, `candidate_call_timeout_seconds`,
  `allocation_timeout_seconds`).

No API key, bearer token, Pod id, provider URL or prompt ever enters the record (asserted by
`test_the_allocation_record_explains_the_failure_without_secrets` and the whole
`tests/test_security.py` suite).

## 7. Deterministic test matrix

No test creates, resumes or stops a paid resource; the provider is a fake and time is a
controllable clock.

| # | Case | Test |
|---|---|---|
| 1 | preferred GPU available → selected | `gateway/tests/test_allocation.py::test_matrix_1_the_preferred_candidate_is_selected` |
| 2 | preferred unavailable, second compatible available → second selected | `::test_matrix_2_a_refused_preferred_candidate_falls_through_to_the_next` |
| 3 | first datacenter unavailable, another available → fallback succeeds | `::test_matrix_3_an_unavailable_datacenter_falls_back_to_another_placement` (+ `::test_matrix_3b_without_the_extra_placement_the_same_catalogue_has_no_candidate`) |
| 4 | all compatible capacity unavailable → bounded `capacity_unavailable` | `::test_matrix_4_all_capacity_unavailable_is_bounded_capacity_unavailable`, `::test_matrix_4b_every_candidate_refused_closes_the_operation_immediately` |
| 5 | one candidate errors → the next is tried | `::test_matrix_5_one_erroring_candidate_never_stops_the_walk` (+ `::test_a_vanished_gpu_ends_with_the_catalogue_conclusion`) |
| 6 | concurrent ensure calls → one allocator | `::test_matrix_6_concurrent_ensures_share_one_walk` |
| 7 | deadline expires → no further active searching | `::test_matrix_7_the_deadline_closes_the_search_and_nothing_stays_active` |
| 8 | stale searching with no allocator behind it → Disconnected | `::test_matrix_8_a_stale_search_with_no_allocator_is_disconnected` |
| 9 | successful allocation → exactly one Pod | `::test_matrix_9_a_successful_walk_creates_exactly_one_pod` |
| 10 | failed allocation → zero leaked Pods | `::test_matrix_10_a_failed_walk_leaks_no_pod` |
| 11 | the walk is bounded per call, per budget and per count | `::test_a_candidate_cannot_consume_the_whole_allocation_budget`, `::test_the_candidate_call_timeout_never_exceeds_what_is_left`, `::test_the_walk_never_exceeds_the_candidate_limit` |
| 12 | the record explains the failure | `::test_the_allocation_record_explains_the_failure_without_secrets` |

Direct mode (the dev/private path) walks the same policy for the *approved* card only — a quote is
a confirmation bound to one GPU id and one price, so it is never substituted and never made more
expensive: `backend/tests/test_allocation_candidates.py` (the policy itself, the placement walk,
`price_changed` semantics, no leaked Pod).

## 8. Files

| File | Role |
|---|---|
| `apps/backend/app/compute/candidates.py` | the shared candidate policy: placements, ordering, reasons, refusal classification, budget constants |
| `apps/backend/app/compute/runpod_api.py` | `gpu_offers()` (all tiers/datacenters), `project_options()` (the quote projection), placement-aware `create_pod()` |
| `apps/backend/app/compute/controller.py` | direct mode: the same policy for the approved card, placement walk, `allocation_policy_payload()` |
| `apps/gateway/gateway/compute.py` | the Gateway walk, the total budget, the allocation record, the policy block |
| `apps/gateway/gateway/config.py` | `RUNPOD_DATACENTERS`, `RUNPOD_ALLOW_COMMUNITY_CLOUD` |
| `apps/backend/app/config.py` | the same two settings for direct mode |
| `apps/desktop/src/components/ComputePanel.tsx` | the visible Community-tier choice and its reason |

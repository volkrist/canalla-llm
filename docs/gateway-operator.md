# Gateway operator: roles, money entitlements and the emergency Stop (A10/A11)

The Gateway is the authority. A **role** and every **money entitlement** are server-side facts,
stored on the `installations` row and changeable only with the operator CLI, run locally on the
Gateway host. No request body, header, client role or preference can set them, and none of the
values is a hidden product cap: an installation nobody has constrained (`NULL`) keeps exactly the
user's own policy.

## Bootstrapping the first operator

There is **no** operator by default and **no** automatic promotion: a fresh install (and every
installation created before the role column existed) is `member`. The safe path, for the person who
already holds the server console and the RunPod master key:

```sh
# 1. On the Gateway host only: create a one-time activation code for your own install.
python -m gateway.cli create-code --label "Owner PC"

# 2. Enroll it from the desktop app (the code is shown once, stored only as a digest).

# 3. Promote that installation — with the role of the person who owns the account.
python -m gateway.cli installations                       # find the installation id
python -m gateway.cli set-role --installation-id <id> --role operator

# 4. Optional: hold it (and everyone else) to a ceiling before inviting anyone.
python -m gateway.cli set-entitlement --installation-id <id> \
    --max-hourly-price 1.20 --session-budget 3.00 --period-budget 50.00 --period-days 30
python -m gateway.cli set-entitlement --installation-id <member-id> \
    --max-hourly-price 0.52 --session-budget 3.00 --period-budget 10.00 --period-days 30
```

Nothing here is run in this repository and no real installation's role is changed: REGYNA and the
owner's install keep their identity and credentials, and only an explicit `set-role` promotes one.

## The four Stop meanings

| Intent | Who | How |
|---|---|---|
| Cancel my own generation | any installation | disconnect / `POST /compute/cancel` |
| Withdraw my own demand | any installation | `POST /compute/stop` with `"scope": "own"` |
| Stop the shared managed Pod | the session owner or an operator | `POST /compute/stop` (default `scope: global`) |
| Emergency global Stop | an **operator** | `POST /compute/emergency-stop` |

A non-owner, non-operator `global` Stop is refused with `gateway_stop_forbidden` (403) and the Pod
is left untouched. The emergency Stop reuses the durable Stop: a RunPod 500/timeout keeps the stop
intent and is retried from persisted state on the next tick. An unmanaged Pod is never terminated.

If **no operator has been assigned**, the emergency path is still available from the server console
— the person with the host and the RunPod key is the operator by definition and runs
`set-role` first. Until then, ordinary installations simply cannot stop each other's compute.

## Preconditions before a public rollout

1. At least one operator is assigned via `set-role`, and the operator's own access is verified.
2. Every installation that may be invited carries an explicit `set-entitlement` ceiling
   (`NULL` means *unbounded* — acceptable only for the trusted owner).
3. A period budget is set for every non-owner installation, so a series of sessions cannot drain
   the shared balance.
4. `python -m gateway.cli installations` shows the expected roles before invitations go out.

See `docs/compute-preferences.md` for the user-facing policy and `docs/gateway-status-contract.md`
§7 for the wire contract these commands feed.

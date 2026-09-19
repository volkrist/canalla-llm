# Tools

ToolRegistry holds provider-independent definitions and adapters. Each definition has
a Pydantic-validated JSON input schema, capability, risk, cost class and timeout.
ToolExecutor enforces ToolPolicy independently of model output. The model cannot
provide a confirmation flag, change the risk or register tools.

READ tools follow Web Off/Auto/On and user preferences. NORMAL_CHANGE,
SENSITIVE and CRITICAL tools wait for an ownership-checked one-time approval
except Trusted Workspace, which auto-allows safe NORMAL_CHANGE inside workspace
roots. The immutable payload digest binds approval to the exact pending action
(`action` + arguments). Approval expires after
five minutes, cannot be replayed, and permissions are rechecked before execution.
CRITICAL has Allow once only — never Always allow. Direct Browser definitions are never automatically routed. CredentialReference and
LocalCredentialProvider keep raw secrets on the paired host; the model sees only
a logical reference.

Defaults: 8 web calls, 24 coding calls, hard ceiling 32, 3 searches, 3 fetch batches (1–3 URLs each), 20000 reference characters, 20 files changed, 2 MB file bytes, 120 s process runtime, 180 seconds wall. Limits are server configurable. Every model proposal is validated again;
unknown names, extra JSON fields and malformed arguments do not execute a provider.
Tool responses are untrusted reference data and never independently invoke tools.

ToolRun records owner/chat/generation, lifecycle timestamps, sanitized input preview,
payload digest, `origin` (`model` or `server_policy`), optional `assigned_device_id`,
provider run ID, actual/estimated nullable costs and safe metadata.
Paid budget reservations are serialized using the database writer/user-row lock.
Unknown supplier cost remains unknown; its reservation conservatively counts against
the daily allowance. A reservation is not a reported charge.

WebSourceSnapshot is separate from RAG storage and persists bounded source excerpts,
requested/final URLs, provider, timestamps, channel (web/tor), authority, canonical URL
and D/W/T labels for the original generation.
Normal audit/source endpoints filter by owner, including administrator requests.
Cancellation closes the provider task and records stopped/cancelled_at. Provider
adapters must perform their own supplier cancellation in finally blocks.

Direct Browser typed actions remain explicit (`browser_start` / `browser_read` / `browser_write`) with per-action confirmation. Planner-facing `web_browser` and `web_agent` are Off/Auto/On (default Auto). Auto hides them from the weak planner; the server injects a paid tool only when the deterministic router selects it. Agent is READ_ONLY only: the current TinyFish API has no pre-action approval, so side-effect goals are blocked before the provider call. Prompt text is not a security boundary. Settings change through authenticated APIs without restarting the backend. See [TinyFish](tinyfish.md), [Tor](tor.md) and [Local Computer](local-computer.md).

Autonomous 0.9 tasks add a persistent plan and tighter ceilings (default 40 calls / 30 min / 20 files, hard 100 / 120 min / 100 files) without a second registry. Unattended “run until done” still cannot skip SENSITIVE/CRITICAL confirmation.

`web_mode`, `tor_mode` (Off/Auto/On) and `computer_mode` are independent. Web Off does not turn off Local Computer or Tor. Computer Off does not turn off Web.

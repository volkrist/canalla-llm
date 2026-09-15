# Tools

ToolRegistry holds provider-independent definitions and adapters. Each definition has
a Pydantic-validated JSON input schema, capability, risk, cost class and timeout.
ToolExecutor enforces ToolPolicy independently of model output. The model cannot
provide a confirmation flag, change the risk or register tools.

READ_ONLY tools follow Web Off/Auto/On and user preferences. EXTERNAL_SIDE_EFFECT
and SENSITIVE tools wait for an ownership-checked one-time approval. The immutable
payload digest binds approval to the exact pending action. Approval expires after
five minutes, cannot be replayed, and permissions are rechecked before execution.
Direct Browser definitions are never automatically routed. CredentialReference and
ToolCredentialProvider reserve future vault/BYOK integration without storing passwords.

Defaults: 8 calls, 3 searches, 3 fetch batches, 10 pages, 20000 reference characters,
180 seconds. Limits are server configurable. Every model proposal is validated again;
unknown names, extra JSON fields and malformed arguments do not execute a provider.
Tool responses are untrusted reference data and never independently invoke tools.

ToolRun records owner/chat/generation, lifecycle timestamps, sanitized input preview,
payload digest, provider run ID, actual/estimated nullable costs and safe metadata.
Paid budget reservations are serialized using the database writer/user-row lock.
Unknown supplier cost remains unknown; its reservation conservatively counts against
the daily allowance. A reservation is not a reported charge.

WebSourceSnapshot is separate from RAG storage and persists bounded source excerpts,
requested/final URLs, provider, timestamps and W labels for the original generation.
Normal audit/source endpoints filter by owner, including administrator requests.
Cancellation closes the provider task and records stopped/cancelled_at. Provider
adapters must perform their own supplier cancellation in finally blocks.

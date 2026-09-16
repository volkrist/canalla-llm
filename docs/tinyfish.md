# TinyFish adapter contracts — 0.6.0

Production uses direct REST with httpx, never CLI subprocesses. CLI/MCP is developer tooling only. BackendCredentialProvider resolves the shared SecretStr TINYFISH_API_KEY. UI receives configured boolean only; keys, CDP credentials and website passwords are not stored in audit/context/schema/frontend. CredentialReference reserves future secure vault integration; login automation is not supported.

Official references: [Search](https://docs.tinyfish.ai/search-api/reference), [Fetch](https://docs.tinyfish.ai/fetch-api/reference), [Agent](https://docs.tinyfish.ai/agent-api/reference), [Browser](https://docs.tinyfish.ai/browser-api/reference).

- Search: GET https://api.search.tinyfish.ai, X-API-Key, query/purpose/language/location/recency/date/domain/page filters.
- Fetch: POST https://api.fetch.tinyfish.ai, markdown, bounded URLs/timeouts/cache TTL, partial results/errors.
- Agent adapter: POST https://agent.tinyfish.ai/v1/automation/run-sse, STARTED/PROGRESS/COMPLETE, GET run usage and POST run cancel. max_steps is optional beta (off until supported); local timeout and step estimates are not a hard supplier billing guarantee. Real execution is disabled because the current API cannot enforce read-only/per-action approval. Contract tests use fake HTTP; this is not a completed production autonomous Agent feature.
- Browser: POST https://api.browser.tinyfish.ai starts blank; CDP Playwright installs guards before navigation. Typed navigate/read/wait/extract/screenshot and separately confirmed click/type only. Browser is Advanced/off by default, never model auto-routed. Session creation is serialized; ownership, active-session checks, runtime watchdog and budget reservations apply.

The current Browser reference documents DELETE /{session_id}, unlike the earlier task assumption. Stop closes local control then attempts supplier DELETE. Only 204 confirms termination. Failures retain UNKNOWN and a visible warning, never claim billing stopped. Capability TINYFISH_BROWSER_DELETE_SUPPORTED can disable this call. Provider timeout also limits session lifetime; crashes/unconfirmed termination still require supplier verification. Browser costs are estimates at configured $0.002/minute; Agent steps default $0.016. Neither is represented as supplier-reported actual billing.

Search/Fetch use configured free capability; setting TINYFISH_SEARCH_FETCH_FREE=false blocks calls until pricing is reviewed. Prices/capabilities are configuration, not eternal API guarantees. Paid per-run/daily preferences reserve budgets transactionally; unknown costs retain conservative reservations. Runtime budgets are soft where provider hard enforcement is unavailable. 429/503 respects bounded Retry-After, at most three attempts; Search is paced, and paid create is never retried automatically.

Normal tests use deterministic fake adapters and HTTP contracts. Optional live Search/Fetch test requires explicit opt-in plus preconfigured backend key. No real Agent/Browser call is part of any test suite. A paid validation needs separate user approval and must confirm supplier termination/cost.

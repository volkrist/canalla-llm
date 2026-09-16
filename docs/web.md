# Web in 0.6.0

Composer supports Off, Auto and On plus an explicit search button. Off makes no web calls. Auto gives the tool-capable model freshness hints; regex is not the production planner. On requests public evidence. Search → Fetch → bounded ToolResult → final generation uses the same generic loop. Existing settings apply without backend restart.

Search provides bounded titles, URLs, snippets, rank and available dates. Fetch requests markdown (up to 10 public URLs), TTL 0 for fresh mode or 3600 normally, bounded relevant excerpts, final URLs and optional ETag/Last-Modified. Partial failures preserve valid results. Network/provider errors are sanitized; missing keys leave chat operational and clearly mark web unavailable. The final model is told when no web results were available.

WebSourceSnapshot stores W1/W2 sources for the original generation; D1/D2 are independent document snapshots. The UI renders only recorded source links, never turns unknown W99 into a citation, and escapes provider text. Persisted excerpts do not change when pages change. Source panels indicate available fetched/searched/published dates; references are not a guarantee that a model's claim is supported.

HTTP(S), public DNS/IPs and standard ports only. Local/private/link-local/metadata endpoints, credentials in URLs, unsupported schemes and unsafe final URLs are rejected. Fetch is remote: final metadata is checked, but the backend cannot independently attest every upstream redirect hop. Browser requests are guarded separately. Untrusted references never become system instructions, and every subsequent model tool proposal is revalidated.

Real llama.cpp tool calls are contract-tested; actual OrcaRouter tool compliance, citations and answer quality require a separately approved GPU E2E. No current internet knowledge is claimed based only on fake tests.

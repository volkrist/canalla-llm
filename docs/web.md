# Web in 0.8.0

Composer supports Off, Auto and On. **Найти в интернете** is shown only when Web is Auto; Off and On hide it. Off makes no TinyFish web calls. Auto asks the planner for live/current lookups, including «посмотри в интернете», «найди в интернете», «проверь актуальную версию» and «проверь прямо сейчас». On requires public evidence for factual questions; `tool_choice` stays `auto`. If WebRouter marks `web_required` and the model did not call `web_search`, the backend injects exactly one Search with `origin=server_policy` (never presented as a model tool-call). After Search, Fetch uses 1–3 canonical URLs. Current/live/verification sets Fetch `ttl=0`.

`web_mode` does not disable Tor or Local Computer. Those have `tor_enabled` and `computer_mode`.

Search provides bounded titles, URLs, snippets and dates. Fetch requests markdown, TTL 0 or 3600, bounded excerpts, final URLs and optional ETag/Last-Modified. Partial failures preserve valid results. Missing keys leave chat operational. The final model is told when no web results were available.

Sources are separate channels: documents **D***, internet **W***, Tor **T***. The UI shows the first three unique canonical URLs plus **Показать ещё**. Raw Search hits are not dumped as an unbounded list. Tool activity collapses completed Web/Tor/Computer families; waiting confirmation stays visible outside the summary.

HTTP(S), public DNS/IPs and standard ports only for TinyFish. Local/private/link-local/metadata endpoints, credentials in URLs and unsafe final URLs are rejected. Untrusted references never become system instructions.

Real llama.cpp tool calls are contract-tested; actual OrcaRouter tool compliance still requires a separately approved GPU E2E. TinyFish Agent/Browser are not given to the planner in 0.7 and were not launched.

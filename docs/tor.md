# Tor in 0.8.6

Tor is a separate capability from TinyFish Web. `web_mode` does not enable or
disable it. Composer and Web & Tools expose **Tor Off / Auto / On**, independent
of Web mode.

Since 1.1.0 Tor is also an **always-ready service**: the backend keeps a route to
Tor up and *proves* it, so «Tor Готово» is a real health statement instead of a
configuration flag. See *Tor service (1.1.0)* at the end of this document — the
routing rules below are unchanged by it.

## Modes

- **Off** — no Tor tools for that request.
- **Auto** — Tor only when the user explicitly asks (через Tor, `.onion`, hidden
  service, continue onion research, and English equivalents).
- **On** — Tor is allowed for factual/research prompts, but not for greetings,
  simple rewrites, math, or local-only tasks.
- «не используй Tor» / «don't use Tor» disables Tor for that request.

The model is expected to call `tor_search` then `tor_fetch` itself. If the user
prompt already contains an http(s) `.onion` URL, the server fetches that URL
first and does not inject a new search. If the user has explicit Tor intent, no
onion URL in the prompt, and the planner returns no Tor tool, the server injects
a safe `tor_search` (and may fetch/follow within limits) with
`origin=server_policy`. Audit never pretends a server-injected call was
model-selected.

If `tor_fetch` returns a JS app shell (`needs_browser`), Auto Tor Browser mode
opens the same URL through `TorBrowserProvider` and may click the first safe
L-id. That fallback is `origin=server_policy`, not a Cursor-injected tool. The
heuristic does not hard-code test hostnames or render markers.

## Research loop

Automatic research stays on the existing ToolOrchestrator. Default ceilings:

- 3 `tor_search`
- 8 `tor_fetch`
- 8 followed links
- depth 3
- 12 Tor tool calls
- 50 candidate links
- wall-clock `tools_max_tor_seconds` (hard 300s)

`tor_fetch` extracts structured links (URL, text, source page, onion/clearnet,
same-host). Relative links are resolved. Fragments and tracking parameters are
stripped. `mailto:`, `javascript:`, `data:`, `file:`, `magnet:`, and download
suffixes are not followed. The model picks relevant next onion URLs; the server
enforces visited-URL loop protection.

Sources keep channel **T** with transport=tor, depth, parent, and authority.
Reachable does not mean official.

## Providers

- `TorTransportProvider` — SOCKS5h to loopback Tor (`127.0.0.1:9050` by default).
  Destination hostnames are sent as SOCKS ATYP `0x03`. `.onion` is never passed to
  the local DNS resolver. There is no Tor → Direct fallback.
- `TorSearchProvider` — GET against configured `TOR_SEARCH_PROVIDERS` URL
  templates (`{query}` or `__QUERY__`), also through that transport.
- `TorFetchProvider` — reads http(s) URLs, including `.onion`, through the same
  transport.
- `TorBrowserProvider` — isolated-profile automation of the **installed Tor
  Browser executable** via Marionette (not stock Firefox/Chrome through SOCKS).
  HTTP `tor_fetch` stays primary. Browser fallback is read-only: open, wait,
  render, extract L-ids, click navigation links, back, close. Forms, downloads,
  login and arbitrary JS are blocked. Temporary profile, Windows Job Object,
  `TOR_SKIP_LAUNCH=1`, SOCKS5h `127.0.0.1:9050`. No Tor→Direct fallback.
  `TorRoutedBrowserProvider` is a separate name and is **not implemented**.
  Settings: Tor Browser fallback Off/Auto/On. Pytest disables live launches
  unless `ALEX_TOR_BROWSER_LIVE=1`.

A curated `TOR_OFFICIAL_MAPPING` is provenance only. It is not a search index
and is never returned as if it were Tor Search hits.

If `TOR_SEARCH_PROVIDERS` is empty, `tor_search` fails closed with
`tor_search_not_configured`. The UI shows **Tor Search provider not configured**.
A configured provider that is unreachable, returns HTTP 4xx/5xx, or yields no
onion hits fails with `tor_search_failed` instead of pretending it is unconfigured.
`TorTransport.fetch` follows up to three HTTP redirects through the same SOCKS5h
path (ATYP `0x03`, no local DNS, no Direct fallback).
`tor_fetch` of a known URL still works when Tor SOCKS is reachable.

Operators should copy live URL templates from an official publisher at
configuration time (for example the onion search URL currently published on
Ahmia's clearnet site). If the provider's public search form includes a
rotating anti-bot field, set `form_url` to that homepage; Alex fetches the
form through SOCKS5h and appends the hidden fields before searching.
Application defaults stay empty so an expired address is not shipped as if it
were still valid. Prefer `TOR_SEARCH_PROVIDERS_FILE` or
`TOR_OFFICIAL_MAPPING_FILE` for JSON lists; environment JSON also works.

Automatic research may search, fetch pages, follow normal links, and read
content. It does not submit forms, log in, create accounts, download
executables/archives, upload files, enter credentials, make purchases, or send
messages.

## Authority

Official means a confirmable mapping from that list (or an equivalent official
clearnet/onion pair). Reachability never implies official:

- `OFFICIAL_AND_REACHABLE`
- `REACHABLE_UNVERIFIED`
- `OFFICIAL_UNREACHABLE`

## Limits

SOCKS is loopback-only. TinyFish is not used. Paid Agent/Browser are not Tor
transports. Migration 0010 adds `web_source_snapshots.details` for Tor research
metadata.

## Tor service (1.1.0)

`app.tools.tor.service.TorService` is the single owner of Tor health. It runs in
the backend lifespan (never in `app_env=test`), starts nothing on the GPU and
never blocks startup.

**Discovery order** — the configured endpoint first, then `127.0.0.1:9050`
(standalone Tor), then `127.0.0.1:9150` (Tor Browser), plus any `tor_extra_ports`.
The first endpoint that answers becomes the runtime route; nothing else is ever
tried, so a stray proxy can never be adopted by accident.

**Managed start** — when nothing answers and `tor_managed_enabled` is on, the
service starts its own process: `TOR_BINARY_PATH` if set, otherwise the Tor
Browser bundle (`Browser/TorBrowser/Tor/tor.exe`), otherwise `tor` on `PATH`,
otherwise `Program Files\Tor`. It uses its own `--DataDirectory` under the data
root and logs to `<data root>/logs/tor.log`.

**Readiness is a proof, not a listener.** Tor opens its SOCKS listener at
bootstrap 0 %, so an open port means nothing. The chip turns green only after a
SOCKS5h round trip to `tor_check_url` (default
`https://check.torproject.org/api/ip`) came back as Tor (`IsTor: true`). The
result is persisted at `<data root>/runtime/tor.json` for
`tor_proof_ttl_seconds` and **never stores the exit IP**.

| Snapshot state | Meaning | Chip |
|---|---|---|
| `ready` | endpoint known **and** a fresh proof | «Готово» |
| `starting` | managed process bootstrapping (`Bootstrapped NN%` read from the Tor log) | «Подключается…» |
| `configured` | a listener answers but no proof (e.g. an external Tor we do not own) | «Настроено» |
| `unavailable` | no endpoint / proof failed / start failed, with a typed reason | «Недоступно» |

Typed reasons: `tor_not_installed`, `tor_circuit_invalid`, `tor_no_endpoint`,
`tor_start_failed`, `tor_managed_disabled`.

**The mode is policy, never health.** `tor_mode` is reported as a row in the
popover (`Состояние` / `Режим` / `SOCKS` / `Порт отвечает` / `Цепь проверена` /
`Метод` / `Процесс` / `Последняя проверка` / `Откат`). `Off` or `Auto` never
makes a healthy service look offline and never turns an unhealthy one green.

**Recovery is automatic.** A supervisor re-probes on a bounded cadence
(`tor_supervise_seconds`, `STARTING_DELAY_SECONDS` while bootstrapping), restarts
the owned process when it died and re-proves the route. `POST /tools/tor/ensure`
(«Проверить снова» / «Запустить Tor») is the manual fallback only: it returns the
current snapshot immediately and continues the work in the background.

**No clearnet fallback, ever.** A request that requires Tor fails closed with its
typed reason while Tor is unhealthy; it is never quietly sent over the direct
network, and the recovery keeps running in the background.

**Dependency.** Tor itself is not bundled. The managed start needs a local Tor:
typically the Tor Browser installation or `tor.exe` on `PATH`; without one the
honest state is `unavailable` / `tor_not_installed` with the manual path in the
popover.

`stop()` terminates **only** the process this service started, and the backend
stops it again on shutdown. An external Tor is never killed, adopted or
reconfigured.

# Tor in 0.8.5

Tor is a separate capability from TinyFish Web. `web_mode` does not enable or
disable it. Composer and Web & Tools expose **Tor Off / Auto / On**, independent
of Web mode.

## Modes

- **Off** — no Tor tools for that request.
- **Auto** — Tor only when the user explicitly asks (через Tor, `.onion`, hidden
  service, continue onion research, and English equivalents).
- **On** — Tor is allowed for factual/research prompts, but not for greetings,
  simple rewrites, math, or local-only tasks.
- «не используй Tor» / «don't use Tor» disables Tor for that request.

The model is expected to call `tor_search` then `tor_fetch` itself. If the user
has explicit Tor intent and the planner returns no Tor tool, the server injects
a safe `tor_search` (and may fetch/follow within limits) with
`origin=server_policy`. Audit never pretends a server-injected call was
model-selected.

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

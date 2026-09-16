# Tor in 0.8.0

Tor is a separate capability from TinyFish Web. `web_mode` does not enable or
disable it. The user toggle is `tor_enabled` in Web & Tools settings.

## Providers

- `TorTransportProvider` — SOCKS5h to loopback Tor (`127.0.0.1:9050` by default).
  Destination hostnames are sent as SOCKS ATYP `0x03`. `.onion` is never passed to
  the local DNS resolver.
- `TorSearchProvider` — GET against configured `TOR_SEARCH_PROVIDERS` URL
  templates (`{query}` or `__QUERY__`), also through that transport.
- `TorFetchProvider` — reads http(s) URLs, including `.onion`, through the same
  transport.

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
`TOR_OFFICIAL_MAPPING` remains provenance only and is never used as a search index.

## Authority

Official means a confirmable mapping from that list (or an equivalent official
clearnet/onion pair). Reachability never implies official:

- `OFFICIAL_AND_REACHABLE`
- `REACHABLE_UNVERIFIED`
- `OFFICIAL_UNREACHABLE`

## Limits

SOCKS is loopback-only. TinyFish is not used. Paid Agent/Browser are not Tor
transports.

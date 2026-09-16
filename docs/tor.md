# Tor in 0.8.0

Tor is a separate capability from TinyFish Web. `web_mode` does not enable or
disable it. The user toggle is `tor_enabled` in Web & Tools settings.

## Providers

- `TorTransportProvider` — SOCKS5h to loopback Tor (`127.0.0.1:9050` by default).
  Destination hostnames are sent as SOCKS ATYP `0x03`. `.onion` is never passed to
  the local DNS resolver.
- `TorSearchProvider` — GET against configured `TOR_SEARCH_PROVIDERS` URL
  templates (`{query}`), also through that transport.
- `TorFetchProvider` — reads http(s) URLs, including `.onion`, through the same
  transport.

A curated `TOR_OFFICIAL_MAPPING` is provenance only. It is not a search index
and is never returned as if it were Tor Search hits.

If `TOR_SEARCH_PROVIDERS` is empty, `tor_search` fails closed with
`tor_search_not_configured`. The UI shows **Tor Search provider not configured**.
`tor_fetch` of a known URL still works when Tor SOCKS is reachable.

## Authority

Official means a confirmable mapping from that list (or an equivalent official
clearnet/onion pair). Reachability never implies official:

- `OFFICIAL_AND_REACHABLE`
- `REACHABLE_UNVERIFIED`
- `OFFICIAL_UNREACHABLE`

## Limits

SOCKS is loopback-only. TinyFish is not used. Paid Agent/Browser are not Tor
transports.

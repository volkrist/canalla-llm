import type { Api } from "./api";
import { count, flag, record, text } from "./payload";

/** The Tor service, as the backend reports it.
 *
 * Health and policy travel in the same payload and must never be confused:
 *
 * * **health** — state, listening socket, verified route, binary: what a real check proved;
 * * **policy** — `mode` (the composer's `off` / `auto` / `on`): what the user chose for chat and
 *   tools.
 *
 * A policy never changes health. A proven route stays `ready` while the mode is `off`, and a mode
 * never turns a healthy service into an offline one. There is no clearnet fallback at any point, so
 * a request that needs Tor fails closed instead of leaking direct.
 */

export interface TorBinary {
  /** The `tor.exe` the backend manages. A path, never a secret. */
  path: string;
  /** `tor_browser` | `standalone` | `configured`, or `null` when the backend did not say. */
  source: string | null;
}

/** `subsystems.tor.details` from `GET /status`. A field the backend does not send stays `null`,
 *  and the row is dropped: a missing answer is never rendered as a value. */
export interface TorDetails {
  /** Usage policy, never health. */
  mode: string | null;
  proxy_host: string | null;
  proxy_port: number | null;
  configured_port: number | null;
  socks_listening: boolean | null;
  verified_chain: boolean | null;
  verified_at: string | null;
  method: string | null;
  /** True when the proven endpoint is our own managed `tor` process. */
  managed: boolean | null;
  binary: TorBinary | null;
  /** False when the payload had no `binary` field at all: "not found" and "not said" differ. */
  binary_known: boolean;
  /** Always `none`: this product has no clearnet fallback. */
  fallback: string | null;
}

export function parseTorBinary(value: unknown): TorBinary | null {
  const raw = record(value);
  const path = raw ? text(raw.path) : null;
  if (!path) return null;
  return { path, source: raw ? text(raw.source) : null };
}

export function parseTorDetails(
  details: Record<string, unknown> | null | undefined,
): TorDetails {
  const raw = details || {};
  return {
    mode: text(raw.mode),
    proxy_host: text(raw.proxy_host),
    proxy_port: count(raw.proxy_port),
    configured_port: count(raw.configured_port),
    socks_listening: flag(raw.socks_listening),
    verified_chain: flag(raw.verified_chain),
    verified_at: text(raw.verified_at),
    method: text(raw.method),
    managed: flag(raw.managed),
    binary: parseTorBinary(raw.binary),
    binary_known: "binary" in raw,
    fallback: text(raw.fallback),
  };
}

/** `127.0.0.1:9050`, or only the part the backend actually reported. */
export function torEndpoint(details: TorDetails): string | null {
  const { proxy_host: host, proxy_port: port } = details;
  if (host && port !== null) return `${host}:${port}`;
  if (host) return host;
  return port === null ? null : String(port);
}

/** Where the managed `tor` came from. A source this build does not know is shown as sent,
 *  never guessed. */
export function torBinarySourceLabel(source: string): string {
  if (source === "tor_browser") return "Tor Browser";
  if (source === "standalone") return "Отдельная установка Tor";
  if (source === "configured") return "TOR_BINARY_PATH";
  return source;
}

/** The binary row, said exactly as far as the backend answered: a known binary is named by its
 *  source and path, an absent one is "не найден", and a payload that never answered stays
 *  "неизвестно" instead of claiming a missing file. */
export function torBinaryText(details: TorDetails): string {
  if (!details.binary_known) return "неизвестно";
  const binary = details.binary;
  if (!binary) return "не найден";
  const source = binary.source
    ? torBinarySourceLabel(binary.source)
    : "источник неизвестен";
  return `${source} · ${binary.path}`;
}

/** The one honest dependency note: the service is down *and* the backend said there is no binary
 *  to run. It never fires on a payload that simply did not answer. */
export function torMissingBinary(details: TorDetails | null): boolean {
  return details !== null && details.binary_known && details.binary === null;
}

/** What the manual action will do, said honestly: with no binary there is nothing to start, so
 *  that case stays a plain re-check and the Settings note explains the dependency. */
export function torEnsureText(details: TorDetails | null): string {
  if (details && details.binary && details.socks_listening === false) {
    return "Запустить Tor";
  }
  return "Проверить снова";
}

/**
 * `POST /tools/tor/ensure` — the manual «Проверить снова».
 *
 * The service answers at once and keeps working in the background (a cold Tor bootstraps for a
 * minute or more), so the caller re-reads the authoritative `/status` snapshot right after. Nothing
 * here can widen a route: it is the same discovery → managed start → proof path the supervisor
 * uses, with no clearnet fallback.
 */
export async function ensureTorService(api: Api): Promise<void> {
  await api.json<{ requested?: boolean }>("/tools/tor/ensure", {
    method: "POST",
  });
}

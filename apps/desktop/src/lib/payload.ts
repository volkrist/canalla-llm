/** Narrowing for hand-typed JSON payloads.
 *
 * `Api.json<T>` proves nothing at runtime, so a field is `unknown` until its shape is checked.
 * These helpers keep that check in one place and never invent a value: a field that is missing,
 * empty or of the wrong type stays `null`, and the caller then omits the row instead of showing a
 * made-up one. Only the fields a surface renders are read.
 */

export function record(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

/** A non-empty string, or `null`. An empty string is "not said", never a value. */
export function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

export function flag(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

export function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

import type { CloudComputeSession, CloudComputeStatus } from "./cloud";

/** The user's own compute policy: the values a user sets and reads, and the honest
 *  statements about them. Nothing here starts, stops, redeems or changes anything —
 *  a policy stays the user's decision.
 *
 *  Since 1.0 these values are preferences, not product caps: the backend defaults are
 *  $0.52/hour and $3.00/session, and every authenticated user may raise or lower both
 *  for themselves inside the technical bounds ($100/hour, $1000/session).
 */

/** Gateway compute states in which a Pod exists and is billed. `searching`, `gpu_found`
 *  and `creating` are deliberately absent: a planned rate is not a running GPU. */
const RUNNING_COMPUTE_STATES = new Set([
  "starting_pod",
  "loading_model",
  "ready",
  "generating",
  "stopping",
]);

/** The shared session a panel may describe as running, or null. `GET /cloud/status` does
 *  not carry a rate, so this reads the last Gateway compute answer and refuses to present
 *  a planned or a stopped one as current. Unknown is silence, never a guess. */
export function runningSharedSession(
  compute: CloudComputeStatus | null,
): CloudComputeSession | null {
  if (!compute || !RUNNING_COMPUTE_STATES.has(compute.state)) return null;
  return compute.session;
}

/** Money a user sets and reads. Two decimals, the shape the stored policy has
 *  ($0.52, $3.00); the panel keeps its own three-decimal format for measured cost. */
export function policyMoney(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `$${parsed.toFixed(2)}` : "—";
}

/** §27: shared compute is global, so the running Pod can cost more than the limit of the
 *  installation that lowered its own policy while that Pod was already up. Stated as a
 *  fact, never as an error, and never repaired automatically: raising the limit stays the
 *  user's own decision in the panel. An unknown rate or an unread policy says nothing. */
export function overLimitRateLine(
  session: CloudComputeSession | null,
  maxHourlyPrice: number | string | null | undefined,
): string | null {
  if (!session) return null;
  const rate = Number(session.hourly_rate_usd);
  const limit = Number(maxHourlyPrice);
  if (!Number.isFinite(rate) || !Number.isFinite(limit)) return null;
  if (limit <= 0 || rate <= limit) return null;
  return (
    `Сейчас работает общий GPU за $${rate.toFixed(2)}/час — это выше вашего предела ` +
    `$${limit.toFixed(2)}/час. Можно повысить предел в настройках ниже.`
  );
}

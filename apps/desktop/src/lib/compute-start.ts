// The one decision the «Найти GPU и подключиться» button makes about *who* owns compute.
//
// In shared mode the Gateway owns the RunPod account, and the local lifecycle routes
// (`/compute/search`, `/compute/start`, `/compute/stop`) are deliberately refused with
// `gateway_managed_compute`. A button that still called them showed that refusal instead of
// starting anything. This function is the single routing point, so the regression is testable
// without rendering the panel.

export type ComputeStartRoute = "gateway" | "local";

export interface StartComputeDeps {
  /** `isSharedMode(cloud.status)` — the app's own mode, never a user setting. */
  shared: boolean;
  /** Save the user's own limits before anything is started. */
  savePreferences: () => Promise<void>;
  /** `ensureCloudCompute()`: the same bounded Gateway ensure the chat path uses. */
  ensureGateway: () => Promise<void>;
  /** The direct-mode quote/search flow. */
  searchLocal: () => Promise<void>;
}

/** Save first, then start through exactly one mechanism. Never both. */
export async function startCompute(
  deps: StartComputeDeps,
): Promise<ComputeStartRoute> {
  await deps.savePreferences();
  if (deps.shared) {
    await deps.ensureGateway();
    return "gateway";
  }
  await deps.searchLocal();
  return "local";
}

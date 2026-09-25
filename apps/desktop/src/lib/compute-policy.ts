/**
 * The compute policy rules the panel must obey, kept out of the component so they can be proven.
 *
 * These exist because the installed product got them wrong in a way the user could see: the panel
 * seeded a form with the GPU name `NVIDIA L40S`, showed an empty *placeholder* that looked like a
 * filled value, and let the user submit a manual selection with no card — which the backend then
 * refused with a red banner. Nothing here is cosmetic: each rule below is a state the product either
 * promises or must refuse.
 *
 * ## The promise
 *
 * `automatic` means "the allocator chooses any compatible GPU". In that mode there is no pin, the
 * user is never asked to type a GPU name, and `gpu_id` is `null` — both in the panel and on the
 * wire. A pin left over from an earlier manual choice must not survive a switch back to automatic,
 * because a value the mode does not use is a value the user cannot see.
 *
 * ## The refusal
 *
 * `manual` with no card named is the one state that cannot work: the backend refuses it with
 * `auto_connect` set (`ComputePreferences.enforce`), and the panel must say so before submitting
 * rather than let a 422 arrive as an unexplained error. Automatic mode with no pin is *valid* and is
 * the default — it must never produce an error, which is what the operator's red banner actually was.
 */

export type ComputeSelection = "automatic" | "manual";

/** The subset of the preferences this module reasons about. Extra fields pass through untouched. */
export interface ComputePolicyShape {
  selection: ComputeSelection;
  gpu_id?: string | null;
}

/**
 * What the panel assumes before the server answers.
 *
 * `gpu_id` is `null` and no card is named. Seeding a real GPU name here was the original defect:
 * the name travelled into the form, into the wire payload and into what the user believed was
 * selected.
 */
export const COMPUTE_POLICY_DEFAULTS = {
  selection: "automatic" as ComputeSelection,
  gpu_id: null as string | null,
};

/** The one state that cannot work: a manual pin with nothing pinned. */
export function manualNeedsGpu(policy: ComputePolicyShape): boolean {
  return policy.selection === "manual" && !policy.gpu_id;
}

/**
 * The preferences as they may leave the panel.
 *
 * Automatic mode drops the pin. Manual mode keeps it (the caller has already checked it exists).
 */
export function policyForWire<T extends ComputePolicyShape>(policy: T): T {
  return {
    ...policy,
    gpu_id: policy.selection === "manual" ? policy.gpu_id || null : null,
  };
}

/** The sentence to show for a refusal, or `""` when the policy is usable. */
export function policyRefusal(policy: ComputePolicyShape): string {
  if (!manualNeedsGpu(policy)) return "";
  return "Выберите GPU или переключитесь на автоматический выбор.";
}

/** Whether the actions that start compute may run for this policy. */
export function policyIsActionable(policy: ComputePolicyShape): boolean {
  return !manualNeedsGpu(policy);
}

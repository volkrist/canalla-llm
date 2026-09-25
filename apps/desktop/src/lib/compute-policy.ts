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
 * `manual` with no card named is the one state that cannot work — whether it arrives through the GPU
 * selection or through the `manual` strategy, because they mean the same thing to the backend. The
 * backend refuses it with `auto_connect` set (`ComputePreferences.enforce`) and with the manual
 * strategy (`_caps`), and the panel must say so before submitting rather than let a 422 arrive as an
 * unexplained error. Automatic mode with no pin is *valid* and is the default — it must never
 * produce an error, which is what the operator's red banner actually was.
 *
 * ## The order
 *
 * `strategy` orders the candidates and never widens them: the VRAM floor and the user's own price
 * ceiling bound all four values, so picking one cannot spend more than the user's own maximum.
 */

export type ComputeSelection = "automatic" | "manual";

/**
 * How the allocator orders the candidates it may book.
 *
 * The four values are the backend's own closed set, and the default is `balanced` — the one that
 * prefers a card that is actually bookable and the placement that already holds the model over a
 * marginally cheaper one. `manual` is a pin, so it needs a card, exactly like `selection`.
 */
export type ComputeStrategy = "balanced" | "fastest" | "cheapest" | "manual";

/** The subset of the preferences this module reasons about. Extra fields pass through untouched. */
export interface ComputePolicyShape {
  selection: ComputeSelection;
  strategy?: ComputeStrategy;
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
  strategy: "balanced" as ComputeStrategy,
  gpu_id: null as string | null,
};

/** The one state that cannot work: a pin asked for with nothing pinned. */
export function manualNeedsGpu(policy: ComputePolicyShape): boolean {
  return pinMissing(policy);
}

/**
 * Either control may ask for "this exact card": the GPU selection and the `manual` strategy.
 *
 * Both mean the same thing to the backend, so both must be checked the same way — otherwise the
 * panel would submit a state the server refuses.
 */
export function pinMissing(policy: ComputePolicyShape): boolean {
  const pinned = policy.selection === "manual" || policy.strategy === "manual";
  return pinned && !policy.gpu_id;
}

/**
 * The preferences as they may leave the panel.
 *
 * A pin is kept when either control asked for it and dropped otherwise: a value the chosen mode
 * does not use is a value the user cannot see.
 */
export function policyForWire<T extends ComputePolicyShape>(policy: T): T {
  const pinned = policy.selection === "manual" || policy.strategy === "manual";
  return {
    ...policy,
    gpu_id: pinned ? policy.gpu_id || null : null,
  };
}

/** The sentence to show for a refusal, or `""` when the policy is usable. */
export function policyRefusal(policy: ComputePolicyShape): string {
  if (!pinMissing(policy)) return "";
  if (policy.selection === "manual") {
    return "Выберите GPU или переключитесь на автоматический выбор.";
  }
  return "Для стратегии «Вручную» выберите GPU или вернитесь к «Сбалансированной» — она берёт любую подходящую карту.";
}

/** Whether the actions that start compute may run for this policy. */
export function policyIsActionable(policy: ComputePolicyShape): boolean {
  return !pinMissing(policy);
}

/**
 * The four orderings, in the order the panel offers them, with the sentence each one shows.
 *
 * The labels say what changes, because the four values differ only in *order*: none of them can
 * raise the price ceiling or lower the VRAM floor.
 */
export const COMPUTE_STRATEGIES: { value: ComputeStrategy; label: string }[] = [
  { value: "balanced", label: "Сбалансированная · доступность и цена" },
  { value: "fastest", label: "Быстрее · первая доступная карта" },
  { value: "cheapest", label: "Дешевле · минимальная цена" },
  { value: "manual", label: "Вручную · только выбранная карта" },
];

/**
 * The preferences after the user picks a strategy.
 *
 * `manual` *is* a pin, so it moves the GPU control with it instead of asking the user to make the
 * same choice twice; the three orderings leave the GPU control exactly as it was, because "only
 * this card, cheapest first" is a coherent request.
 */
export function appliedStrategy<T extends ComputePolicyShape>(
  policy: T,
  strategy: ComputeStrategy,
): T {
  return {
    ...policy,
    strategy,
    // `manual` is a pin by another name, so it moves the GPU control with it rather than asking the
    // user to make the same choice twice. The three orderings leave the GPU control exactly as it
    // was: "only this card, cheapest first" is a coherent request.
    selection: strategy === "manual" ? "manual" : policy.selection,
  };
}

/**
 * The preferences after the user picks a GPU mode.
 *
 * Leaving `manual` drops the pin — a value the mode does not use is a value the user cannot see — and
 * going back to `automatic` also moves the strategy off `manual`, which is a pin by another name.
 */
export function appliedSelection<T extends ComputePolicyShape>(
  policy: T,
  selection: ComputeSelection,
): T {
  return {
    ...policy,
    selection,
    strategy:
      selection === "automatic" && policy.strategy === "manual"
        ? "balanced"
        : policy.strategy,
    gpu_id: selection === "manual" ? policy.gpu_id : null,
  };
}

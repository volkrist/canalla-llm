// Stopping the paid AI must also stop the answer it is paying for.
//
// The Gateway intentionally defers a manual stop while a generation is in flight (it must not
// interrupt another installation's work), so a client that only asks to stop compute leaves the
// Pod billing until the model finishes the whole answer. Aborting the stream first releases the
// generation, which lets the pending stop terminate the Pod promptly.

export async function stopPaidAi(options: {
  stopGeneration: () => void;
  stopCompute: () => Promise<unknown>;
  refresh?: () => Promise<unknown>;
}): Promise<void> {
  options.stopGeneration();
  await options.stopCompute();
  if (options.refresh) await options.refresh();
}

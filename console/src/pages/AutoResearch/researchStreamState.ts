export function latestResearchSequence(
  events: ReadonlyArray<{ sequence?: number }>,
): number {
  return events.reduce(
    (latest, event) =>
      typeof event.sequence === "number"
        ? Math.max(latest, event.sequence)
        : latest,
    -1,
  );
}

export function nextResearchSequence(
  current: number,
  candidate: unknown,
): number | null {
  return typeof candidate === "number" && candidate > current
    ? candidate
    : null;
}

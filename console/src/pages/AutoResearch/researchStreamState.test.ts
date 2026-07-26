import { describe, expect, it } from "vitest";

import {
  latestResearchSequence,
  nextResearchSequence,
} from "./researchStreamState";

describe("research stream sequence state", () => {
  it("initializes from the newest snapshot event", () => {
    expect(
      latestResearchSequence([
        { sequence: 2 },
        { sequence: 5 },
        { sequence: 4 },
      ]),
    ).toBe(5);
  });

  it("rejects missing, old, and duplicate live sequences", () => {
    expect(nextResearchSequence(5, undefined)).toBeNull();
    expect(nextResearchSequence(5, 4)).toBeNull();
    expect(nextResearchSequence(5, 5)).toBeNull();
    expect(nextResearchSequence(5, 6)).toBe(6);
  });
});

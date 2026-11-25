import { describe, expect, it } from "vitest";
import { answerNow, type Heard } from "./introanswer";

describe("an answer said aloud in the introduction (E-253)", () => {
  const level: Heard = { step: "level", value: 2, heard: "niveau twee" };

  it("counts once, at its own step", () => {
    expect(answerNow(level, null, null, "level")).toBe(level);
    expect(answerNow(level, level, null, "level")).toBeNull(); // already used
    expect(answerNow(level, null, null, "learning")).toBeNull(); // another step
    expect(answerNow({ ...level, value: null }, null, null, "level")).toBeNull(); // not understood
  });

  it("does not count again in a new introduction (found in the real house: step 1 was skipped)", () => {
    expect(answerNow(level, null, level, "level")).toBeNull();
    const fresh: Heard = { step: "level", value: 3, heard: "niveau drie" };
    expect(answerNow(fresh, null, level, "level")).toBe(fresh);
  });
});

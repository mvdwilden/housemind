import { describe, expect, it } from "vitest";
import { speechWave, voiceLevel } from "./voice";

// Vesta moves with her voice while she speaks, like the meter while she listens (the
// administrator's wish, 20-10-2026).
describe("her movement with her voice", () => {
  it("is still when she does not speak", () => {
    expect(voiceLevel(1234)).toBe(0);
  });

  it("moves like speech when the sound cannot be measured", () => {
    const levels = Array.from({ length: 200 }, (_, i) => speechWave(i * 16));
    expect(Math.min(...levels)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...levels)).toBeLessThanOrEqual(1);
    expect(Math.max(...levels) - Math.min(...levels)).toBeGreaterThan(0.4); // it really moves
  });
});

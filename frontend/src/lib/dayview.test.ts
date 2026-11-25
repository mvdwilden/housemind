import { describe, expect, it } from "vitest";
import { onRing, partScore, powerFlow } from "./dayview";
import type { ReadyPart } from "./types";

describe("the day in pictures (E-435 – E-437)", () => {
  it("puts a moment of the day on the ring like a clock: midnight on top", () => {
    expect(onRing(0, 100, 120)).toEqual({ x: 120, y: 20 });
    expect(onRing(0.25, 100, 120)).toEqual({ x: 220, y: 120 }); // 06:00 on the right
    expect(onRing(0.5, 100, 120)).toEqual({ x: 120, y: 220 }); // noon at the bottom
  });

  it("scores a part by how often its skills went well", () => {
    const part = { skills: [{ bar: { value: 80, target: 90 } }, { bar: { value: 60, target: 90 } }] } as unknown as ReadyPart;
    expect(partScore(part)).toBe(70);
    expect(partScore({ skills: [] } as unknown as ReadyPart)).toBeNull();
  });

  it("shows what flows into the house and out of it", () => {
    const flow = powerFlow({ solar: 1500, grid: -600, batteries: [{ name: "Thuisaccu", watts: 300 }], car: { watts: 0 } });
    expect(flow.into.map((p) => [p.key, p.active])).toEqual([["solar", true]]);
    expect(flow.out.map((p) => [p.key, p.watts, p.active])).toEqual([
      ["to_grid", 600, true],
      ["battery", 300, true],
      ["car", 0, false],
    ]);
    const night = powerFlow({ solar: 0, grid: 400, batteries: [{ name: "Thuisaccu", watts: -700 }], car: null });
    expect(night.into.map((p) => [p.key, p.active])).toEqual([["solar", false], ["from_grid", true], ["battery", true]]);
  });
});

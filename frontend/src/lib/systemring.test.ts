import { describe, expect, it } from "vitest";
import { diskParts, ticks } from "./systemring";
import type { TidySystem } from "./types";

const GB = 2 ** 30;

function sys(disk: TidySystem["disk"], database: number | undefined, loose: number[]): TidySystem {
  return {
    at: "",
    backup: { state: "ok", days: 0 },
    database: { state: "ok", bytes: database },
    disk,
    keepAll: false,
    loose: loose.map((size, i) => ({ id: String(i), name: "b", date: "2026-09-01", size })),
  };
}

describe("the disk ring (E-451)", () => {
  it("splits the disk into database, backups, old loose backups, the rest and what is free", () => {
    const parts = diskParts(sys({ state: "ok", total: 62.3 * GB, used: 42.3 * GB, backups: 8 * GB }, 1.5 * GB, [0.5 * GB, 0.8 * GB]));
    expect(parts).not.toBeNull();
    expect(parts!.database).toBeCloseTo(1.5 * GB);
    expect(parts!.loose).toBeCloseTo(1.3 * GB);
    expect(parts!.backups).toBeCloseTo(6.7 * GB); // the loose ones counted once
    expect(parts!.other).toBeCloseTo(32.8 * GB);
    expect(parts!.free).toBeCloseTo(20 * GB);
  });

  it("has no ring for a disk it cannot see", () => {
    expect(diskParts(sys({ state: "unknown" }, 1, []))).toBeNull();
  });

  it("always makes 100 parts, and keeps a small part visible", () => {
    const parts = ticks([1.5, 6.7, 1.3, 32.8, 20]);
    expect(parts.reduce((a, b) => a + b, 0)).toBe(100);
    expect(parts.every((p) => p >= 1)).toBe(true);
    expect(ticks([0.001, 99.999])).toEqual([1, 99]);
    expect(ticks([0, 5, 0])).toEqual([0, 100, 0]);
  });
});

import { describe, expect, it } from "vitest";
import { afterText, keepOrder, type Moment } from "./temps";
import type { TempValue } from "./types";

const t = (key: string, p: Record<string, unknown> = {}): string => `${key}${p.n !== undefined ? `:${p.n}` : ""}`;

function three(home: number, sleep: number, away: number): Record<Moment, TempValue> {
  return {
    home: { value: home, from: "own" },
    sleep: { value: sleep, from: "start" },
    away: { value: away, from: "start_away" },
  };
}

describe("keepOrder", () => {
  it("lowering Thuis for heating takes Slapen and Weg down with it", () => {
    const v = three(16, 17, 17.5);
    keepOrder(v, "heat");
    expect([v.home.value, v.sleep.value, v.away.value]).toEqual([16, 16, 16]);
    expect(v.sleep.from).toBe("own");
  });
  it("what stays below Thuis keeps where it came from", () => {
    const v = three(20.5, 17, 17.5);
    keepOrder(v, "heat");
    expect([v.sleep.value, v.away.value]).toEqual([17, 17.5]);
    expect(v.away.from).toBe("start_away");
  });
  it("for cooling Slapen and Weg are never colder than Thuis", () => {
    const v = three(26, 23, 27);
    keepOrder(v, "cool");
    expect([v.sleep.value, v.away.value]).toEqual([26, 27]);
  });
});

describe("afterText", () => {
  it("says at once, minutes, hours and a day", () => {
    expect([0, 0.5, 2, 24].map((h) => afterText(h, t))).toEqual([
      "ui.temps.after.now",
      "ui.temps.after.minutes:30",
      "ui.temps.after.hours:2",
      "ui.temps.after.day",
    ]);
  });
});

import { describe, expect, it } from "vitest";
import { MAX_STEPS, placeTip, tourDue, toured, tourFor } from "./tour";

const keys = (steps: { key: string }[]) => steps.map((s) => s.key);

describe("the tour (E-249)", () => {
  it("level 1: Vesta, Vraag, Pauze, Mijn voorkeuren and Help, between welcome and done", () => {
    expect(keys(tourFor(1, false, []))).toEqual(["welcome", "vesta", "ask", "pause", "prefs", "help", "done"]);
    expect(keys(tourFor(1, true, []))).toEqual(["welcome", "vesta", "ask", "pause", "prefs", "help", "settings", "done"]);
  });

  it("at most eight steps by itself; Help shows them all", () => {
    const first = tourFor(4, true, []);
    expect(first.length).toBe(MAX_STEPS);
    expect(first[0].key).toBe("welcome");
    expect(first.at(-1)?.key).toBe("done");
    expect(keys(tourFor(4, true, [], true))).toContain("brain_expert");
    expect(keys(tourFor(4, false, [], true))).not.toContain("settings"); // only administrators
  });

  it("after a higher level only what is new, with 'Wil je zien wat er nu bij is?' first", () => {
    expect(keys(tourFor(4, false, [1, 2]))).toEqual(["more", "brain", "brain_expert", "done"]);
    expect(keys(tourFor(4, false, [1, 2, 3]))).toEqual(["more", "brain_expert", "done"]);
  });

  it("starts by itself once per level, never on a wall tablet or during the introduction", () => {
    const base = { level: 2, seen: [] as number[], tablet: false, introducing: false, busy: false };
    expect(tourDue(base)).toBe(true);
    expect(tourDue({ ...base, seen: [2] })).toBe(false);
    expect(tourDue({ ...base, tablet: true })).toBe(false);
    expect(tourDue({ ...base, introducing: true })).toBe(false);
    expect(tourDue({ ...base, busy: true })).toBe(false);
    expect(toured(3, [1])).toEqual([1, 2, 3]);
  });

  it("puts the card under the lit part, else above it, always inside the screen", () => {
    const screen = { width: 400, height: 800 };
    const tip = { width: 340, height: 200 };
    expect(placeTip({ top: 100, bottom: 150, left: 380 }, screen, tip)).toEqual({ top: 164, left: 48 });
    expect(placeTip({ top: 600, bottom: 700, left: 20 }, screen, tip).top).toBe(386);
    expect(placeTip(null, screen, tip)).toEqual({ top: 300, left: 30 });
  });
});

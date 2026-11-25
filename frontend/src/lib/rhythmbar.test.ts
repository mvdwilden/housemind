import { describe, expect, it } from "vitest";
import { barLabel, barParts, clock, nowAt, unsureParts } from "./rhythmbar";
import type { RhythmDay } from "./types";

const WORDS: Record<string, string> = {
  "rhythm.legend.sleep": "Slapen",
  "rhythm.legend.morning": "Ochtend",
  "rhythm.legend.away": "Weg",
  "rhythm.legend.home": "Thuis",
  "rhythm.bar.learning": "leert nog",
  "rhythm.bar.not_enough": "nog niet genoeg nachten",
};
const t = (key: string): string => WORDS[key] ?? key;

const workday: RhythmDay = {
  key: "workday",
  label: "Werkdagen",
  bar: [
    { kind: "sleep", from: 0, to: 410 },
    { kind: "morning", from: 410, to: 465 },
    { kind: "away", from: 465, to: 1050 },
    { kind: "home", from: 1050, to: 1390 },
    { kind: "sleep", from: 1390, to: 1440 },
  ],
  moments: [],
};

describe("the rhythm bar (E-507)", () => {
  it("places each part on 24 hours, with an icon only where it fits", () => {
    const parts = barParts(workday, t);
    expect(parts.map((p) => [p.kind, Math.round(p.left * 10) / 10, Math.round(p.width * 10) / 10])).toEqual([
      ["sleep", 0, 28.5],
      ["morning", 28.5, 3.8],
      ["away", 32.3, 40.6],
      ["home", 72.9, 23.6],
      ["sleep", 96.5, 3.5],
    ]);
    expect(parts.map((p) => p.icon)).toEqual(["moon", null, "away", "home", null]);
    expect(parts[1].title).toBe("Ochtend 06:50–07:45");
  });

  it("says the bar in one sentence, and that it is still learning", () => {
    expect(barLabel(workday, t)).toBe(
      "Werkdagen: slapen 00:00–06:50, ochtend 06:50–07:45, weg 07:45–17:30, thuis 17:30–23:10, slapen 23:10–24:00",
    );
    const learning = { ...workday, bar: workday.bar.slice(0, 1), learning: true };
    expect(barLabel(learning, t)).toBe("Werkdagen: slapen 00:00–06:50; leert nog");
    expect(barLabel({ ...workday, bar: [] }, t)).toBe("Werkdagen: nog niet genoeg nachten");
  });

  it("stripes what is not sure yet, and puts now only on today's bar", () => {
    const day = { ...workday, unsure: [{ from: 390, to: 445 }] };
    expect(unsureParts(day).map((u) => [Math.round(u.left * 10) / 10, Math.round(u.width * 10) / 10])).toEqual([
      [27.1, 3.8],
    ]);
    expect(unsureParts(workday)).toEqual([]);
    expect(nowAt(workday, "workday", 720)).toBe(50);
    expect(nowAt(workday, "free", 720)).toBeNull();
    expect(nowAt({ ...workday, bar: [] }, "workday", 720)).toBeNull();
    expect(clock(1440)).toBe("24:00");
  });
});

import { describe, expect, it } from "vitest";
import { byRoom, demoNow, demoTime, lineTime, percent, speedIndex, speedKey } from "./play";

const live = { now: "2026-10-22T17:00:00Z", speed: 20, running: true, busy: null, held: false };

describe("the demo house's time (E-321)", () => {
  it("runs on at the house's speed between pictures", () => {
    expect(demoNow(live, 1000, 4000).toISOString()).toBe("2026-10-22T17:01:00.000Z");
    expect(demoNow({ ...live, speed: 0.05 }, 1000, 21000).toISOString()).toBe("2026-10-22T17:00:01.000Z");
    expect(demoNow({ ...live, speed: 1 }, 1000, 61000).toISOString()).toBe("2026-10-22T17:01:00.000Z");
  });
  it("stands still after Stop", () => {
    expect(demoNow({ ...live, running: false }, 1000, 9000).toISOString()).toBe("2026-10-22T17:00:00.000Z");
  });
  it("stands still while a jump goes on, and never goes back", () => {
    const busy = { ...live, busy: { what: "week", kind: "jump", done: 0.4 } };
    expect(demoNow(busy, 1000, 9000).toISOString()).toBe("2026-10-22T17:00:00.000Z");
    expect(demoNow(live, 5000, 1000).toISOString()).toBe("2026-10-22T17:00:00.000Z");
  });
  it("shows the day and the time", () => {
    expect(demoTime(new Date("2026-10-22T17:04:00"), "nl")).toContain("17:04");
  });
  it("adds the weekday to a line from another day", () => {
    const today = new Date("2026-10-22T19:00:00");
    expect(lineTime({ at: "18:12", day: "2026-10-22" }, today, "nl")).toBe("18:12");
    expect(lineTime({ at: "07:57", day: "2026-10-21" }, today, "en")).toBe("Wed 07:57");
  });
  it("names the speed and the progress", () => {
    expect(speedKey(10)).toBe("play.speed.x10");
    expect(speedKey(0.05)).toBe("play.speed.slower20");
    expect(speedKey(0.1)).toBe("play.speed.slower10");
    const speeds = [0.05, 0.1, 1, 10, 20];
    expect(speeds.map((s) => speedIndex(speeds, s))).toEqual([0, 1, 2, 3, 4]);
    expect(speedIndex(speeds, 60)).toBe(4);
    expect(percent(0.426)).toBe(43);
    expect(percent(1.2)).toBe(100);
  });
  it("groups switches by room", () => {
    const groups = byRoom([
      { room: "Gang", id: "a" },
      { room: "Keuken", id: "b" },
      { room: "Gang", id: "c" },
    ]);
    expect(groups.map(([room, items]) => [room, items.length])).toEqual([["Gang", 2], ["Keuken", 1]]);
  });
});

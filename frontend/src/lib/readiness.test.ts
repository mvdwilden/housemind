import { describe, expect, it } from "vitest";
import { filtered, grouped } from "./filters";
import { closestFirst, detailFacts, flagOf, onlyPart, readyAutomations, readyRows, ringOf, rowFilters, READY_STEPS, waits } from "./readiness";
import type { ReadyPart, ReadySkill } from "./types";

const t = (key: string): string => ({ "steps.observe": "Observeren", "ui.filter.title.room": "Ruimte" })[key] ?? key;

function skill(skillName: string, room: string, step: string, progress: number, extra: Partial<ReadySkill> = {}): ReadySkill {
  return {
    skill: skillName, area: room.toLowerCase(), what: `${skillName} in de ${room}`, skillName, room, step, stepName: step,
    heldBack: false, ready: false, progress, bar: { value: 0, target: 85 }, counters: [], text: "", ...extra,
  };
}

const PARTS: ReadyPart[] = [
  {
    domain: "lighting", name: "Verlichting", counts: {}, cap: null, capName: null, text: "", limit: false,
    skills: [
      skill("licht bij binnenkomen", "Keuken", "autonomous", 1),
      skill("licht bij binnenkomen", "Badkamer", "observe", 0.4),
      skill("licht uit bij weggaan", "Keuken", "propose", 0.9, { ready: true }),
    ],
    automations: [{ id: "automation.hal", name: "Hal", state: "learning", ready: 1, total: 2, text: "", rooms: ["Gang", "Keuken"] }],
  },
  {
    domain: "covers", name: "Raambekleding", counts: {}, cap: null, capName: null, text: "", limit: false,
    skills: [skill("gordijnen dicht", "Keuken", "propose", 0.7, { heldBack: true })],
  },
];

describe("Klaar voor autonomie? as one list with filters (wish from the real house)", () => {
  it("puts every skill × room of all parts in one list, each with its part", () => {
    const rows = readyRows(PARTS);
    expect(rows.map((r) => `${r.partName}: ${r.what}`)).toEqual([
      "Verlichting: licht bij binnenkomen in de Keuken",
      "Verlichting: licht bij binnenkomen in de Badkamer",
      "Verlichting: licht uit bij weggaan in de Keuken",
      "Raambekleding: gordijnen dicht in de Keuken",
    ]);
    expect(readyAutomations(PARTS).map((a) => [a.name, a.part])).toEqual([["Hal", "lighting"]]);
  });

  it("filters per room across parts, and groups per room with the closest to the next step first", () => {
    const rows = readyRows(PARTS);
    const kitchen = filtered(rows, rowFilters(PARTS, t), { room: ["Keuken"] });
    expect(kitchen).toHaveLength(3);
    const byRoom = grouped(kitchen, (r) => r.room).map(([room, list]) => [room, [...list].sort(closestFirst).map((r) => r.skillName)]);
    // Held back by a limit first, then ready, then how far; already Autonomous last.
    expect(byRoom).toEqual([["Keuken", ["gordijnen dicht", "licht uit bij weggaan", "licht bij binnenkomen"]]]);
  });

  it("finds what is ready for the next step, also when a limit holds it back", () => {
    const ready = filtered(readyRows(PARTS), rowFilters(PARTS, t), { ready: ["yes"] });
    expect(ready.map((r) => r.skillName).sort()).toEqual(["gordijnen dicht", "licht uit bij weggaan"]);
  });

  it("groups per step in the order of the steps", () => {
    const keys = grouped(readyRows(PARTS), (r) => r.step, READY_STEPS).map(([k]) => k);
    expect(keys).toEqual(["autonomous", "propose", "observe"]);
  });

  it("a tap on a part in the overview shows only that part; a second tap shows all again", () => {
    const once = onlyPart({ room: ["Keuken"] }, "lighting");
    expect(once).toEqual({ room: ["Keuken"], part: ["lighting"] });
    expect(onlyPart(once, "lighting").part).toEqual([]);
    expect(onlyPart({ part: ["lighting", "covers"] }, "covers").part).toEqual(["covers"]);
  });
});

describe("a quiet row (E-501, E-502)", () => {
  it("says in one flag what a rule waits for", () => {
    expect(flagOf(skill("a", "Hal", "observe", 0.5, { heldBack: true, ready: true }))).toBe("held");
    expect(flagOf(skill("a", "Hal", "observe", 0.5, { question: { kind: "off", id: "x", text: "", declined: false, button: "" } }))).toBe("question");
    expect(flagOf(skill("a", "Hal", "observe", 0.5, { steer: { ids: ["x"], text: "" } }))).toBe("steer");
    expect(flagOf(skill("a", "Hal", "propose", 1, { ready: true }))).toBe("ready");
    expect(flagOf(skill("a", "Hal", "observe", 0.2))).toBe("");
    expect(flagOf(skill("a", "Hal", "observe", 0, { unseen: true }))).toBe("unseen");
    expect(waits(skill("a", "Hal", "observe", 0.5, { steer: { ids: ["x"], text: "" } }))).toBe(true);
  });
  it("draws the ring against its target, and on Autonomous how often it went well", () => {
    expect(ringOf(skill("a", "Hal", "observe", 0.5, { bar: { value: 91, target: 70, history: 20 } }))).toEqual({ value: 91, target: 70, history: 20, yes: false });
    expect(ringOf(skill("a", "Hal", "autonomous", 1, { bar: { value: 0, target: 100, against: true }, good: 96 }))).toEqual({ value: 96, target: null, history: 0, yes: false });
  });
  it("splits the line of details into terms and values", () => {
    expect(detailFacts("lampen: Oprit · donker buiten: zon < 3° · 6 van de 7 keer goed")).toEqual([
      ["lampen", "Oprit"], ["donker buiten", "zon < 3°"], ["", "6 van de 7 keer goed"],
    ]);
    expect(detailFacts(undefined)).toEqual([]);
  });
});

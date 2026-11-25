import { describe, expect, it } from "vitest";
import { active, chips, filtered, grouped, howMany, matching, picked, toggle, type FilterDef } from "./filters";

interface Auto {
  name: string;
  rooms: string[];
  parts: string[];
  scene: boolean;
}

const items: Auto[] = [
  { name: "Hal licht bij beweging", rooms: ["Hal"], parts: ["lighting"], scene: false },
  { name: "Keuken licht", rooms: ["Keuken"], parts: ["lighting"], scene: false },
  { name: "Gordijnen keuken", rooms: ["Keuken"], parts: ["covers"], scene: false },
  { name: "Avondscène", rooms: ["Woonkamer", "Keuken"], parts: ["lighting"], scene: true },
  { name: "Melding", rooms: [], parts: ["messages"], scene: false },
];
const defs: FilterDef<Auto>[] = [
  { key: "room", title: "Ruimte", values: (a) => a.rooms, label: (v) => v || "Geen ruimte" },
  { key: "part", title: "Onderdeel", values: (a) => a.parts, label: (v) => v, order: ["lighting", "covers", "messages"] },
  { key: "scene", title: "Scène", values: (a) => (a.scene ? ["yes"] : []), label: () => "Start een scène of script", hideNone: true },
];

describe("ordering and filtering (E-258)", () => {
  it("is 'or' within one filter and 'and' between filters", () => {
    let chosen = toggle({}, "room", "Hal");
    chosen = toggle(chosen, "room", "Woonkamer");
    expect(filtered(items, defs, chosen).map((a) => a.name)).toEqual(["Hal licht bij beweging", "Avondscène"]);
    chosen = toggle(chosen, "part", "covers");
    expect(filtered(items, defs, chosen)).toEqual([]);
    chosen = toggle(toggle(chosen, "part", "covers"), "room", "Keuken");
    chosen = toggle(chosen, "part", "covers");
    expect(filtered(items, defs, chosen).map((a) => a.name)).toEqual(["Gordijnen keuken"]);
  });

  it("counts what is left with the other filters, keeps empty buttons and puts 'none' last", () => {
    const chosen = toggle({}, "part", "covers");
    const rooms = chips(items, defs[0], defs, chosen);
    expect(rooms.map((c) => [c.label, c.count])).toEqual([
      ["Hal", 0],
      ["Keuken", 1],
      ["Woonkamer", 0],
      ["Geen ruimte", 0],
    ]);
    const parts = chips(items, defs[1], defs, chosen);
    expect(parts.map((c) => [c.value, c.count, c.on])).toEqual([
      ["lighting", 3],
      ["covers", 1],
      ["messages", 1],
    ].map(([v, n]) => [v, n, v === "covers"]));
  });

  it("shows a single on/off button for 'starts a scene or script'", () => {
    expect(chips(items, defs[2], defs, {}).map((c) => [c.value, c.count])).toEqual([["yes", 1]]);
    expect(filtered(items, defs, toggle({}, "scene", "yes")).map((a) => a.name)).toEqual(["Avondscène"]);
  });

  it("searches together with the filters, every word somewhere", () => {
    const chosen = toggle({}, "room", "Keuken");
    expect(filtered(items, defs, chosen, "licht keuken", (a) => a.name).map((a) => a.name)).toEqual(["Keuken licht"]);
    expect(active(chosen)).toBe(true);
    expect(active(toggle(chosen, "room", "Keuken"))).toBe(false);
  });

  it("groups in the given order, the rest by name and 'none' last", () => {
    const groups = grouped(items, (a) => a.rooms[0] ?? "", ["Woonkamer"]);
    expect(groups.map(([k, list]) => [k, list.length])).toEqual([
      ["Woonkamer", 1],
      ["Hal", 1],
      ["Keuken", 2],
      ["", 1],
    ]);
  });
});

describe("the Filter button and the chosen chips (E-276)", () => {
  it("counts every chosen value and lists them in the order of the filters", () => {
    const chosen = toggle(toggle(toggle({}, "part", "covers"), "room", "Keuken"), "scene", "yes");
    expect(howMany(chosen)).toBe(3);
    expect(picked(defs, chosen).map((c) => c.label)).toEqual(["Keuken", "covers", "Scène"]);
    expect(howMany(toggle(chosen, "room", "Keuken"))).toBe(2);
  });

  it("finds a value in a long filter by typing, and keeps what is ticked", () => {
    const rooms = chips(items, defs[0], defs, toggle({}, "room", "Hal"));
    expect(matching(rooms, "keu").map((c) => c.label)).toEqual(["Hal", "Keuken"]);
    expect(matching(rooms, "").length).toBe(rooms.length);
  });
});


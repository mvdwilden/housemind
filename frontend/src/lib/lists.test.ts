import { describe, expect, it } from "vitest";
import type { FilterDef } from "./filters";
import { ALL_AT_ONCE, chosenColumns, csv, fileName, markdown, overview, pageOf, separator, table, type ListDef } from "./lists";

interface Row {
  id: string;
  name: string;
  room: string;
  kind: string;
  state: string;
}

const rows: Row[] = Array.from({ length: 45 }, (_, i) => ({
  id: `light.l${i}`,
  name: `Lamp ${i}`,
  room: i % 3 ? "Keuken" : "",
  kind: i % 5 ? "light" : "switch",
  state: i % 9 ? "on" : "unavailable",
}));
const def: ListDef<Row> = {
  key: "devices",
  name: { key: "name", title: "Naam", value: (r) => r.name },
  columns: [
    { key: "room", title: "Ruimte", value: (r) => r.room, on: true },
    { key: "kind", title: "Soort", value: (r) => r.kind, on: true },
    { key: "state", title: "Status", value: (r) => r.state },
  ],
  id: (r) => r.id,
};
const kindDef: FilterDef<Row> = { key: "kind", title: "Soort", values: (r) => [r.kind], label: (v) => v };

describe("pages (E-325, B-196)", () => {
  it("shows 1–20 of 45, then 21–40 and 41–45", () => {
    expect(pageOf(rows, 1, 20)).toMatchObject({ page: 1, pages: 3, from: 1, to: 20 });
    expect(pageOf(rows, 2, 20).items[0].name).toBe("Lamp 20");
    expect(pageOf(rows, 3, 20)).toMatchObject({ from: 41, to: 45 });
  });

  it("keeps a page beyond the end on the last one, and an empty list on page 1 of 1", () => {
    expect(pageOf(rows, 9, 20)).toMatchObject({ page: 3, from: 41 });
    expect(pageOf([], 4, 20)).toEqual({ items: [], page: 1, pages: 1, from: 0, to: 0 });
  });

  it("shows all, but in a big house first a part until the rest is asked for (E-322)", () => {
    const many = Array.from({ length: ALL_AT_ONCE + 7 }, (_, i) => i);
    expect(pageOf(rows, 1, 0)).toMatchObject({ pages: 1, from: 1, to: 45 });
    expect(pageOf(many, 1, 0).to).toBe(ALL_AT_ONCE);
    expect(pageOf(many, 1, 0, true).to).toBe(ALL_AT_ONCE + 7);
  });
});

describe("columns (B-198)", () => {
  it("are the defaults until a person chooses, and the name is never one of them", () => {
    expect(chosenColumns(def, undefined).map((c) => c.key)).toEqual(["room", "kind"]);
    expect(chosenColumns(def, ["state"]).map((c) => c.key)).toEqual(["state"]);
    expect(chosenColumns(def, ["name", "state"]).map((c) => c.key)).toEqual(["state"]);
  });
});

describe("export (B-197)", () => {
  const data = table(
    [{ id: "light.a", name: "=SOM(A1)", room: "Hal; boven", kind: 'zeg "hoi"', state: "on" }],
    def,
    chosenColumns(def, undefined),
    "Id",
  );

  it("has the name, the chosen columns and the id", () => {
    expect(data.keys).toEqual(["name", "room", "kind", "id"]);
    expect(data.head).toEqual(["Naam", "Ruimte", "Soort", "Id"]);
  });

  it("is CSV that Excel opens: a BOM, the separator of the language, quotes, no formulas", () => {
    expect(separator("nl")).toBe(";");
    expect(separator("en-GB")).toBe(",");
    const nl = csv(data, "nl");
    expect(nl.startsWith("\ufeffNaam;Ruimte;Soort;Id\r\n")).toBe(true);
    expect(nl).toContain(`'=SOM(A1);"Hal; boven";"zeg ""hoi""";light.a`);
    expect(csv(data, "en")).toContain(`'=SOM(A1),Hal; boven,"zeg ""hoi""",light.a`);
  });

  it("is a Markdown table (no JSON: CSV and Markdown are enough, QA 3)", () => {
    const md = markdown(table([{ ...rows[0], name: "a | b" }], def, [], "Id"));
    expect(md).toBe("| Naam | Id |\n|---|---|\n| a \\| b | light.l0 |\n");
  });

  it("is named after the list and the day", () => {
    expect(fileName("logbook", "csv", new Date(2026, 9, 3))).toBe("housemind-logbook-2026-10-03.csv");
  });
});

describe("overview per kind (B-199)", () => {
  it("counts over the whole list, the biggest first, with what is not available", () => {
    const kinds = overview(rows, kindDef, { kind: ["switch"] }, (r) => r.state === "unavailable");
    expect(kinds.map((k) => [k.value, k.count, k.on])).toEqual([["light", 36, false], ["switch", 9, true]]);
    expect(kinds[0].share).toBeCloseTo(0.8);
    expect(kinds.reduce((sum, k) => sum + k.problem, 0)).toBe(5);
  });

  it("puts 'none' last", () => {
    const roomDef: FilterDef<Row> = { key: "room", title: "Ruimte", values: (r) => [r.room], label: (v) => v || "Geen ruimte" };
    expect(overview(rows, roomDef, {}).map((k) => k.label)).toEqual(["Keuken", "Geen ruimte"]);
  });
});

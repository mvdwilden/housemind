// Wat ik weet in Brein (E-524, E-525, B-448, B-449): what HouseMind knows of the house, per
// state, per room and folded per device, and the three buttons of each line.
import type { KnowledgeRow } from "./types";

export type KnowledgeState = KnowledgeRow["state"];
/** The tiles on top: the three states, and the scenes and scripts to read. */
export type KnowledgeTile = KnowledgeState | "scenes";
export const STATES: KnowledgeState[] = ["open", "yes", "no"];
export const TILES: KnowledgeTile[] = [...STATES, "scenes"];

/** How many lines there are in each state. */
export function counts(rows: KnowledgeRow[]): Record<KnowledgeState, number> {
  const out: Record<KnowledgeState, number> = { open: 0, yes: 0, no: 0 };
  for (const row of rows) out[row.state] += 1;
  return out;
}

/** The lines of one tile (none: all of them) that match the search, in name, room, device or
 * what HouseMind thinks. */
export function shown(rows: KnowledgeRow[], tile: KnowledgeState | null, query: string): KnowledgeRow[] {
  const q = query.trim().toLocaleLowerCase();
  return rows.filter(
    (r) =>
      (tile === null || r.state === tile) &&
      (!q || [r.name, r.room, r.device ?? "", r.what, r.label].join(" ").toLocaleLowerCase().includes(q)),
  );
}

/** What "Iets anders…" offers: another kind of room, another function, another sensor. */
export type Choice = "role" | "function" | "sensor" | null;
export function choiceOf(row: KnowledgeRow): Choice {
  if (row.kind === "area" && row.field === "role") return "role";
  if (row.kind === "entity" && row.field === "function") return "function";
  if (row.field === "temperature" && (row.options?.length ?? 0) > 1) return "sensor";
  return null;
}

/** The buttons of a line, per state (E-525): Klopt, Klopt niet, Iets anders… while it waits;
 * Iets anders…, Klopt toch niet and Terug naar te bevestigen when confirmed; Klopt toch wel,
 * Iets anders… and Terug naar te bevestigen after Klopt niet. A person's own choice cannot be
 * "toch niet": they choose again, or take it back. A room HouseMind does not know has only its
 * list (E-486). */
export type Act = "yes" | "no" | "other" | "back";
export function acts(row: KnowledgeRow): Act[] {
  const other: Act[] = choiceOf(row) ? ["other"] : [];
  if (row.state === "open") return row.unknown ? [] : ["yes", "no", ...other];
  if (row.state === "yes") return [...other, ...(row.source === "person" ? [] : (["no"] as Act[])), "back"];
  return ["yes", ...other, "back"];
}

/** The words on a button: the same act says "toch" once it was answered. */
export function actKey(act: Act, state: KnowledgeState): string {
  if (act === "yes") return state === "no" ? "ui.knowledge.act.yes_after_all" : "ui.knowledge.yes";
  if (act === "no") return state === "yes" ? "ui.knowledge.act.no_after_all" : "ui.knowledge.no";
  return act === "other" ? "ui.knowledge.act.other" : "ui.knowledge.act.back";
}

/** Some lines of one device together, folded: its name and what is in it in short. */
export interface DeviceFold {
  id: string; // "device:<id>"
  name: string;
  model: string; // its type (E-432)
  rows: KnowledgeRow[];
  summary: { what: string; count: number }[];
}
export type Entry = KnowledgeRow | DeviceFold;
export const isFold = (entry: Entry): entry is DeviceFold => "rows" in entry;

/** The lines of a device fold together where there are two or more of it; one stays a line. */
export function folded(rows: KnowledgeRow[]): Entry[] {
  const per = new Map<string, KnowledgeRow[]>();
  for (const row of rows) if (row.deviceId) per.set(row.deviceId, [...(per.get(row.deviceId) ?? []), row]);
  const out: Entry[] = [];
  const done = new Set<string>();
  for (const row of rows) {
    const mine = row.deviceId ? (per.get(row.deviceId) ?? []) : [];
    if (mine.length < 2) {
      out.push(row);
      continue;
    }
    if (done.has(row.deviceId ?? "")) continue;
    done.add(row.deviceId ?? "");
    const tally = new Map<string, number>();
    for (const r of mine) tally.set(r.what, (tally.get(r.what) ?? 0) + 1);
    out.push({
      id: `device:${row.deviceId}`,
      name: row.device ?? "",
      model: row.deviceModel ?? "",
      rows: mine,
      summary: [...tally].map(([what, count]) => ({ what, count })),
    });
  }
  return out;
}

/** Per room, in the order of their names, the lines without a room last; or all in one. */
export interface KnowledgeGroup {
  key: string; // the room's id, "" for none, "all" for one list
  name: string;
  count: number;
  entries: Entry[];
}
export function grouped(rows: KnowledgeRow[], byRoom: boolean, noRoom: string): KnowledgeGroup[] {
  const order = (a: KnowledgeRow, b: KnowledgeRow): number =>
    (a.device ?? a.name).localeCompare(b.device ?? b.name) || a.name.localeCompare(b.name);
  if (!byRoom) {
    const all = [...rows].sort(order);
    return all.length ? [{ key: "all", name: "", count: all.length, entries: folded(all) }] : [];
  }
  const rooms = new Map<string, KnowledgeRow[]>();
  for (const row of rows) rooms.set(row.roomId, [...(rooms.get(row.roomId) ?? []), row]);
  return [...rooms]
    .map(([key, items]) => ({ key, name: items[0]?.room || noRoom, count: items.length, entries: folded([...items].sort(order)) }))
    .sort((a, b) => Number(a.key === "") - Number(b.key === "") || a.name.localeCompare(b.name));
}

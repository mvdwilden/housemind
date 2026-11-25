// Long lists (E-325, B-196 – B-199): how many there are, pages, columns, export and an
// overview per kind, the same for every list with the filter bar. Showing and exporting only:
// nothing here changes the house.

import { valuesOf, type Chosen, type FilterDef } from "./filters";

export interface Column<I> {
  key: string; // English, also the key in JSON
  title: string;
  value: (item: I) => string; // the text on screen and in an export
  on?: boolean; // shown when nobody chose yet
  step?: (item: I) => string; // the value is a step: shown as its coloured label (StepPill)
}

export interface ListDef<I> {
  key: string; // devices, logbook, automations, scenes, readiness: where the choices are kept
  name: Column<I>; // always there, always first
  columns: Column<I>[];
  id: (item: I) => string; // in an export, so a row can be found again
  overview?: { filter: string; problem?: (item: I) => boolean }; // B-199
}

export const ROWS = [20, 50, 100, 0] as const; // 0: all
export const ALL_AT_ONCE = 500; // "Alle" in a big house: this many, then "Toon de rest" (E-322)

export interface Page<I> {
  items: I[];
  page: number; // 1-based, within 1 … pages
  pages: number;
  from: number; // 1-based; 0 when the list is empty
  to: number;
}

/** One page of a list. ``rows`` 0 is all of it – in a big house the first ALL_AT_ONCE until
 * ``more`` asks for the rest. A page beyond the end shows the last one. */
export function pageOf<I>(items: I[], page: number, rows: number, more = false): Page<I> {
  if (!items.length) return { items: [], page: 1, pages: 1, from: 0, to: 0 };
  if (rows <= 0) {
    const shown = more ? items : items.slice(0, ALL_AT_ONCE);
    return { items: shown, page: 1, pages: 1, from: 1, to: shown.length };
  }
  const pages = Math.ceil(items.length / rows);
  const at = Math.min(Math.max(1, page), pages);
  const start = (at - 1) * rows;
  const shown = items.slice(start, start + rows);
  return { items: shown, page: at, pages, from: start + 1, to: start + shown.length };
}

/** The columns a person sees: their own choice, else the ones that are on by default. */
export function chosenColumns<I>(def: ListDef<I>, keys: string[] | undefined): Column<I>[] {
  if (!keys) return def.columns.filter((column) => column.on);
  return def.columns.filter((column) => keys.includes(column.key));
}

export interface Table {
  keys: string[];
  head: string[];
  rows: string[][];
}

/** What an export holds: the name, the chosen columns and the id, for every row given. */
export function table<I>(items: I[], def: ListDef<I>, columns: Column<I>[], idTitle: string): Table {
  const all = [def.name, ...columns];
  return {
    keys: [...all.map((column) => column.key), "id"],
    head: [...all.map((column) => column.title), idTitle],
    rows: items.map((item) => [...all.map((column) => clean(column.value(item))), def.id(item)]),
  };
}

function clean(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** The separator Excel expects in a language: a semicolon where the comma is the decimal sign. */
export function separator(lang: string): string {
  return lang.split("-")[0] === "en" ? "," : ";";
}

/** CSV that Excel opens as it should (B-197): UTF-8 with a BOM, the separator of the language,
 * quotes where needed, and never a formula – a value starting with = + - or @ gets a '. */
export function csv(data: Table, lang: string): string {
  const sep = separator(lang);
  const cell = (value: string): string => {
    const safe = /^[=+\-@]/.test(value) ? `'${value}` : value;
    return /["\r\n]/.test(safe) || safe.includes(sep) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  const lines = [data.head, ...data.rows].map((row) => row.map(cell).join(sep));
  return `\ufeff${lines.join("\r\n")}\r\n`;
}

/** A Markdown table: readable as it is, and in any editor. */
export function markdown(data: Table): string {
  const cell = (value: string): string => value.replace(/\|/g, "\\|");
  const line = (row: string[]): string => `| ${row.map(cell).join(" | ")} |`;
  return [line(data.head), `|${data.head.map(() => "---").join("|")}|`, ...data.rows.map(line)].join("\n") + "\n";
}

export const KINDS = { csv: "text/csv;charset=utf-8", md: "text/markdown;charset=utf-8" } as const;
export type ExportKind = keyof typeof KINDS;

/** The name of the file: housemind-<list>-<date>.<ext>. */
export function fileName(list: string, kind: ExportKind, day: Date): string {
  const date = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
  return `housemind-${list}-${date}.${kind}`;
}

export interface Kind {
  value: string;
  label: string;
  count: number;
  share: number; // 0 … 1 of the whole list
  problem: number; // how many of them are not available
  on: boolean; // chosen in the filter
}

/** The overview per kind (B-199): over the whole list, not only what the filter leaves, so it
 * does not jump while choosing; the biggest first, "none" last. */
export function overview<I>(items: I[], def: FilterDef<I>, chosen: Chosen, problem?: (item: I) => boolean): Kind[] {
  const counts = new Map<string, { count: number; problem: number }>();
  for (const item of items) {
    for (const value of valuesOf(def, item)) {
      const now = counts.get(value) ?? { count: 0, problem: 0 };
      now.count += 1;
      if (problem?.(item)) now.problem += 1;
      counts.set(value, now);
    }
  }
  const wanted = chosen[def.key] ?? [];
  return [...counts.entries()]
    .map(([value, { count, problem: bad }]) => ({
      value,
      label: def.label(value),
      count,
      share: items.length ? count / items.length : 0,
      problem: bad,
      on: wanted.includes(value),
    }))
    .sort((a, b) => (a.value === "" ? 1 : 0) - (b.value === "" ? 1 : 0) || b.count - a.count || a.label.localeCompare(b.label));
}

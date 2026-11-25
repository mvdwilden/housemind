// Ordering and filtering (E-258, B-106, E-276): above a long list a search field and one
// Filter button; the panel has a section per filter with boxes to tick. Within one filter "or"
// (Woonkamer or Keuken), between filters "and" (Woonkamer and Verlichting). The number at a
// box counts what is left with the other filters; a box with 0 stays, greyed, so nothing
// jumps. What is chosen shows as small chips above the list. Showing only: nothing here
// changes the house.

export const PARTS = ["lighting", "covers", "climate", "air", "media", "cleaning", "garden", "water", "security", "messages", "other"] as const;
export const CAUSES = ["time", "sun", "presence", "people", "opening", "button", "other"] as const;
export const STEPS = ["autonomous", "propose", "observe", "measure", "left_alone"] as const;

export interface FilterDef<I> {
  key: string;
  title: string;
  values: (item: I) => string[]; // none: "" (for example no room)
  label: (value: string) => string;
  order?: readonly string[];
  hideNone?: boolean; // a single on/off button, such as "Start een scène of script"
  pill?: boolean; // the values are steps: shown as the coloured step label (StepPill)
  skipNone?: boolean; // no box for "none": a line without a room is not "Geen ruimte" (Logboek)
}

export type Chosen = Record<string, string[]>;

export interface Chip {
  value: string;
  label: string;
  count: number;
  on: boolean;
}

export function valuesOf<I>(def: FilterDef<I>, item: I): string[] {
  const found = def.values(item);
  return found.length ? found : [""];
}

/** Whether an item passes every filter (but one: its own buttons count without it). */
export function passes<I>(item: I, defs: FilterDef<I>[], chosen: Chosen, skip?: string): boolean {
  return defs.every((def) => {
    if (def.key === skip) return true;
    const wanted = chosen[def.key] ?? [];
    return !wanted.length || valuesOf(def, item).some((value) => wanted.includes(value));
  });
}

/** What the search field finds: every word somewhere in the item's text. */
export function found(text: string, search: string): boolean {
  const words = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const haystack = text.toLowerCase();
  return words.every((word) => haystack.includes(word));
}

export function filtered<I>(items: I[], defs: FilterDef<I>[], chosen: Chosen, search = "", text: (item: I) => string = () => ""): I[] {
  return items.filter((item) => passes(item, defs, chosen) && found(text(item), search));
}

/** The buttons of one filter: every value in the whole list, with what is left of it. */
export function chips<I>(items: I[], def: FilterDef<I>, defs: FilterDef<I>[], chosen: Chosen, keep: (item: I) => boolean = () => true): Chip[] {
  const all = new Set<string>();
  for (const item of items) for (const value of valuesOf(def, item)) all.add(value);
  if (def.hideNone || def.skipNone) all.delete("");
  const rest = items.filter((item) => keep(item) && passes(item, defs, chosen, def.key));
  const wanted = chosen[def.key] ?? [];
  const rank = (value: string): number => {
    if (value === "") return 1e6; // "Geen ruimte" last
    const at = def.order?.indexOf(value) ?? -1;
    return at >= 0 ? at : 1000;
  };
  return [...all]
    .map((value) => ({
      value,
      label: def.label(value),
      count: rest.filter((item) => valuesOf(def, item).includes(value)).length,
      on: wanted.includes(value),
    }))
    .sort((a, b) => rank(a.value) - rank(b.value) || a.label.localeCompare(b.label));
}

export function toggle(chosen: Chosen, key: string, value: string): Chosen {
  const now = chosen[key] ?? [];
  return { ...chosen, [key]: now.includes(value) ? now.filter((v) => v !== value) : [...now, value] };
}

export function active(chosen: Chosen): boolean {
  return Object.values(chosen).some((values) => values.length > 0);
}

/** Items in groups, in the given order of group keys, the rest by name, "" last. */
export function grouped<I>(items: I[], key: (item: I) => string, order: readonly string[] = []): [string, I[]][] {
  const groups = new Map<string, I[]>();
  for (const item of items) {
    const k = key(item);
    groups.set(k, [...(groups.get(k) ?? []), item]);
  }
  const rank = (k: string): number => (k === "" ? 1e6 : order.includes(k) ? order.indexOf(k) : 1000);
  return [...groups.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

/** How many values are chosen in all filters together (the number on the Filter button). */
export function howMany(chosen: Chosen): number {
  return Object.values(chosen).reduce((sum, values) => sum + values.length, 0);
}

/** What is chosen, as chips above the list: per filter in its own order, with its label. */
export function picked<I>(defs: FilterDef<I>[], chosen: Chosen): { key: string; value: string; label: string }[] {
  return defs.flatMap((def) => (chosen[def.key] ?? []).map((value) => ({ key: def.key, value, label: def.hideNone ? def.title : def.label(value) })));
}

/** The boxes of a long filter that match what is typed in its own search field. */
export function matching(list: Chip[], query: string): Chip[] {
  return list.filter((chip) => chip.on || found(chip.label, query));
}

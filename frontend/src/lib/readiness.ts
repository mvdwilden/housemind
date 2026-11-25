// "Klaar voor autonomie?" with one list under the overview (wish from the real house): every
// skill × room of all parts together, to search, filter (part, room, step, ready) and group
// (part, room, step), the same way as Apparaten en entiteiten. Before, each part had its own
// fold-out list "Per kamer", and a house with 35 rows in 14 rooms got very long.
import { CAUSES, type FilterDef } from "./filters";
import type { ReadyAutomation, ReadyPart, ReadySkill } from "./types";
import type { T } from "./ui";

export interface ReadyRow extends ReadySkill {
  part: string;
  partName: string;
}

export interface ReadyAutoRow extends ReadyAutomation {
  part: string;
  partName: string;
}

export const READY_STEPS = ["autonomous", "propose", "observe"] as const;

export function readyRows(parts: ReadyPart[]): ReadyRow[] {
  return parts.flatMap((p) => (p.skills ?? []).map((s) => ({ ...s, part: p.domain, partName: p.name })));
}

export function readyAutomations(parts: ReadyPart[]): ReadyAutoRow[] {
  return parts.flatMap((p) => (p.automations ?? []).map((a) => ({ ...a, part: p.domain, partName: p.name })));
}

/** Closest to the next step first: held back by a limit, then ready, then how far it is;
 * what already does it itself last. */
export function closestFirst(a: ReadySkill, b: ReadySkill): number {
  const rank = (s: ReadySkill): number => (s.heldBack ? 0 : s.ready ? 1 : s.step === "autonomous" ? 3 : 2);
  return rank(a) - rank(b) || b.progress - a.progress || a.what.localeCompare(b.what);
}

/** The filters of the list: part, room, step and "ready for the next step". */
export function rowFilters(parts: ReadyPart[], t: T): FilterDef<ReadyRow>[] {
  return [
    { key: "part", title: t("ui.filter.title.part"), values: (r) => [r.part], label: (v) => parts.find((p) => p.domain === v)?.name ?? v, order: parts.map((p) => p.domain) },
    { key: "room", title: t("ui.filter.title.room"), values: (r) => [r.room], label: (v) => v || t("ui.filter.no_room") },
    { key: "step", title: t("ui.filter.title.step"), values: (r) => [r.step], label: (v) => t(`steps.${v}`), order: READY_STEPS, pill: true },
    // Held back by a limit counts too: it is ready, only not allowed yet.
    { key: "ready", title: t("ui.ready.filter_ready"), values: (r) => (r.ready || r.heldBack ? ["yes"] : []), label: () => t("ui.ready.filter_ready"), hideNone: true },
    // Waits for something: an automation steers it, a question for you, or a limit (E-501).
    { key: "waits", title: t("ui.ready.filter_waits"), values: (r) => (waits(r) ? ["yes"] : []), label: () => t("ui.ready.filter_waits"), hideNone: true },
  ];
}

/** A row waits for something outside HouseMind's own learning (E-501). */
export function waits(r: ReadySkill): boolean {
  return Boolean(r.heldBack || r.question || r.steer);
}

/** What a row waits for or is, in one short flag; "" for nothing to say (E-501, B-421). */
export function flagOf(r: ReadySkill): "" | "held" | "question" | "steer" | "ready" | "unseen" {
  if (r.heldBack) return "held";
  if (r.question && !r.question.declined) return "question";
  if (r.steer) return "steer";
  if (r.ready) return "ready";
  if (r.unseen) return "unseen"; // a new device's rule (E-485): in sight in the list too
  return "";
}

/** The ring of a row: how often it went well against its target; on Autonomous, where the bar
 * counts steps back, how often it went well without a target (B-421). */
export function ringOf(r: ReadySkill): { value: number | null; target: number | null; history: number; yes: boolean } {
  if (r.bar.against) return { value: r.good ?? null, target: null, history: 0, yes: false };
  const yes = r.step === "propose" && r.counters[0]?.key === "answers";
  return { value: r.bar.value, target: r.bar.target, history: r.bar.history ?? 0, yes };
}

/** The line of details on Expert as terms and values (B-423): "lampen: Oprit · bron: …". */
export function detailFacts(line: string | undefined): [string, string][] {
  if (!line) return [];
  return line.split(" · ").map((part) => {
    const at = part.indexOf(": ");
    return at > 0 ? [part.slice(0, at), part.slice(at + 2)] : ["", part];
  });
}

export function automationFilters(parts: ReadyPart[], t: T): FilterDef<ReadyAutoRow>[] {
  return [
    // "other": the automations no part lists, in the same list (E-523).
    { key: "part", title: t("ui.filter.title.part"), values: (a) => [a.part], label: (v) => parts.find((p) => p.domain === v)?.name ?? t(`ui.filter.part.${v}`), order: [...parts.map((p) => p.domain), "other"] },
    { key: "room", title: t("ui.filter.title.room"), values: (a) => a.rooms ?? [], label: (v) => v || t("ui.filter.no_room") },
    // As they were at Huisinstellingen (E-258, E-345): what starts it, its category and labels.
    { key: "cause", title: t("ui.filter.title.cause"), values: (a) => (a.causes?.length ? a.causes : ["other"]), label: (v) => t(`ui.filter.cause.${v}`), order: CAUSES },
    { key: "category", title: t("ui.filter.title.category"), values: (a) => a.categories ?? [], label: (v) => v, skipNone: true },
    { key: "label", title: t("ui.filter.title.label"), values: (a) => a.labels ?? [], label: (v) => v, skipNone: true },
  ];
}

/** Every automation once, per part, and the ones no part lists under Overig (E-523). */
export function allAutomations(parts: ReadyPart[], others: ReadyAutomation[], other: string): ReadyAutoRow[] {
  return [...readyAutomations(parts), ...others.map((a) => ({ ...a, part: "other", partName: other }))];
}

/** One part chosen from the overview, or none again when it was the only one chosen. */
export function onlyPart(chosen: Record<string, string[]>, part: string): Record<string, string[]> {
  const now = chosen.part ?? [];
  return { ...chosen, part: now.length === 1 && now[0] === part ? [] : [part] };
}

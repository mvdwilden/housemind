// The logbook (E-263, B-117): one timeline, the newest on top – what HouseMind did or left,
// the notifications, your conversations with Vesta, the morning briefing and the house report.
// The server sends the lines of the chosen period; here they are searched, filtered and grouped
// the way Brein does (E-323) and put under their day, or – without groups – in pages, with the
// details a line shows as columns and an export (E-325). Also what Huis shows at most: three
// cards that ask for attention (E-261).

import { PARTS, type FilterDef } from "./filters";
import type { Column, ListDef } from "./lists";
import type { MindItem, Notice, ProposalView, Why } from "./types";
import type { T } from "./ui";

interface When {
  when: string; // ISO, for the order
  day: string; // the house's own day, yyyy-mm-dd
  at: string; // hh:mm in the house
}
export interface LogDone extends Omit<MindItem, "at">, When {
  answered?: string; // the answer to the question in this line: "Jij zei ja om 09:14" (QA 3)
  type: "done";
  id: number | null; // known while the brain remembers the decision: then Why? and Undo work
  undo: boolean;
  part?: string; // lighting, covers …, for Filter (E-323)
}
export interface LogNotice extends Omit<Notice, "at">, When {
  type: "notice";
  open: boolean;
}
export interface LogTalk extends When {
  type: "talk";
  id: number;
  question: string;
  answer: string;
}
export interface LogPart extends When {
  type: "part"; // a part turned off or on again, and by whom (E-400)
  id: number;
  part: string;
  off: boolean;
  text: string;
}
/** The alarm (E-533, E-534): switched on, gone off with what HouseMind did, switched off,
 * and the house put back – one line each, never a line per lamp. */
export interface LogAlarm extends When {
  type: "alarm";
  id: number;
  state: "armed" | "triggered" | "disarmed" | "back";
  text: string;
}
export interface LogBriefing extends When {
  type: "briefing";
  text: string;
}
export interface LogReport extends When {
  type: "report";
  week: string;
  lines: string[];
  ai?: boolean; // in the AI's words; the numbers are HouseMind's own (E-418)
}
/** A question to an AI (E-348), only for an administrator: what was sent and what came back. */
export interface LogAi extends When {
  type: "ai";
  id: number;
  purpose: string; // vesta, ask, rule, general, roles, switches, automations, doubts, forward
  model: string;
  instruction: string; // why HouseMind asked and what answer it wanted; empty from before
  sent: string;
  received: string; // the answer, or the error when there was none
  ok: boolean;
  cost: number; // in euro
}
export type LogItem = LogDone | LogNotice | LogTalk | LogBriefing | LogReport | LogAi | LogPart | LogAlarm;
export interface Logbook {
  today: string;
  items: LogItem[];
  period?: string; // the period these lines are of (E-323)
  oldest?: string; // the oldest day the house still keeps, for Kies een dag
}

export type LogKind = "done" | "watched" | "notices" | "talks" | "ai";
export const KINDS: LogKind[] = ["done", "watched", "notices", "talks", "ai"];
export const PERIODS = ["recent", "today", "yesterday", "week", "month", "day"] as const;
export const VIEWS = ["none", "day", "room"] as const; // none: one list in pages (E-325); no per kind: Filter › Soort does that (QA 3)

/** Gedaan · Alleen bekeken · Meldingen · Gesprekken. What HouseMind only watched stays apart
 * from what it did (QA 2, B-180); the briefing and the house report are messages from the
 * house, so they go with the notifications. */
export function kindOf(item: LogItem): LogKind {
  if (item.type === "done") return item.outcome === "observed" ? "watched" : "done";
  if (item.type === "talk") return "talks";
  if (item.type === "ai") return "ai";
  return "notices";
}

/** What the search field looks in: every word a line shows. */
export function logText(item: LogItem): string {
  if (item.type === "done") return [item.text, item.area, item.thought, item.did, ...(item.saw ?? [])].join(" ");
  if (item.type === "talk") return `${item.question} ${item.answer}`;
  if (item.type === "report") return item.lines.join(" ");
  if (item.type === "ai") return `AI ${item.purpose} ${item.model} ${item.instruction} ${item.sent} ${item.received}`;
  return item.text;
}

/** The filters of Logboek, the same panel as in Brein: Soort, Ruimte, Onderdeel and – from
 * level 3, where a line shows it – Uitkomst. A notification has no room or part: it is in
 * no box there, never in "Geen ruimte". */
export function logFilters(items: LogItem[], level: number, t: T): FilterDef<LogItem>[] {
  const outcomes = new Map<string, string>();
  for (const item of items) if (item.type === "done") outcomes.set(item.outcome, item.outcomeName);
  const defs: FilterDef<LogItem>[] = [
    { key: "kind", title: t("ui.filter.title.kind"), values: (i) => [kindOf(i)], label: (v) => t(`ui.logbook.${v}`), order: KINDS },
    { key: "room", title: t("ui.filter.title.room"), values: (i) => (i.type === "done" && i.area ? [i.area] : []), label: (v) => v, skipNone: true },
    { key: "part", title: t("ui.filter.title.part"), values: (i) => (i.type === "done" && i.part ? [i.part] : []), label: (v) => t(`ui.filter.part.${v}`), order: PARTS, skipNone: true },
  ];
  if (level >= 3) {
    defs.push({ key: "outcome", title: t("ui.logbook.outcome"), values: (i) => (i.type === "done" ? [i.outcome] : []), label: (v) => outcomes.get(v) ?? v, skipNone: true });
  }
  return defs;
}

/** What a line says, in one cell of an export (E-325): a conversation as question – answer,
 * the house report line after line. */
export function lineText(item: LogItem): string {
  if (item.type === "talk") return `${item.question} – ${item.answer}`;
  if (item.type === "report") return item.lines.join(" · ");
  if (item.type === "ai") return `AI (${item.model}): ${item.sent} – ${item.received}`;
  return item.text;
}

/** How a line is found again in an export: its kind and number, or – without a number, such
 * as the briefing – its kind and the moment it was written. */
export function logId(item: LogItem): string {
  return "id" in item && item.id !== null ? `${item.type}-${item.id}` : `${item.type}-${item.when}`;
}

/** One key per line of a list, never twice: lines without a number can share their moment –
 * several things only watched in the same second – and one key twice stops the whole list. */
export function lineKeys(list: LogItem[]): string[] {
  const seen = new Map<string, number>();
  return list.map((item) => {
    const base = `${item.type}-${"id" in item && item.id !== null ? item.id : ""}-${item.when}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return n ? `${base}~${n}` : base;
  });
}

/** Logboek as a long list (E-325): the text first, then what a line shows – day, time, kind,
 * room, part and, from level 3 where a line shows it, the outcome. A notification or a
 * conversation has no room, part or outcome: there the cell stays empty. */
export function logList(level: number, t: T): ListDef<LogItem> {
  const columns: Column<LogItem>[] = [
    { key: "day", title: t("ui.logbook.group.day"), value: (i) => i.day, on: true },
    { key: "time", title: t("ui.logbook.time"), value: (i) => i.at, on: true },
    { key: "kind", title: t("ui.filter.title.kind"), value: (i) => t(`ui.logbook.${kindOf(i)}`) },
    { key: "room", title: t("ui.filter.title.room"), value: (i) => (i.type === "done" ? i.area : ""), on: true },
    { key: "part", title: t("ui.filter.title.part"), value: (i) => (i.type === "done" && i.part ? t(`ui.filter.part.${i.part}`) : "") },
  ];
  if (level >= 3) columns.push({ key: "outcome", title: t("ui.logbook.outcome"), value: (i) => (i.type === "done" ? i.outcomeName : ""), on: true });
  return { key: "logbook", name: { key: "text", title: t("ui.logbook.text"), value: lineText }, columns, id: logId, overview: { filter: "kind" } };
}

/** Groeperen: per day (as always), per room, or per kind; the newest first in each. */
export function groupKey(view: string, item: LogItem): string {
  if (view === "room") return item.type === "done" ? item.area : "";
  return item.day;
}

/** The lines under their day, in the order they came (the newest first). */
export function byDay(items: LogItem[]): { day: string; items: LogItem[] }[] {
  const days: { day: string; items: LogItem[] }[] = [];
  for (const item of items) {
    const last = days.at(-1);
    if (last?.day === item.day) last.items.push(item);
    else days.push({ day: item.day, items: [item] });
  }
  return days;
}

/** "today", "yesterday" or the date in words, in the person's language. */
export function dayName(day: string, today: string, lang: string): "today" | "yesterday" | string {
  if (day === today) return "today";
  const date = new Date(`${day}T12:00:00`);
  const before = new Date(`${today}T12:00:00`);
  before.setDate(before.getDate() - 1);
  if (date.toDateString() === before.toDateString()) return "yesterday";
  return date.toLocaleDateString(lang, { weekday: "long", day: "numeric", month: "long" });
}

export type Attention = { kind: "proposal"; proposal: ProposalView } | { kind: "notice"; notice: Notice };

/** What Huis shows at most (E-261): three cards that ask for attention – an emergency first,
 * then a proposal, a question, an important notification. The rest is in Logboek. */
/** How many more wait for you besides the reason under Vesta's word (E-389, B-297): what Huis
 * shows as cards, without that one and without what you put on Later or Niet meer tonen. */
export function othersWaiting(proposals: ProposalView[] = [], notices: Notice[] = [], why?: Why | null): number {
  const all = attention(proposals, notices.filter((n) => !n.hidden), Infinity).cards;
  return all.filter((card) =>
    card.kind === "proposal"
      ? card.proposal.id !== why?.proposal
      : card.notice.id !== why?.notification && (why?.proposal == null || card.notice.proposal !== why.proposal),
  ).length;
}

export function attention(proposals: ProposalView[] = [], notices: Notice[] = [], max = 3): { cards: Attention[]; more: number } {
  const shown = new Set(proposals.map((p) => p.id));
  const asks = (n: Notice) => (n.proposal !== null && !shown.has(n.proposal)) || n.actions.includes("yes");
  const all: Attention[] = [
    ...notices.filter((n) => n.kind === "emergency").map((notice) => ({ kind: "notice" as const, notice })),
    ...proposals.map((proposal) => ({ kind: "proposal" as const, proposal })),
    ...notices.filter((n) => n.kind !== "emergency" && asks(n)).map((notice) => ({ kind: "notice" as const, notice })),
    ...notices.filter((n) => n.kind === "important" && !asks(n)).map((notice) => ({ kind: "notice" as const, notice })),
  ];
  return { cards: all.slice(0, max), more: Math.max(0, all.length - max) };
}

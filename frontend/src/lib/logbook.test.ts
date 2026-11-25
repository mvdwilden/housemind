import { describe, expect, it } from "vitest";
import { filtered, grouped } from "./filters";
import { chosenColumns, table } from "./lists";
import { attention, byDay, othersWaiting, dayName, groupKey, KINDS, kindOf, lineKeys, lineText, logFilters, logId, logList, logText, VIEWS, type LogItem } from "./logbook";
import type { Notice, ProposalView } from "./types";

const when = (day: string, at: string) => ({ when: `${day}T${at}:00+02:00`, day, at });
const items: LogItem[] = [
  { type: "talk", id: 3, question: "hoe gaat het?", answer: "Goed.", ...when("2026-10-19", "09:10") },
  { type: "notice", id: 7, kind: "important", text: "De deur staat open.", actions: ["snooze", "dismiss"], proposal: null, open: true, ...when("2026-10-19", "09:05") },
  { type: "done", id: 12, undo: true, area: "Keuken", outcome: "executed", outcomeName: "gedaan", text: "Licht aan.", ...when("2026-10-19", "09:00") },
  { type: "briefing", text: "Goedemorgen.", ...when("2026-10-19", "07:00") },
  { type: "report", week: "2026-W42", lines: ["Een rustige week."], ...when("2026-10-18", "20:00") },
];

describe("the logbook (E-263)", () => {
  it("gives every line its own key, also lines only watched in the same second (no number)", () => {
    const watched = (text: string) => ({ ...items[2], id: null, outcome: "observed", text }) as unknown as LogItem;
    const list = [watched("Gordijnen dicht."), watched("Tuinlamp aan."), items[2], watched("Gordijnen dicht.")];
    const keys = lineKeys(list);
    expect(new Set(keys).size).toBe(list.length);
    // Numbered lines keep a key of their own number, so a line stays itself when others come.
    expect(keys[2]).toBe(lineKeys([items[2]])[0]);
  });
  it("sorts each line into a kind: done, only watched, notifications (with briefing and report), talks", () => {
    expect(items.map(kindOf)).toEqual(["talks", "notices", "done", "notices", "notices"]);
    // What HouseMind only watched stays apart from what it did (QA 2, B-180).
    expect(kindOf({ ...items[2], outcome: "observed" } as LogItem)).toBe("watched");
  });

  it("filters like Brein: per kind, per room and per part; a notification is in no room box (E-323)", () => {
    const t = (key: string) => key;
    const lines: LogItem[] = [...items, { type: "done", id: 13, undo: false, area: "Badkamer", outcome: "proposed", outcomeName: "voorgesteld", text: "Gordijn dicht?", part: "covers", ...when("2026-10-18", "21:00") }];
    lines[2] = { ...(lines[2] as Extract<LogItem, { type: "done" }>), part: "lighting" };
    const defs = logFilters(lines, 3, t);
    expect(defs.map((d) => d.key)).toEqual(["kind", "room", "part", "outcome"]);
    expect(logFilters(lines, 2, t).map((d) => d.key)).toEqual(["kind", "room", "part"]);
    expect(filtered(lines, defs, { kind: ["notices"] }).map((i) => i.type)).toEqual(["notice", "briefing", "report"]);
    expect(filtered(lines, defs, { room: ["Keuken"] }).map((i) => i.type)).toEqual(["done"]);
    expect(filtered(lines, defs, { part: ["covers"] }).map((i) => ("text" in i ? i.text : ""))).toEqual(["Gordijn dicht?"]);
    expect(filtered(lines, defs, { outcome: ["proposed"] })).toHaveLength(1);
    // The search looks in what a line shows, also a conversation.
    expect(filtered(lines, defs, {}, "gaat het", logText).map((i) => i.type)).toEqual(["talk"]);
  });

  it("groups per day or per room (no room last), the newest first in each; per kind is Filter › Soort (QA 3)", () => {
    expect(grouped(items, (i) => groupKey("room", i)).map(([k, list]) => [k, list.length])).toEqual([["Keuken", 1], ["", 4]]);
    expect(VIEWS as readonly string[]).not.toContain("kind");
    expect(items.map((i) => groupKey("day", i))).toEqual(["2026-10-19", "2026-10-19", "2026-10-19", "2026-10-19", "2026-10-18"]);
  });

  it("puts the lines under their day, the newest first", () => {
    const days = byDay(items);
    expect(days.map((d) => d.day)).toEqual(["2026-10-19", "2026-10-18"]);
    expect(days[0].items).toHaveLength(4);
  });

  it("is a long list too (E-325): no groups as a choice, the text first, the details as columns", () => {
    const t = (key: string) => key;
    expect(VIEWS[0]).toBe("none");
    const two = logList(2, t);
    expect(two.key).toBe("logbook");
    expect(two.columns.map((c) => c.key)).toEqual(["day", "time", "kind", "room", "part"]);
    // The outcome only from level 3, where a line shows it; on by default like day, time and room.
    const three = logList(3, t);
    expect(chosenColumns(three, undefined).map((c) => c.key)).toEqual(["day", "time", "room", "outcome"]);
    // A conversation in one cell, a line without a number found again by its moment.
    expect(items.map(lineText)).toEqual(["hoe gaat het? – Goed.", "De deur staat open.", "Licht aan.", "Goedemorgen.", "Een rustige week."]);
    expect(items.map(logId)).toEqual(["talk-3", "notice-7", "done-12", "briefing-2026-10-19T07:00:00+02:00", "report-2026-10-18T20:00:00+02:00"]);
    // An export: what a notification lacks stays empty.
    const data = table(items.slice(1, 3), three, chosenColumns(three, undefined), "Id");
    expect(data.keys).toEqual(["text", "day", "time", "room", "outcome", "id"]);
    expect(data.rows).toEqual([
      ["De deur staat open.", "2026-10-19", "09:05", "", "", "notice-7"],
      ["Licht aan.", "2026-10-19", "09:00", "Keuken", "gedaan", "done-12"],
    ]);
  });

  it("an AI question is its own kind, found by Soort › AI and by what was sent or came back (E-348)", () => {
    const ai: LogItem = {
      type: "ai", id: 5, purpose: "vesta", model: "claude-haiku-5-5", instruction: "You are Vesta, the voice of a home.",
      sent: '<data>{"lamp": "lamp 3"}</data>',
      received: '{"text": "Ik deed lamp 3 aan."}', ok: true, cost: 0.0003, ...when("2026-10-19", "08:00"),
    };
    const lines = [...items, ai];
    expect(kindOf(ai)).toBe("ai");
    expect(KINDS.at(-1)).toBe("ai");
    const defs = logFilters(lines, 2, (key: string) => key);
    expect(filtered(lines, defs, { kind: ["ai"] })).toEqual([ai]);
    expect(filtered(lines, defs, {}, "lamp 3", logText)).toEqual([ai]);
    expect(filtered(lines, defs, {}, "voice of a home", logText)).toEqual([ai]); // the instruction
    expect(filtered(lines, defs, { room: ["Keuken"] })).not.toContain(ai); // no room
    expect(lineText(ai)).toContain("claude-haiku-5-5");
    expect(logId(ai)).toBe("ai-5");
  });

  it("names today and yesterday, other days in words", () => {
    expect(dayName("2026-10-19", "2026-10-19", "nl")).toBe("today");
    expect(dayName("2026-10-18", "2026-10-19", "nl")).toBe("yesterday");
    expect(dayName("2026-10-01", "2026-10-19", "nl")).toContain("oktober");
  });
});

const notice = (id: number, kind: Notice["kind"], extra: Partial<Notice> = {}): Notice =>
  ({ id, kind, text: `melding ${id}`, at: "09:00", actions: ["snooze", "dismiss"], proposal: null, ...extra });
const proposal = (id: number): ProposalView =>
  ({ id, question: `voorstel ${id}`, reason: "", domain: "Licht", area: "Keuken", canRule: true, areaId: "kitchen", entity: "light.kitchen", at: "09:00" });

describe("Huis asks for attention with three cards at most (E-261)", () => {
  it("an emergency first, then a proposal, a question, an important notification", () => {
    const { cards, more } = attention([proposal(1)], [notice(1, "important"), notice(2, "emergency", { actions: [] }), notice(3, "info", { actions: ["yes", "no"] })]);
    expect(cards.map((c) => (c.kind === "proposal" ? `p${c.proposal.id}` : `n${c.notice.id}`))).toEqual(["n2", "p1", "n3"]);
    expect(more).toBe(1); // the important one waits in Logboek
  });

  it("a proposal once, also when it is a notification too; info and learning stay in Logboek", () => {
    const { cards, more } = attention([proposal(4)], [notice(5, "learning", { proposal: 4, actions: ["do", "not_now"] }), notice(6, "info")]);
    expect(cards).toHaveLength(1);
    expect(more).toBe(0);
    // Level 2 has no proposal cards: the proposal comes as a notification with its answers.
    expect(attention([], [notice(5, "learning", { proposal: 4, actions: ["do", "not_now"] })]).cards).toHaveLength(1);
  });
});

describe("why Vesta has her colour, and how many more wait (E-389)", () => {
  it("counts what Huis shows, without the reason in the line itself", () => {
    const notices = [notice(1, "important"), notice(2, "important"), notice(3, "info")];
    expect(othersWaiting([], notices, { text: "melding 1", notification: 1, proposal: null })).toBe(1);
    expect(othersWaiting([], notices, null)).toBe(2); // "+2" opens Logboek from the top
  });

  it("a question is not counted twice, and what you put off does not count", () => {
    const notices = [notice(5, "learning", { proposal: 4, actions: ["do", "not_now"] }), notice(6, "important", { hidden: "later" })];
    expect(othersWaiting([proposal(4)], notices, { text: "Zal ik …?", notification: 5, proposal: 4 })).toBe(0);
  });
});

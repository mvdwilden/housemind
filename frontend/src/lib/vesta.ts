// The Vesta window (E-236, E-241, B-85): one conversation, the same on every level – after a
// tap on her, the microphone button or a typed question. Pure steps, so they can be tested.

import type { Browser, MicProblem } from "./michelp";

export type VestaPhase = "thinking" | "speaking" | "listening" | "hearing" | "idle";
export interface VestaTurn {
  who: "vesta" | "you";
  text: string;
  more?: VestaMore; // Lees meer: the part of the manual or the Help question (E-246)
  ai?: boolean; // the answer came via the AI (E-247)
  partial?: boolean; // an AI's answer as it is written; her answer replaces it (E-159)
}

/** Where Lees meer goes: a part of the manual in the app, or a Help question. */
export interface VestaMore {
  kind: "manual" | "help";
  id: string;
  title: string;
}
export interface VestaWindow {
  open: boolean;
  phase: VestaPhase;
  turns: VestaTurn[];
  level: number; // how loud you speak now (0–1), for the meter
  listenStart: number; // when the microphone opened (ms)
  noMic: boolean; // the microphone did not work on this device
  micHelp: { problem: MicProblem; browser: Browser } | null; // why not, and how to fix it
  confirm: string | null; // she asks yes or no (a house rule): the buttons are in the window
  typed: boolean; // the conversation is typed: there is more time to read and type
  ask: VestaAsk | null; // a question that waits for an answer, with its own buttons (E-252)
  offer: VestaOffer | null; // "Zal ik het voor je openen?": a part of a screen (E-244)
  choices: VestaChoice[] | null; // her choices after a tap without an AI, or the AI's own (E-349)
}

/** One of her choices: after a tap without an AI ("tap"), or the AI asking back ("ai"). */
export interface VestaChoice {
  id: string;
  text: string;
  kind: "tap" | "ai";
}

/** Where a setting is: Mijn voorkeuren or Huisinstellingen, and the part of that screen. */
export interface VestaOffer {
  sheet: string;
  section: string;
}

/** A question Vesta asks: a proposal, a task, or (administrators) an automation – by herself, or
 * after a tap (E-252, E-349). A proposal also has Waarom? ("why"). */
export interface VestaAsk {
  key: string;
  kind:
    | "proposal"
    | "automation"
    | "restore"
    | "vacation"
    | "task"
    | "knowledge"
    | "unusual" // something unusual: Ja, zet uit / Nee, laat aan (E-416)
    | "rule_suggest"; // a house rule the AI suggests: Ja / Nee (E-417)
  text: string;
  actions: string[];
  proposal: number | null;
  notification: number;
  follow: boolean; // the next one after an answer: it may come in the open window (E-464)
}

export const CLOSED: VestaWindow = {
  open: false, phase: "idle", turns: [], level: 0, listenStart: 0, noMic: false, micHelp: null, confirm: null, typed: false, ask: null, offer: null, choices: null,
};
export const CLOSE_MS = 15000; // the window closes this long after her last word (E-464)
export const TYPED_CLOSE_MS = 60000; // after a typed answer: time to read and type again

/** You typed to her – in the talk bar or in her window: it opens (or continues) the window. */
export function typedTo(window: VestaWindow, text: string): VestaWindow {
  const turns = window.open ? [...window.turns, { who: "you" as const, text }] : [{ who: "you" as const, text }];
  return window.open
    ? { ...window, phase: "thinking", turns, confirm: null, typed: true }
    : { ...CLOSED, open: true, phase: "thinking", turns, typed: true };
}

/** Her answer in the open window: what you said (when spoken), then her words; a yes or no
 * question keeps its token so the buttons are in the window, not behind it. Her open question
 * and her choices come with every answer while they are open (E-349): an answer without them
 * means they are over. */
export function saidTo(
  window: VestaWindow, text: string, heard?: string, confirm?: string, offer?: VestaOffer | null,
  more?: VestaMore, ai?: boolean, ask?: VestaAsk | null, choices?: VestaChoice[] | null,
): VestaWindow {
  const turns = window.turns.filter((turn) => !turn.partial); // her answer replaces it (E-159)
  if (heard) turns.push({ who: "you", text: heard });
  turns.push({ who: "vesta", text, ...(more ? { more } : {}), ...(ai ? { ai } : {}) });
  return {
    ...window, turns, phase: "idle", confirm: confirm ?? null, offer: offer ?? null, ask: ask ?? null,
    choices: choices && choices.length ? choices : null,
  };
}

/** An AI's answer as it is written (E-159): one turn of hers that grows while she still
 * thinks; her answer replaces it (saidTo). Only in an open window. */
export function partialTo(window: VestaWindow, text: string): VestaWindow {
  if (!window.open) return window;
  const turns = window.turns.filter((turn) => !turn.partial);
  return { ...window, turns: [...turns, { who: "vesta", text, partial: true }] };
}

/** How long the window stays after her last word; null: until it is closed. */
export function closeDelay(window: VestaWindow, typedMs = TYPED_CLOSE_MS, answerMs = CLOSE_MS): number | null {
  if (window.micHelp || window.confirm || window.ask || window.offer) return null; // to read or answer
  return window.typed || window.choices ? typedMs : answerMs; // time to choose (E-349)
}

/** Whether Vesta may ask now (E-252): the app in view, no other screen open, not typing, and
 * her window closed – never in the middle of something, never over a question or an answer you
 * are reading. Only her next question after your answer comes in the open window (E-464). */
export function mayAsk(state: { visible: boolean; busy: boolean; typing: boolean; window: VestaWindow; follow?: boolean }): boolean {
  const w = state.window;
  const open = state.follow === true && w.phase === "idle" && !w.confirm && !w.ask && !w.offer && !w.choices && !w.micHelp;
  return state.visible && !state.busy && !state.typing && (!w.open || open);
}

/** Her question in the window: a new window, or the next turn of the conversation. */
export function askedTo(window: VestaWindow, ask: VestaAsk): VestaWindow {
  const turn = { who: "vesta" as const, text: ask.text };
  return window.open
    ? { ...window, phase: "idle", turns: [...window.turns, turn], ask, choices: null }
    : { ...CLOSED, open: true, phase: "idle", turns: [turn], ask };
}


/** What Vesta does now, in words next to her name (E-269): ``ui.vesta.now.<word>``. */
export const DOING = [
  "calm", "thinking", "learning", "attention", "paused", "sleeping", "secured", "emergency", "listening", "speaking", "inactive",
] as const;
export type Doing = (typeof DOING)[number];

/** The word follows what the Orb in her window shows (``shown``, the same colour and shape),
 * and also says when she listens or speaks. An emergency always comes first. */
export function doing(shown: string, phase: VestaPhase, speaking: boolean): Doing {
  if (shown === "emergency") return "emergency";
  if (phase === "listening") return "listening";
  if (speaking || phase === "speaking") return "speaking";
  return (DOING as readonly string[]).includes(shown) ? (shown as Doing) : "calm";
}


// The tour (E-249, B-98): like the first version – one part of the screen lit up, a small card
// with Vesta's explanation, Skip / Back / Next. The steps are data: what they point at
// (data-tour="…"), from which level, and whether only for administrators. Each person sees it
// once per level; a higher level shows only what is new; Help starts it again in full.

export interface TourStep {
  key: string; // the texts: ui.tour.<key>.title / .text
  target?: string; // data-tour="…" on the screen; none: the card in the middle
  level: number; // from this level on
  only?: number; // only on this level (the buttons that level 1 has)
  admin?: boolean;
}

// In order of what matters most: when there are too many, the last ones wait for Help.
export const STEPS: TourStep[] = [
  { key: "welcome", level: 1 },
  { key: "vesta", target: "vesta", level: 1 },
  { key: "ask", target: "ask", level: 1, only: 1 },
  { key: "pause", target: "pause", level: 1, only: 1 },
  { key: "talk", target: "talk", level: 2 }, // the button Vraag Vesta (E-264)
  { key: "tabs", target: "tabs", level: 2 }, // with Logboek (E-263)
  { key: "mind", target: "mind", level: 2 },
  { key: "rooms", target: "rooms", level: 2 },
  { key: "prefs", target: "prefs", level: 1 },
  { key: "help", target: "help", level: 1 }, // in the header on every screen (E-260)
  { key: "brain", target: "tab-brain", level: 3 }, // with the house rules (redesign step 3)
  { key: "brain_expert", target: "tab-brain", level: 4 }, // the trace and the devices
  { key: "settings", target: "settings", level: 1, admin: true },
  { key: "done", level: 1 },
];
export const MAX_STEPS = 8; // at most this many by itself (18.11)

/** The steps for a person: the first time all of their level, after a higher level only the
 * new ones (with "Wil je zien wat er nu bij is?" first). ``seen`` are the levels they toured. */
export function tourFor(level: number, admin: boolean, seen: number[], all = false): TourStep[] {
  const before = all ? 0 : Math.max(0, ...seen.filter((l) => l < level));
  const fits = (s: TourStep) =>
    (s.only === undefined || s.only === level) && (!s.admin || admin) && s.level <= level;
  const middle = STEPS.filter(
    (s) => s.key !== "welcome" && s.key !== "done" && fits(s) && (before === 0 || s.level > before),
  );
  if (!middle.length) return [];
  const start: TourStep = before === 0 ? STEPS[0] : { key: "more", level };
  const end = STEPS[STEPS.length - 1];
  const room = all ? middle.length : MAX_STEPS - 2;
  return [start, ...middle.slice(0, room), end];
}

/** Whether the tour starts by itself: not done for this level, not on a wall tablet, after the
 * introduction, and only when nothing else is open. */
export function tourDue(state: {
  level: number;
  seen: number[];
  tablet: boolean;
  introducing: boolean;
  busy: boolean;
}): boolean {
  return !state.tablet && !state.introducing && !state.busy && !state.seen.includes(state.level);
}

/** The levels to remember after the tour: this one and every one below it. */
export function toured(level: number, seen: number[]): number[] {
  const all = new Set(seen);
  for (let l = 1; l <= level; l++) all.add(l);
  return [...all].sort();
}

/** Where the card goes: under the lit part when it fits, else above it, inside the screen. */
export function placeTip(
  target: { top: number; bottom: number; left: number } | null,
  screen: { width: number; height: number },
  tip: { width: number; height: number },
): { top: number; left: number } {
  const margin = 12;
  if (!target) return { top: Math.max(margin, (screen.height - tip.height) / 2), left: (screen.width - tip.width) / 2 };
  const below = target.bottom + 14;
  const top = below + tip.height + margin <= screen.height ? below : Math.max(margin, target.top - tip.height - 14);
  const left = Math.max(margin, Math.min(screen.width - tip.width - margin, target.left));
  return { top, left };
}

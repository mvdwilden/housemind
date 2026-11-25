// The seven Orb states (B-23): each has a colour AND a shape AND a word (E-46). "Nog niet
// actief" (E-275) looks like a pause, with its own words.

export type OrbState =
  | "calm"
  | "thinking"
  | "learning"
  | "attention"
  | "paused"
  | "sleeping"
  | "emergency"
  | "secured"
  | "inactive";

export type OrbShape = "circle" | "dots" | "plus" | "triangle" | "bars" | "crescent" | "rings" | "shield";

export interface OrbLook {
  state: OrbState;
  color: string;
  deep: string;
  glow: string;
  shape: OrbShape;
  textKey: string;
  network: number; // how much of the inner network shows (0–1); clearly visible, it turns
  breathes: boolean;
}

export const ORB_LOOKS: Record<OrbState, OrbLook> = {
  calm: { state: "calm", color: "#5ED4C4", deep: "#145E61", glow: "rgba(94,212,196,0.45)", shape: "circle", textKey: "orb.calm", network: 0.4, breathes: true },
  thinking: { state: "thinking", color: "#9C8CFF", deep: "#33287A", glow: "rgba(156,140,255,0.5)", shape: "dots", textKey: "orb.thinking", network: 0.95, breathes: true },
  learning: { state: "learning", color: "#F3B65B", deep: "#6B4512", glow: "rgba(243,182,91,0.45)", shape: "plus", textKey: "orb.learning", network: 0.75, breathes: true },
  attention: { state: "attention", color: "#FF9150", deep: "#6E2A0C", glow: "rgba(255,145,80,0.5)", shape: "triangle", textKey: "orb.attention", network: 0.4, breathes: true },
  paused: { state: "paused", color: "#8C96AD", deep: "#3A4258", glow: "rgba(140,150,173,0.3)", shape: "bars", textKey: "orb.paused", network: 0, breathes: false },
  sleeping: { state: "sleeping", color: "#5B6CC9", deep: "#1C2250", glow: "rgba(91,108,201,0.22)", shape: "crescent", textKey: "orb.sleeping", network: 0.2, breathes: true },
  // The alarm is on (E-539): a calm blue with a shield.
  secured: { state: "secured", color: "#4EA1F7", deep: "#133A6B", glow: "rgba(78,161,247,0.42)", shape: "shield", textKey: "orb.secured", network: 0.3, breathes: true },
  emergency: { state: "emergency", color: "#FF5468", deep: "#6A0E1B", glow: "rgba(255,84,104,0.6)", shape: "rings", textKey: "orb.emergency", network: 0, breathes: false },
  // "Nog niet actief" (E-275): the grey of a pause, with its own words.
  inactive: { state: "inactive", color: "#8C96AD", deep: "#3A4258", glow: "rgba(140,150,173,0.3)", shape: "bars", textKey: "orb.inactive", network: 0, breathes: false },
};

/** While HouseMind answers a question, the Orb thinks at once (E-159: within 0.2 s), unless
 * something more urgent shows: an emergency, "attention" or the pause. */
export function shownOrb(state: string, waiting: boolean): string {
  return waiting && !["emergency", "attention", "paused", "inactive"].includes(state) ? "thinking" : state;
}

export function orbLook(state: string, level: number): OrbLook {
  const look = ORB_LOOKS[state as OrbState] ?? ORB_LOOKS.calm;
  // At level 4 the inner network is always visible (H9.1).
  return level >= 4 ? { ...look, network: Math.max(look.network, 0.5) } : look;
}

/** How long the Orb shows a state at least, so people see that something happened (asked for
 * in a real house: after a tap it went teal → violet → teal in a blink). */
export const ORB_HOLD_MS = 3000;
/** These show at once: an emergency, "attention", thinking right after a tap (E-159), and a
 * pause someone just chose. */
export const ORB_AT_ONCE: ReadonlySet<string> = new Set(["emergency", "attention", "thinking", "paused", "inactive"]);

export interface OrbShown {
  state: string;
  since: number; // ms
}

/** What the Orb shows now: a new state waits until the shown one has been there ORB_HOLD_MS,
 * unless it must show at once. ``recheck``: when to look again (ms), or null. */
export function holdOrb(
  shown: OrbShown | null,
  wanted: string,
  now: number,
): { shown: OrbShown; recheck: number | null } {
  if (shown === null || shown.state === wanted) {
    return { shown: shown ?? { state: wanted, since: now }, recheck: null };
  }
  const held = now - shown.since;
  if (ORB_AT_ONCE.has(wanted) || held >= ORB_HOLD_MS) {
    return { shown: { state: wanted, since: now }, recheck: null };
  }
  return { shown, recheck: ORB_HOLD_MS - held };
}

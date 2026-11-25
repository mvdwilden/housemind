// The rhythm bar (E-507, B-433): the stretches of a type of day as parts of a 24-hour track,
// the unsure stretches over them, and one sentence for a screen reader.
import type { RhythmDay } from "./types";
import type { T } from "./ui";

export const DAY = 1440;
/** A part this wide (share of the day) has room for its icon. */
export const ICON_FROM = 0.09;
const ICONS = { sleep: "moon", morning: "sunrise", away: "away", home: "home" } as const;

export interface BarPart {
  kind: "sleep" | "morning" | "away" | "home";
  left: number; // percent
  width: number; // percent
  icon: string | null;
  title: string; // "Slapen 00:00–06:50"
}

/** Minutes from 0:00 as a clock: 410 → "06:50", 1440 → "24:00". */
export function clock(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

const pct = (minutes: number): number => (minutes / DAY) * 100;

/** The parts of the bar, each with its place, its icon when it is wide enough, and its words. */
export function barParts(day: RhythmDay, t: T): BarPart[] {
  return day.bar.map((s) => {
    const share = (s.to - s.from) / DAY;
    return {
      kind: s.kind,
      left: pct(s.from),
      width: pct(s.to - s.from),
      icon: share > ICON_FROM ? ICONS[s.kind] : null,
      title: `${t(`rhythm.legend.${s.kind}`)} ${clock(s.from)}–${clock(s.to)}`,
    };
  });
}

/** The stretches that are not sure yet, striped over the bar. */
export function unsureParts(day: RhythmDay): { left: number; width: number }[] {
  return (day.unsure ?? []).map((s) => ({ left: pct(s.from), width: pct(s.to - s.from) }));
}

/** One sentence for a screen reader: "Werkdagen: slapen 00:00–06:50, …; leert nog". */
export function barLabel(day: RhythmDay, t: T): string {
  if (!day.bar.length) return `${day.label}: ${t("rhythm.bar.not_enough")}`;
  const parts = barParts(day, t).map((p) => p.title.charAt(0).toLowerCase() + p.title.slice(1));
  const learning = day.learning ? `; ${t("rhythm.bar.learning")}` : "";
  return `${day.label}: ${parts.join(", ")}${learning}`;
}

/** Where "now" is on the bar, in percent – only on today's type of day. */
export function nowAt(day: RhythmDay, today: string | undefined, minute: number | undefined): number | null {
  if (!day.bar.length || today !== day.key || minute == null) return null;
  return pct(Math.max(0, Math.min(DAY, minute)));
}

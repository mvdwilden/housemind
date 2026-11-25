// 'Speel het huis' (E-321): the demo house's own time on the screen. The server sends the time
// with each picture; between pictures the screen lets it run on at the house's speed.
import type { DemoLive } from "./types";

/** The demo house's time now: what the server said, plus the time since, at its speed – or
 * the same, while it stands still (Stop) or a jump goes on. */
export function demoNow(live: DemoLive, receivedAt: number, nowMs: number): Date {
  const running = live.busy || !live.running ? 0 : Math.max(0, nowMs - receivedAt) * live.speed;
  return new Date(Date.parse(live.now) + running);
}

/** "di 22 okt · 19:04" in the app's language. */
export function demoTime(at: Date, language: string): string {
  const day = at.toLocaleDateString(language, { weekday: "short", day: "numeric", month: "short" });
  const time = at.toLocaleTimeString(language, { hour: "2-digit", minute: "2-digit" });
  return `${day} · ${time}`;
}

/** A line's time, with the weekday when it was not on the house's day of now. */
export function lineTime(line: { at: string; day: string }, today: Date, language: string): string {
  const own = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  if (line.day === own) return line.at;
  const day = new Date(`${line.day}T12:00:00`).toLocaleDateString(language, { weekday: "short" });
  return `${day} ${line.at}`;
}

/** The speed's text key: 0.05 → play.speed.slower20, 1 → play.speed.x1, 20 → play.speed.x20. */
export function speedKey(speed: number): string {
  return speed < 1 ? `play.speed.slower${Math.round(1 / speed)}` : `play.speed.x${Math.round(speed)}`;
}

/** Where a speed stands on the slider (the nearest of the speeds the house has). */
export function speedIndex(speeds: number[], speed: number): number {
  let best = 0;
  speeds.forEach((s, i) => {
    if (Math.abs(Math.log(s / speed)) < Math.abs(Math.log(speeds[best] / speed))) best = i;
  });
  return best;
}

/** How far a jump is, as a whole percentage. */
export function percent(done: number): number {
  return Math.max(0, Math.min(100, Math.round(done * 100)));
}

/** Free controls in groups by room, in the order the server gave them. */
export function byRoom<T extends { room: string }>(items: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const item of items) groups.set(item.room, [...(groups.get(item.room) ?? []), item]);
  return [...groups.entries()];
}

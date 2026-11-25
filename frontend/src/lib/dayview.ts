// The day in pictures (E-435 – E-437, design 52): the ring around Vesta, how often a part went
// well, and where the power of the house flows now.
import type { ReadyPart } from "./types";

/** A moment of the day on a clock of 24 hours: midnight at the top, clockwise. */
export function onRing(at: number, r: number, c: number): { x: number; y: number } {
  const angle = 2 * Math.PI * at;
  return { x: +(c + r * Math.sin(angle)).toFixed(2), y: +(c - r * Math.cos(angle)).toFixed(2) };
}

/** How often a part went well: the average of how often its skills went well (E-460), or null
 * without any. A row where nothing was judged yet says nothing; so does the bar of what
 * HouseMind does itself, which counts what was put back. A row without ``good`` measures. */
export function partScore(part: ReadyPart): number | null {
  const bars = (part.skills ?? [])
    .map((s) => (s.good !== undefined ? s.good : s.bar?.against ? null : s.bar?.value))
    .filter((v): v is number => typeof v === "number");
  return bars.length ? Math.round(bars.reduce((a, b) => a + b, 0) / bars.length) : null;
}

export interface Power {
  key: string;
  watts: number | null;
  active: boolean; // power flows that way now (more than 50 W)
  name?: string;
}

/** What flows into the house (sun, grid, a battery giving) and out of it (grid, a battery
 * charging, the car), from the live values; ``active`` when more than 50 W flows. */
export function powerFlow(live: {
  solar: number | null;
  grid: number | null;
  batteries: { name: string; watts: number | null }[];
  car: { watts: number | null } | null;
}): { into: Power[]; out: Power[] } {
  const into: Power[] = [];
  const out: Power[] = [];
  if (live.solar !== null) into.push({ key: "solar", watts: live.solar, active: live.solar > 50 });
  if (live.grid !== null) {
    if (live.grid >= 0) into.push({ key: "from_grid", watts: live.grid, active: live.grid > 50 });
    else out.push({ key: "to_grid", watts: -live.grid, active: live.grid < -50 });
  }
  for (const b of live.batteries) {
    const w = b.watts ?? 0;
    if (w < -50) into.push({ key: "battery", name: b.name, watts: -w, active: true });
    else out.push({ key: "battery", name: b.name, watts: b.watts, active: w > 50 });
  }
  if (live.car) out.push({ key: "car", watts: live.car.watts, active: (live.car.watts ?? 0) > 50 });
  return { into, out };
}

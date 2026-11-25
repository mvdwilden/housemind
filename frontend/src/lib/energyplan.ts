// The energy plan in pictures (E-346, B-223 – B-225, B-253): what the day clock and the plan
// draw – price bands, the stretches with a negative price, power in and out, the line when
// touched. The home battery and the car only before now, as measured (E-363). Showing only:
// nothing here changes the house.

import type { EnergyBlock, EnergyPoint } from "./types";
import type { T } from "./ui";

export type Band = "negative" | "low" | "mid" | "high" | "peak";

/** The band of a price: negative always stands out; expensive is measured against the day. */
export function priceBand(price: number | null, prices: (number | null)[]): Band | null {
  if (price === null) return null;
  if (price < -0.0005) return "negative";
  const known = prices.filter((p): p is number => p !== null);
  const hi = Math.max(...known);
  if (hi - Math.min(...known) < 0.005) return "mid"; // a fixed price has no cheap or dear hours
  if (price >= hi * 0.85) return "peak";
  if (price >= hi * 0.7) return "high";
  return price >= hi * 0.3 ? "mid" : "low";
}

/** The stretches with a negative price, as [first, end) of the points. */
export function negativeRuns(points: { price: number | null }[]): [number, number][] {
  const out: [number, number][] = [];
  points.forEach((p, i) => {
    if (p.price === null || p.price >= -0.0005) return;
    const last = out[out.length - 1];
    if (last && last[1] === i) last[1] = i + 1;
    else out.push([i, i + 1]);
  });
  return out;
}

export type Flow = "solar" | "from_battery" | "from_grid" | "house" | "to_battery" | "boiler" | "car" | "to_grid";

/** What comes in (above the line) and what goes out (below it), in watts; the two add up.
 * The battery and the car are only there before now (measured); after now they are 0. */
export function flows(p: EnergyPoint): { up: [Flow, number][]; down: [Flow, number][] } {
  return {
    up: [
      ["solar", p.solar],
      ["from_battery", Math.max(-p.battery, 0)],
      ["from_grid", Math.max(p.grid, 0)],
    ],
    down: [
      ["house", p.house],
      ["to_battery", Math.max(p.battery, 0)],
      ["boiler", p.boiler],
      ["car", p.car],
      ["to_grid", Math.max(-p.grid, 0)],
    ],
  };
}

export function total(parts: [Flow, number][]): number {
  return parts.reduce((sum, [, w]) => sum + Math.max(w, 0), 0);
}

export function kw(watts: number, t: T): string {
  return `${(Math.round(Math.abs(watts) / 100) / 10).toFixed(1).replace(".", t("format.decimal"))} kW`;
}

/** A price per kWh as the server shows it (E-304): with its sign, three decimals where two say too little. */
export function price(value: number, currency: string, t: T): string {
  let digits = Math.abs(value).toFixed(3);
  if (digits.endsWith("0")) digits = digits.slice(0, -1);
  const minus = Math.round(value * 1000) < 0 ? "−" : "";
  return `${minus}${currency} ${digits.replace(".", t("format.decimal"))}`;
}

/** The line above the plan for one point: everything at that moment, in words. */
export function readout(p: EnergyPoint, currency: string, t: T, expert = false): string {
  const parts = [p.past ? `${p.at} (${t("ui.energy.measured")})` : p.at];
  if (p.price !== null) parts.push(t("ui.energy.per_kwh", { price: price(p.price, currency, t) }));
  if (expert && p.sell != null && Math.abs(p.sell - (p.price ?? 0)) > 0.0005) {
    parts.push(`${t("ui.energy.legend.sell")} ${price(p.sell, currency, t)}`);
  }
  parts.push(t("ui.energy.readout.solar", { kw: kw(p.solar, t) }));
  parts.push(t("ui.energy.readout.house", { kw: kw(p.house, t) }));
  if (p.past && p.battery > 50) parts.push(t("ui.energy.readout.charging", { kw: kw(p.battery, t) }));
  else if (p.past && p.battery < -50) parts.push(t("ui.energy.readout.discharging", { kw: kw(p.battery, t) }));
  if (p.past && p.car > 50) parts.push(t("ui.energy.readout.car", { kw: kw(p.car, t) }));
  if (p.grid > 50) parts.push(t("ui.energy.readout.from_grid", { kw: kw(p.grid, t) }));
  else if (p.grid < -50) parts.push(t("ui.energy.readout.to_grid", { kw: kw(p.grid, t) }));
  return parts.join(" · ");
}

export const ROW_ORDER = ["boiler", "heat_pump", "export", "appliance"];

/** The rows of the timeline: a row per device with something to do, in a fixed order. */
export function timelineRows(blocks: EnergyBlock[]): string[] {
  const rows = new Set(blocks.map((b) => b.row));
  return ROW_ORDER.filter((r) => rows.has(r));
}

/** The first point after now: where the measured part ends (B-253). */
export function nowIndex(points: { past: boolean }[]): number {
  const i = points.findIndex((p) => !p.past);
  return i < 0 ? points.length : i;
}

/** Hour labels: about eight along the axis, on whole hours. */
export function hourTicks(points: { at: string }[], most = 8): number[] {
  const whole = points.map((p, i) => [p, i] as const).filter(([p]) => p.at.endsWith(":00"));
  const every = Math.max(1, Math.ceil(whole.length / most));
  const hours = whole.filter(([p]) => Number(p.at.slice(0, 2)) % every === 0);
  return (hours.length ? hours : whole).map(([, i]) => i);
}

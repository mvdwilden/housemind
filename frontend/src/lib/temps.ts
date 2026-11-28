// Temperaturen per ruimte (E-583): the order of the three temperatures and the words for
// "Pas na", shared by the window and the list.
import type { TempValue } from "./types";
import type { T } from "./ui";

export type Mode = "heat" | "cool";
export type Moment = "home" | "sleep" | "away";
export const MOMENTS: Moment[] = ["home", "sleep", "away"];

/** Slapen and Weg are never warmer than Thuis for heating, never colder for cooling: what
 * crosses Thuis goes along with it, as the window keeps the order itself. */
export function keepOrder(values: Record<Moment, TempValue>, mode: Mode): void {
  const home = values.home.value;
  for (const moment of ["sleep", "away"] as Moment[]) {
    const v = values[moment];
    if ((mode === "heat" && v.value > home) || (mode === "cool" && v.value < home)) {
      values[moment] = { value: home, from: "own" };
    }
  }
}

/** "Pas na" in words: at once, minutes, hours, a day. */
export function afterText(hours: number, t: T): string {
  if (hours <= 0) return t("ui.temps.after.now");
  if (hours < 1) return t("ui.temps.after.minutes", { n: Math.round(hours * 60) });
  if (hours >= 24) return t("ui.temps.after.day");
  return t("ui.temps.after.hours", { n: hours });
}

// Small helpers shared by the screens.

import { text } from "./i18n";
import type { Snapshot } from "./types";

export type T = (key: string, params?: Record<string, string | number>, count?: number) => string;

export function translator(snap: Snapshot | null): T {
  // The voice's name ("Vesta", or the one the administrator gave her) fills {name} wherever a
  // text speaks of her – also in Help – unless a text brings a name of its own.
  return (key, params = {}, count) =>
    snap ? text(snap.texts, snap.fallback, key, { name: snap.vesta?.name ?? "Vesta", ...params }, count) : key;
}

/** At most four tabs, never "More" (E-260, B-108): Huis, Logboek and Energie from level 2,
 * Brein – with the house rules – from level 3. The level decides how much, not where (E-262).
 * An administrator on Expert gets a fifth: Home Assistant netjes, a page of its own (E-449, B-363). */
export function tabsFor(level: number, energy: boolean, admin = false): string[] {
  if (level < 2) return [];
  const tabs = ["home", "logbook", "energy", ...(level >= 3 ? ["brain"] : []), ...(level >= 4 && admin ? ["tidy", "test"] : [])];
  return tabs.filter((tab) => energy || tab !== "energy");
}

/** The tabs in the menu. Where Test het huis opens as a column on the right – a browser on a
 * wide screen – it is a small round button at the top right instead, beside Help (the
 * administrator's wish, 14-11-2026, E-474); elsewhere, and on a phone, it stays in the menu. */
export function menuTabs(tabs: string[], testAside: boolean): string[] {
  return testAside ? tabs.filter((tab) => tab !== "test") : tabs;
}

/** The default house view per level (E-130); every view can be chosen on every level. */
export function defaultView(level: number): "cards" | "plan" | "network" {
  return level >= 4 ? "network" : level === 3 ? "plan" : "cards";
}

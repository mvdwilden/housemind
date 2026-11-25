import { describe, expect, it } from "vitest";
import { haLanguage, shownLanguage, type Around } from "./connection.svelte";
import { defaultView, menuTabs, tabsFor, translator } from "./ui";
import type { Snapshot } from "./types";

describe("screens per level", () => {
  it("grows the tabs with the level and hides energy until it exists", () => {
    expect(tabsFor(1, true)).toEqual([]);
    expect(tabsFor(2, false)).toEqual(["home", "logbook"]);
    expect(tabsFor(2, true)).toEqual(["home", "logbook", "energy"]);
    expect(tabsFor(3, true)).toEqual(["home", "logbook", "energy", "brain"]);
  });

  it("has at most four tabs, the same on level 3 and 4: the level decides how much, not where (E-260, E-262)", () => {
    for (const level of [1, 2, 3, 4]) expect(tabsFor(level, true).length).toBeLessThanOrEqual(4);
    expect(tabsFor(4, true)).toEqual(tabsFor(3, true));
    expect(tabsFor(4, true)).not.toContain("more");
  });

  it("gives only an administrator on Expert Check-up and Test het huis (E-449, E-474)", () => {
    expect(tabsFor(4, true, true)).toEqual(["home", "logbook", "energy", "brain", "tidy", "test"]);
    expect(tabsFor(4, false, true)).toEqual(["home", "logbook", "brain", "tidy", "test"]);
    for (const level of [1, 2, 3]) expect(tabsFor(level, true, true)).not.toContain("tidy");
    for (const level of [1, 2, 3]) expect(tabsFor(level, true, true)).not.toContain("test");
    expect(tabsFor(4, true, false)).not.toContain("tidy");
    expect(tabsFor(4, true, false)).not.toContain("test");
  });

  it("puts Test het huis at the top right where it opens as a column, else keeps it in the menu (E-474)", () => {
    const all = tabsFor(4, true, true);
    expect(menuTabs(all, true)).toEqual(["home", "logbook", "energy", "brain", "tidy"]);
    expect(menuTabs(all, false)).toEqual(all);
    expect(menuTabs(tabsFor(3, true, true), true)).toEqual(tabsFor(3, true, true));
  });

  it("chooses the house view per level", () => {
    expect([1, 2, 3, 4].map(defaultView)).toEqual(["cards", "cards", "plan", "network"]);
  });
});

describe("language of the Home Assistant profile (E-25)", () => {
  const ha = (hass: unknown): Around => ({ document: { querySelector: (s: string) => (s === "home-assistant" ? { hass } : null) } });
  const stored = (value: string | null) => ({ getItem: (key: string) => (key === "selectedLanguage" ? value : null) });

  it("reads HA's stored language safely", () => {
    expect(haLanguage(null, stored('"nl"'))).toBe("nl");
    expect(haLanguage(null, stored(null))).toBeUndefined();
    expect(haLanguage(null, stored("not json"))).toBeUndefined();
    expect(haLanguage(null, stored('{"a":1}'))).toBeUndefined();
    expect(haLanguage(null, undefined)).toBeUndefined();
  });

  it("follows what HA shows around it, before what this browser once stored (found in a real house)", () => {
    // The profile says English (kept in HA, not in this browser); the browser is Dutch.
    expect(haLanguage(ha({ language: "en", locale: { language: "en" } }), stored('"nl"'))).toBe("en");
    expect(haLanguage(ha({ language: "en" }), stored(null))).toBe("en");
    expect(haLanguage(ha({ locale: { language: "de" } }), stored(null))).toBe("de");
  });

  it("falls back to the stored language without a usable HA page around it", () => {
    expect(haLanguage(null, stored('"fr"'))).toBe("fr"); // not in a frame
    expect(haLanguage({ document: { querySelector: () => null } }, stored('"fr"'))).toBe("fr"); // a page without HA
    expect(haLanguage(ha(undefined), stored('"fr"'))).toBe("fr"); // HA not ready yet
    expect(haLanguage(ha({ language: 42 }), stored('"fr"'))).toBe("fr");
    expect(haLanguage(ha({ language: "x".repeat(17) }), stored('"fr"'))).toBe("fr");
  });

  it("never breaks on a page of another origin", () => {
    const foreign = {
      get document(): Around["document"] {
        throw new DOMException("Blocked a frame with origin", "SecurityError");
      },
    } as Around;
    expect(shownLanguage(foreign)).toBeUndefined();
    expect(haLanguage(foreign, stored('"nl"'))).toBe("nl");
    expect(haLanguage(foreign, undefined)).toBeUndefined();
  });
});

describe("the voice's name in every text (the Orb is called Vesta now)", () => {
  it("fills {name} with her name, unless a text brings its own", () => {
    const snap = { texts: { "help.l1.a3": "Tik op {name}.", "ui.x": "Zet ik \"{name}\" uit?" }, fallback: {}, vesta: { name: "Juno", formal: true } } as unknown as Snapshot;
    const t = translator(snap);
    expect(t("help.l1.a3")).toBe("Tik op Juno.");
    expect(t("ui.x", { name: "Avondlicht" })).toBe("Zet ik \"Avondlicht\" uit?");
  });
});

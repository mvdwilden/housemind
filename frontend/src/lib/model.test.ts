import { describe, expect, it } from "vitest";
import { ORB_HOLD_MS, ORB_LOOKS, holdOrb, orbLook, shownOrb } from "./orb";
import { text } from "./i18n";

describe("orb", () => {
  it("has eight states, each with its own colour and shape – Beveiligd too (E-539)", () => {
    const looks = Object.values(ORB_LOOKS).filter((l) => l.state !== "inactive");
    expect(looks).toHaveLength(8);
    expect(new Set(looks.map((l) => l.color)).size).toBe(8);
    expect(new Set(looks.map((l) => l.shape)).size).toBe(8);
  });

  it("is grey like a pause while not active yet, with its own words (E-275)", () => {
    const { inactive, paused } = ORB_LOOKS;
    expect([inactive.color, inactive.shape]).toEqual([paused.color, paused.shape]);
    expect(inactive.textKey).toBe("orb.inactive");
    expect(shownOrb("inactive", true)).toBe("inactive"); // no violet flash while not active
  });

  it("falls back to calm and shows the network at level 4", () => {
    expect(orbLook("nonsense", 1).state).toBe("calm");
    expect(orbLook("calm", 1).network).toBeLessThan(0.5);
    expect(orbLook("calm", 4).network).toBeGreaterThanOrEqual(0.5);
  });

  it("thinks at once while a question is answered, unless something is more urgent", () => {
    expect(shownOrb("calm", true)).toBe("thinking");
    expect(shownOrb("learning", true)).toBe("thinking");
    expect(shownOrb("sleeping", true)).toBe("thinking");
    expect(shownOrb("attention", true)).toBe("attention");
    expect(shownOrb("emergency", true)).toBe("emergency");
    expect(shownOrb("paused", true)).toBe("paused");
    expect(shownOrb("calm", false)).toBe("calm");
  });

  it("holds each state a moment, so a tap shows violet long enough to be seen", () => {
    // Teal, a tap: violet at once; the answer 0.3 s later does not take it away yet.
    let shown = holdOrb(null, "calm", 0).shown;
    shown = holdOrb(shown, "thinking", 10_000).shown;
    expect(shown.state).toBe("thinking");
    const early = holdOrb(shown, "calm", 10_300);
    expect(early.shown.state).toBe("thinking");
    expect(early.recheck).toBe(ORB_HOLD_MS - 300);
    expect(holdOrb(shown, "calm", 10_000 + ORB_HOLD_MS).shown.state).toBe("calm");
    // An emergency and "attention" never wait.
    expect(holdOrb(shown, "emergency", 10_100).shown.state).toBe("emergency");
    expect(holdOrb({ state: "learning", since: 0 }, "attention", 50).shown.state).toBe("attention");
    // A pause someone just chose shows at once too (found in the real house).
    expect(holdOrb({ state: "attention", since: 0 }, "paused", 50).shown.state).toBe("paused");
  });
});

describe("i18n", () => {
  const en = { "a.b": "Hello {name}", "n.one": "{count} person", "n.other": "{count} people" };
  const nl = { "a.b": "Hallo {name}", "n.one": "{count} persoon", "n.other": "{count} mensen" };

  it("formats, pluralises and falls back", () => {
    expect(text(nl, en, "a.b", { name: "huis" })).toBe("Hallo huis");
    expect(text(nl, en, "n", {}, 1)).toBe("1 persoon");
    expect(text(nl, en, "n", {}, 3)).toBe("3 mensen");
    expect(text({}, en, "a.b", { name: "house" })).toBe("Hello house");
    expect(text({}, {}, "missing.key")).toBe("missing.key");
  });
});

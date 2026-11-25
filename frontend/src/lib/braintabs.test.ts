import { describe, expect, it } from "vitest";
import { brainTabs, nextTab, openTab, tabCount, tabFor } from "./braintabs";
import type { ReadyPart } from "./types";

const part = (counts: Record<string, number>): ReadyPart =>
  ({ domain: "lighting", name: "Verlichting", counts, cap: null, capName: null, text: "", limit: false });

describe("Brein in tabjes (E-398)", () => {
  it("gives Involved three tabs and Expert also Apparaten, Ritme only with a rhythm", () => {
    expect(brainTabs(3, true)).toEqual(["readiness", "rules", "rhythm"]);
    expect(brainTabs(3, false)).toEqual(["readiness", "rules"]);
    // No decision trail and no stream of thoughts any more, and so no Meer (QA 3).
    expect(brainTabs(4, true)).toEqual(["readiness", "rules", "rhythm", "devices"]);
  });

  it("gives an administrator from Involved Wat ik weet after Ritme, in sight (E-524)", () => {
    expect(brainTabs(3, true, true)).toEqual(["readiness", "rules", "rhythm", "knowledge"]);
    expect(brainTabs(3, false, true)).toEqual(["readiness", "rules", "knowledge"]);
    expect(brainTabs(4, true, true)).toEqual(["readiness", "rules", "rhythm", "knowledge", "devices"]);
    expect(tabFor("knowledge:area:music:role")).toBe("knowledge");
    expect(tabCount("knowledge", [], [], 3)).toBe("3");
    expect(tabCount("knowledge")).toBe("");
  });

  it("opens the kept tab, or the first when this level does not have it", () => {
    const involved = brainTabs(3, true);
    expect(openTab("simulate", brainTabs(4, true))).toBe("readiness"); // a tab kept from before
    expect(openTab("rules", involved)).toBe("rules");
    expect(openTab("devices", involved)).toBe("readiness"); // Expert's tab, after going back a level
    expect(openTab("trace", brainTabs(4, true))).toBe("readiness"); // a tab that is gone (QA 3)
    expect(openTab(undefined, involved)).toBe("readiness");
  });

  it("lands a jump to the readiness of one part on its tab", () => {
    expect(tabFor("readiness")).toBe("readiness");
    expect(tabFor("ready-lighting")).toBe("readiness");
    expect(tabFor("tidy")).toBeNull();
  });

  it("counts what is on Autonomous of all, and the house rules", () => {
    const parts = [part({ observe: 10, propose: 6, autonomous: 4 }), part({ autonomous: 10 })];
    expect(tabCount("readiness", parts)).toBe("14/30");
    expect(tabCount("readiness", [])).toBe("");
    expect(tabCount("rules", [], [{ id: 1, text: "a" }, { id: 2, text: "b" }])).toBe("2");
    expect(tabCount("devices")).toBe("");
  });

  it("moves along the tabs with the arrow keys, round", () => {
    const tabs = brainTabs(3, true);
    expect(nextTab(tabs, "rhythm", "ArrowRight")).toBe("readiness");
    expect(nextTab(tabs, "readiness", "ArrowLeft")).toBe("rhythm");
    expect(nextTab(tabs, "rules", "End")).toBe("rhythm");
    expect(nextTab(tabs, "rules", "a")).toBeNull();
  });
});

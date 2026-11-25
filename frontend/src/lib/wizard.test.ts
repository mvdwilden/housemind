import { describe, expect, it } from "vitest";
import { introAnswered, introSteps, pipelineStillOpen, wizardPage, wizardTotal } from "./wizard";

describe("one count over the whole wizard (E-395)", () => {
  it("counts every page: four questions on Carefree make nine, three make eight", () => {
    expect(wizardTotal(4)).toBe(9);
    expect(wizardTotal(3)).toBe(8);
  });

  it("numbers the questions, the house questions and the steps after them", () => {
    expect(wizardPage("intro", true, 0, 4, 0)).toBe(1);
    expect(wizardPage("intro", true, 3, 4, 0)).toBe(4);
    expect(wizardPage("intro", false, 3, 4, 0)).toBe(5); // Over het huis
    expect(wizardPage("link", false, 3, 4, 1)).toBe(6);
    expect(wizardPage("finish", false, 3, 4, 4)).toBe(9);
    expect(wizardPage("finish", false, 2, 3, 4)).toBe(8);
  });
});

describe("Volgende only once a page has its answer (E-395)", () => {
  const fresh = { again: false, emergencyPush: null, consentLearning: false, hasPets: null };

  it("the level is always chosen", () => {
    expect(introAnswered("level", {}, fresh)).toBe(true);
  });

  it("a yes-or-no question waits for a choice, even with a default behind it", () => {
    expect(introAnswered("learning", {}, fresh)).toBe(false); // false by default, not answered
    expect(introAnswered("learning", { learning: false }, fresh)).toBe(true);
    expect(introAnswered("emergency", {}, fresh)).toBe(false);
  });

  it("pets: after Ja or Nee", () => {
    expect(introAnswered("pets", {}, fresh)).toBe(false);
    expect(introAnswered("pets", {}, { ...fresh, hasPets: false })).toBe(true);
  });

  it("the introduction once more: the answers of before count", () => {
    const again = { again: true, emergencyPush: true, consentLearning: true, hasPets: null };
    expect(introAnswered("emergency", {}, again)).toBe(true);
    expect(introAnswered("learning", {}, again)).toBe(true);
  });
});

describe("Vesta's own Assist pipeline on the last page (E-280, E-395)", () => {
  it("asks again when the link works and there is none yet, or someone else's", () => {
    expect(pipelineStillOpen({ contact: "ok", pipeline: { state: "none" } })).toBe(true);
    expect(pipelineStillOpen({ contact: "ok", pipeline: { state: "other" } })).toBe(true);
  });

  it("not when she has her own, nor while the link does not work yet", () => {
    expect(pipelineStillOpen({ contact: "ok", pipeline: { state: "ours" } })).toBe(false);
    expect(pipelineStillOpen({ contact: "none", pipeline: { state: "none" } })).toBe(false);
    expect(pipelineStillOpen({ contact: "ok", pipeline: { state: "no_agent" } })).toBe(false);
    expect(pipelineStillOpen(undefined)).toBe(false);
  });
});

describe("the page Onderdelen, only on Expert (E-401)", () => {
  it("comes after the introduction when setting up on Expert, and is counted", () => {
    expect(introSteps(4, true, true)).toEqual(["level", "learning", "pets", "parts"]);
    expect(wizardTotal(introSteps(4, true, true).length)).toBe(9);
    expect(introSteps(3, true, true)).toEqual(["level", "learning", "pets"]);
    expect(introSteps(1, true, true)).toEqual(["level", "emergency", "learning", "pets"]);
  });

  it("is not in the introduction once more, and has an answer of itself", () => {
    expect(introSteps(4, true, false)).not.toContain("parts");
    expect(introAnswered("parts", {}, { again: false, hasPets: null })).toBe(true);
  });
});

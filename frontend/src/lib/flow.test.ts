import { describe, expect, it } from "vitest";
import { causeEffect, flowKind, storySteps } from "./flow";

const t = (key: string) => key;

describe("one decision as a stream (E-434 – E-437)", () => {
  it("flows when done, waits for you when asked, stops otherwise", () => {
    expect(flowKind("executed")).toBe("done");
    expect(flowKind("proposed")).toBe("wait");
    expect(flowKind("observed")).toBe("stop");
    expect(flowKind("refused")).toBe("stop");
  });

  it("tells saw → thought → did, and how it ended", () => {
    const done = { outcome: "executed", text: "x", saw: ["beweging", "donker"], thought: "licht bij binnenkomen", did: "licht aan" };
    expect(storySteps(done, t)).toEqual([
      { label: "ui.mind.saw", text: "beweging · donker" },
      { label: "ui.mind.thought", text: "licht bij binnenkomen" },
      { label: "ui.mind.did", text: "licht aan" },
    ]);
    const asked = { ...done, outcome: "proposed", did: "gevraagd: licht aan", effect: "Licht aan" };
    expect(storySteps(asked, t).at(-1)).toEqual({ label: "ui.flow.you_decide", text: "Licht aan" });
    const left = { ...done, outcome: "observed", text: "Ik wilde het licht aanzetten, maar ik kijk nog mee." };
    expect(storySteps(left, t).at(-1)).toEqual({ label: "ui.flow.not_done", text: left.text });
  });

  it("shows cause → effect only when both are there", () => {
    const item = { at: "19:42", outcome: "executed", text: "x", cause: "beweging in de hal", effect: "licht aan" };
    expect(causeEffect(item, t)).toEqual([
      { label: "19:42", text: "beweging in de hal", mono: true },
      { label: "ui.flow.did", text: "licht aan" },
    ]);
    expect(causeEffect({ ...item, outcome: "proposed" }, t)?.[1].label).toBe("ui.flow.asked");
    expect(causeEffect({ outcome: "executed", text: "x" }, t)).toBeNull();
  });
});

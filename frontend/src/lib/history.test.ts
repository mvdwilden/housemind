import { describe, expect, it } from "vitest";
import { historyLine, progressWidth, type HistoryView } from "./history";

const t = (key: string, params: Record<string, unknown> = {}) => `${key} ${JSON.stringify(params)}`;
const view = (over: Partial<HistoryView>): HistoryView => ({ state: "idle", progress: 0, days: 0, available: null, readAt: null, ...over });

describe("learning from the history (E-257)", () => {
  it("says what it is doing, with the days Home Assistant keeps", () => {
    expect(historyLine(view({ state: "reading", available: 10 }), t)).toBe('ui.history.reading {"days":10}');
    expect(historyLine(view({ state: "failed" }), t)).toBe("ui.history.failed {}");
    expect(historyLine(view({ state: "done", readAt: "2026-10-19T17:00:00Z", days: 0 }), t)).toBe("ui.history.none {}");
    expect(historyLine(view({ state: "done", readAt: "2026-10-19T17:00:00Z", days: 10 }), t)).toContain('"days":10');
    expect(historyLine(view({ state: "idle" }), t)).toBe("");
  });

  it("shows the progress as a width between 0 and 100", () => {
    expect(progressWidth(view({ progress: 0.256 }))).toBe(26);
    expect(progressWidth(view({ progress: 1.4 }))).toBe(100);
    expect(progressWidth(undefined)).toBe(0);
  });
});

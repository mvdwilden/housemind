import { describe, expect, it } from "vitest";
import { noticePlace, topLayer, tourMayStart, TOUR_QUIET_MS, type Layers } from "./layers";

const none: Layers = { emergency: false, vesta: false, sheet: false, introduction: false, tour: false, notices: 0 };

describe("one layer at a time (E-259, B-107)", () => {
  it("puts an emergency above everything", () => {
    expect(topLayer({ ...none, emergency: true, vesta: true, sheet: true, tour: true, notices: 2 })).toBe("emergency");
  });

  it("goes Vesta, then a sheet, then the tour, then a short message", () => {
    expect(topLayer({ ...none, vesta: true, sheet: true, tour: true, notices: 1 })).toBe("vesta");
    expect(topLayer({ ...none, introduction: true, tour: true, notices: 1 })).toBe("sheet");
    expect(topLayer({ ...none, tour: true, notices: 1 })).toBe("tour");
    expect(topLayer({ ...none, notices: 1 })).toBe("notice");
    expect(topLayer(none)).toBeNull();
  });

  it("never lets a short message cover the introduction, the Vesta window or the tour", () => {
    expect(noticePlace({ ...none, introduction: true, notices: 1 })).toBe("wait");
    expect(noticePlace({ ...none, vesta: true, notices: 1 })).toBe("wait");
    expect(noticePlace({ ...none, tour: true, notices: 1 })).toBe("wait");
  });

  it("shows a short message at the foot of an open sheet, and on the screen otherwise", () => {
    expect(noticePlace({ ...none, sheet: true, notices: 1 })).toBe("sheet");
    expect(noticePlace({ ...none, notices: 1 })).toBe("screen");
  });

  it("starts the tour only when nothing is open and it has been quiet for 10 seconds", () => {
    expect(tourMayStart(none, null, 0)).toBe(true);
    expect(tourMayStart({ ...none, notices: 1 }, null, 0)).toBe(false);
    expect(tourMayStart({ ...none, sheet: true }, null, 0)).toBe(false);
    expect(tourMayStart({ ...none, emergency: true }, null, 0)).toBe(false);
    expect(tourMayStart(none, 1_000, 1_000 + TOUR_QUIET_MS - 1)).toBe(false);
    expect(tourMayStart(none, 1_000, 1_000 + TOUR_QUIET_MS)).toBe(true);
  });
});

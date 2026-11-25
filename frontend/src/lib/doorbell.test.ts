import { describe, expect, it } from "vitest";
import { doorbellToShow, type Doorbell } from "./doorbell";

const ring: Doorbell = { at: "2026-10-20T14:02:00+00:00", seconds: 30, left: 30, where: "Gang", url: "/api/camera_proxy_stream/camera.front_door?token=abc" };

describe("the doorbell on a wall tablet (E-187)", () => {
  it("shows only in kiosk mode", () => {
    expect(doorbellToShow(true, ring, null)).toBe(ring);
    expect(doorbellToShow(false, ring, null)).toBeNull();
  });
  it("stays closed for that ring, and a new ring shows again", () => {
    expect(doorbellToShow(true, ring, ring.at)).toBeNull();
    const again = { ...ring, at: "2026-10-20T14:03:00+00:00" };
    expect(doorbellToShow(true, again, ring.at)).toBe(again);
  });
  it("is gone after its 30 seconds", () => {
    expect(doorbellToShow(true, { ...ring, left: 0 }, null)).toBeNull();
    expect(doorbellToShow(true, null, null)).toBeNull();
  });
});

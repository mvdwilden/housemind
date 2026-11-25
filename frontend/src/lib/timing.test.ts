import { describe, expect, it } from "vitest";
import { timing } from "./timing";

// Time to talk and to read (E-299): the three choices, and an unknown one is Normaal.
describe("time to talk and to read", () => {
  it("gives more time step by step", () => {
    expect(timing("normal").listenMs).toBe(6000);
    expect(timing("longer").listenMs).toBe(12000);
    expect(timing("long").listenMs).toBe(20000);
    expect(timing("longer").noticeMs).toBe(20000);
    expect(timing("long").typedCloseMs).toBe(300000);
  });

  it("keeps messages until tapped away on Lang", () => {
    expect(timing("long").noticeMs).toBeNull();
    expect(timing("long").savedMs).toBeNull();
  });

  it("shows Bewaard in the header for 2 seconds, longer on Langer, until tapped on Lang", () => {
    expect(timing("normal").briefMs).toBe(2000);
    expect(timing("longer").briefMs).toBe(4000);
    expect(timing("long").briefMs).toBeNull();
  });

  it("falls back to Normaal", () => {
    expect(timing(undefined)).toEqual(timing("normal"));
    expect(timing("forever")).toEqual(timing("normal"));
  });
});

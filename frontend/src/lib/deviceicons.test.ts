import { describe, expect, it } from "vitest";
import { DEVICE_PATHS, DOMAIN_ICONS, batteryBars, lampFillTop, shutterPath, tone } from "./deviceicons";

describe("the icon set (E-508)", () => {
  it("has a drawing for every icon a kind of device can get", () => {
    for (const icon of Object.values(DOMAIN_ICONS)) expect(DEVICE_PATHS[icon]).toBeTruthy();
    expect(Object.keys(DEVICE_PATHS).length).toBeGreaterThan(60);
  });

  it("fills a lamp to its brightness, and gives a battery its streaks", () => {
    expect(lampFillTop(0)).toBeCloseTo(15.4);
    expect(lampFillTop(100)).toBeCloseTo(2.8);
    expect(lampFillTop(35)).toBeGreaterThan(lampFillTop(60));
    expect(batteryBars(0)).toHaveLength(0);
    expect(batteryBars(62)).toHaveLength(2);
    expect(batteryBars(100)).toHaveLength(4);
  });

  it("lets a shutter come down, and colours only what is on", () => {
    expect(shutterPath(0).match(/M4 \d/g) ?? []).toHaveLength(1); // only the box
    expect(shutterPath(100).match(/M4 \d/g) ?? []).toHaveLength(4);
    expect(tone("lamp", true)).toBe("lamp");
    expect(tone("tv", true)).toBe("on");
    expect(tone("tv", false)).toBe("");
    expect(tone("smoke", false, true)).toBe("alarm");
  });
});

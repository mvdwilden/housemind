import { describe, expect, it } from "vitest";
import { flows, hourTicks, kw, negativeRuns, nowIndex, price, priceBand, readout, timelineRows, total } from "./energyplan";
import type { EnergyBlock, EnergyPoint } from "./types";

// The texts as the app has them, enough for these tests.
const TEXTS: Record<string, string> = {
  "format.decimal": ",",
  "ui.energy.per_kwh": "{price}/kWh",
  "ui.energy.legend.sell": "terugleverprijs",
  "ui.energy.readout.solar": "zon {kw}",
  "ui.energy.readout.house": "huis {kw}",
  "ui.energy.readout.charging": "batterij laadt {kw}",
  "ui.energy.readout.discharging": "batterij levert {kw}",
  "ui.energy.readout.idle": "batterij wacht",
  "ui.energy.readout.from_grid": "uit het net {kw}",
  "ui.energy.readout.to_grid": "naar het net {kw}",
  "ui.energy.readout.car": "auto laadt {kw}",
  "ui.energy.measured": "gemeten",
};
const t = (key: string, params: Record<string, string | number> = {}) =>
  (TEXTS[key] ?? key).replace(/\{(\w+)\}/g, (_, k: string) => String(params[k] ?? ""));

// The prices of a real day (25-10-2026): negative from the night until the afternoon, dear at 18:45.
const DAY = [0.01, -0.002, -0.006, -0.004, 0.0, 0.048, 0.132, 0.151, 0.175, 0.124, 0.062, 0.028];

function point(over: Partial<EnergyPoint> = {}): EnergyPoint {
  return { at: "13:45", price: -0.006, solar: 3600, house: 500, battery: 1000, car: 0, boiler: 0, grid: -2100, past: true, ...over };
}

describe("the energy plan in pictures (E-346)", () => {
  it("shows a negative price as negative, and dear against the day", () => {
    expect(priceBand(-0.006, DAY)).toBe("negative");
    expect(priceBand(0.175, DAY)).toBe("peak");
    expect(priceBand(0.132, DAY)).toBe("high");
    expect(priceBand(0.062, DAY)).toBe("mid");
    expect(priceBand(0.01, DAY)).toBe("low");
    expect(priceBand(0.25, [0.25, 0.25])).toBe("mid"); // a fixed price has no cheap or dear hours
    expect(priceBand(null, DAY)).toBeNull();
  });

  it("finds the stretches with a negative price, for the light blue band", () => {
    expect(negativeRuns(DAY.map((price) => ({ price })))).toEqual([[1, 4]]);
    expect(negativeRuns([{ price: 0.1 }, { price: null }])).toEqual([]);
  });

  it("puts what comes in above the line and what goes out below it, and both add up", () => {
    const f = flows(point());
    expect(f.up).toEqual([["solar", 3600], ["from_battery", 0], ["from_grid", 0]]);
    expect(f.down).toEqual([["house", 500], ["to_battery", 1000], ["boiler", 0], ["car", 0], ["to_grid", 2100]]);
    expect(total(f.up)).toBe(total(f.down));
    const evening = flows(point({ solar: 0, house: 2000, battery: -1500, grid: 500 }));
    expect(total(evening.up)).toBe(2000);
    expect(evening.up[1]).toEqual(["from_battery", 1500]);
  });

  it("writes a price with its sign, as the server does (E-304)", () => {
    expect(price(-0.006, "€", t)).toBe("−€ 0,006");
    expect(price(0.175, "€", t)).toBe("€ 0,175");
    expect(price(0.12, "€", t)).toBe("€ 0,12");
    expect(price(-0.0004, "€", t)).toBe("€ 0,00");
    expect(kw(-1500, t)).toBe("1,5 kW");
  });

  it("says one moment in words when the chart is touched; the battery and the car only as measured", () => {
    expect(readout(point(), "€", t)).toBe(
      "13:45 (gemeten) · −€ 0,006/kWh · zon 3,6 kW · huis 0,5 kW · batterij laadt 1,0 kW · naar het net 2,1 kW",
    );
    expect(readout(point({ car: 7200 }), "€", t)).toContain("auto laadt 7,2 kW");
    expect(readout(point({ battery: 0, grid: 0 }), "€", t)).not.toContain("batterij");
    // After now HouseMind does not plan them (E-363): nothing about the battery or the car.
    expect(readout(point({ past: false }), "€", t)).toBe("13:45 · −€ 0,006/kWh · zon 3,6 kW · huis 0,5 kW · naar het net 2,1 kW");
    // Expert also sees the price for giving back, when it differs.
    expect(readout(point({ sell: 0.01 }), "€", t, true)).toContain("terugleverprijs € 0,01");
    expect(readout(point({ sell: 0.01 }), "€", t, false)).not.toContain("terugleverprijs");
  });

  it("has a timeline row per device with something to do, never the battery or the car", () => {
    const blocks = [{ row: "export" }, { row: "boiler" }, { row: "boiler" }] as EnergyBlock[];
    expect(timelineRows(blocks)).toEqual(["boiler", "export"]);
    expect(timelineRows([{ row: "battery" }] as EnergyBlock[])).toEqual([]);
    expect(timelineRows([])).toEqual([]);
  });

  it("knows where the measured part ends", () => {
    expect(nowIndex([{ past: true }, { past: true }, { past: false }])).toBe(2);
    expect(nowIndex([{ past: false }])).toBe(0);
    expect(nowIndex([])).toBe(0);
  });

  it("labels whole hours, about eight along the axis", () => {
    const points = Array.from({ length: 72 }, (_, i) => ({ at: `${String(Math.floor(i / 2) % 24).padStart(2, "0")}:${i % 2 ? "30" : "00"}` }));
    const ticks = hourTicks(points);
    expect(ticks.length).toBeLessThanOrEqual(10);
    expect(ticks.every((i) => points[i].at.endsWith(":00"))).toBe(true);
  });
});

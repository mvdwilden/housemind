import { describe, expect, it } from "vitest";
import { checkedAfter, every, keepLine, size, tidyLines, tidyTitle, unusedLine } from "./tidy";
import type { TidyItem } from "./types";

// A translator that shows the key, the parameters and the count, so the lines can be read back.
const t = (key: string, params: Record<string, string | number> = {}, count?: number) =>
  `${key}${count === undefined ? "" : `#${count}`}${Object.keys(params).length ? JSON.stringify(params) : ""}`;

const base: TidyItem = {
  key: "k", kind: "unavailable", name: "Tuinsensor", target: "sensor.x", since: "2026-10-25T10:00:00Z",
  heavy: false, followsCount: 0, follows: [], depends: [], dependsCount: 0, used: 0, open: "/config/entities",
  rooms: [],
  can: [],
  markTarget: null,
};

describe("the lines under a card of Home Assistant netjes (B-245)", () => {
  it("names the state of an integration that does not start, and what depends on it", () => {
    const lines = tidyLines({ ...base, kind: "integration_down", state: "setup_retry", followsCount: 12, dependsCount: 2, used: 3 }, t);
    expect(lines).toEqual([
      'ui.tidy.what.integration_down{"state":"ui.tidy.state.setup_retry"}',
      'ui.tidy.depends#2{"count":2}',
      'ui.tidy.used#3{"count":3}',
    ]);
  });

  it("names the working twin of a duplicate", () => {
    const lines = tidyLines({ ...base, kind: "duplicate", suggest: { entityId: "sensor.a", name: "Garage", room: null } }, t);
    expect(lines).toEqual(['ui.tidy.what.duplicate{"name":"Garage (sensor.a)"}']);
  });

  it("counts what a reference points to that is gone, and says HouseMind uses one entity itself", () => {
    expect(tidyLines({ ...base, kind: "missing_ref", missing: ["light.a", "light.b"] }, t)).toEqual([
      'ui.tidy.what.missing_ref#2{"ids":"light.a, light.b","count":2}',
    ]);
    expect(tidyLines({ ...base, used: 1 }, t)).toEqual(["ui.tidy.what.unavailable", "ui.tidy.used_self"]);
  });

  it("explains a bundle of one integration and counts what HouseMind uses of it (B-258)", () => {
    const lines = tidyLines({ ...base, kind: "not_provided_group", followsCount: 76, used: 2 }, t);
    expect(lines).toEqual(["ui.tidy.what.not_provided_group", 'ui.tidy.used#2{"count":2}']);
  });

  it("tells what a still meter measures, its value and what was expected (B-265)", () => {
    const meter = { role: "solar", value: "0.0", unit: "Wh", expected_wh: 4200 };
    expect(tidyLines({ ...base, kind: "meter_still", meter }, t)).toEqual([
      'ui.tidy.what.meter_still{"role":"ui.tidy.role.solar","value":"0.0 Wh"}',
      'ui.tidy.expected{"kwh":"4.2"}',
      "ui.tidy.used_meter",
    ]);
  });

  it("names what looks like a grid meter, or asks for one (B-267)", () => {
    const candidates = [{ entityId: "sensor.p1_import", name: "P1 afname", room: null }];
    expect(tidyLines({ ...base, kind: "energy_no_grid", candidates }, t)).toEqual([
      "ui.tidy.what.energy_no_grid",
      'ui.tidy.like{"name":"P1 afname (sensor.p1_import)"}',
    ]);
    expect(tidyLines({ ...base, kind: "energy_no_grid", candidates: [] }, t)).toEqual([
      "ui.tidy.what.energy_no_grid",
      "ui.tidy.no_grid_none",
    ]);
  });

  it("puts Home Assistant's own points in plain words, and counts them in the title", () => {
    const issues = [
      { type: "entity_unavailable", entityId: "sensor.solar", name: "Zon", room: null },
      { type: "something_new", entityId: "sensor.x", name: "", room: null },
    ];
    const item = { ...base, kind: "energy_issues" as const, issues };
    expect(tidyLines(item, t)).toEqual([
      "ui.tidy.what.energy_issues",
      'ui.tidy.issue_line{"name":"Zon","problem":"ui.tidy.issue.entity_unavailable"}',
      'ui.tidy.issue_line{"name":"sensor.x","problem":"ui.tidy.issue.other{\\"type\\":\\"something_new\\"}"}',
    ]);
    expect(tidyTitle(item, t)).toBe("ui.tidy.kind.energy_issues#2");
  });
});

describe("part C of Home Assistant netjes (B-240 – B-242)", () => {
  it("says how long an automation has not run", () => {
    expect(unusedLine({ ...base, kind: "unused_on", days: 410 }, t)).toBe('ui.tidy.unused.on#410{"count":410}');
    expect(unusedLine({ ...base, kind: "unused_on", days: 95, never: true }, t)).toBe('ui.tidy.unused.never{"count":95}');
    expect(unusedLine({ ...base, kind: "unused_off", days: 256 }, t)).toBe('ui.tidy.unused.off{"count":256}');
  });

  it("shows sizes and how often something changes", () => {
    expect(size(1508.73 * 2 ** 20, "nl")).toBe("1,5 GB");
    expect(size(300 * 2 ** 20, "en")).toBe("300 MB");
    expect(every(2, t, "nl")).toBe('ui.tidy.system.seconds{"n":"2"}');
    expect(every(300, t, "nl")).toBe('ui.tidy.system.minutes{"n":"5"}');
    expect(every(3 * 3600, t, "nl")).toBe('ui.tidy.system.hours{"n":"3"}');
  });

  it("names the looks after an update so far", () => {
    expect(checkedAfter(3, t, "en")).toBe(
      'ui.tidy.update.checked{"times":"ui.tidy.update.after.m30, ui.tidy.update.after.h2, and ui.tidy.update.after.h6"}',
    );
  });
});

describe("what Home Assistant keeps (E-397)", () => {
  it("says how often and how many, in the reader's own day names", () => {
    expect(keepLine({ recurrence: "daily", days: [], copies: 3, keepDays: null }, t, "nl")).toBe(
      'ui.tidy.system.keep_line{"when":"ui.tidy.system.keep_when_daily","what":"ui.tidy.system.keep_copies#3{\\"count\\":3}"}',
    );
    const days = keepLine({ recurrence: "custom_days", days: ["thu", "mon"], copies: null, keepDays: 14 }, t, "nl");
    expect(days).toContain("maandag en donderdag");
    expect(days).toContain("ui.tidy.system.keep_days#14");
    expect(keepLine({ recurrence: "daily", days: [], copies: null, keepDays: null }, t, "en")).toContain("ui.tidy.system.keep_all");
    expect(keepLine({ recurrence: "never", days: [], copies: null, keepDays: null }, t, "en")).toBe("ui.tidy.system.keep_never");
  });
});

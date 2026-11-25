import { describe, expect, it } from "vitest";
import { acts, actKey, choiceOf, counts, grouped, isFold, shown, type DeviceFold } from "./knowledge";
import type { KnowledgeRow } from "./types";

const row = (over: Partial<KnowledgeRow>): KnowledgeRow => ({
  id: "x", kind: "entity", field: "function", label: "Wat het is", name: "Beweging", what: "ziet beweging",
  role: "", source: "ai", fromAI: false, note: "", state: "yes", since: "2026-11-12T10:00:00", room: "Voordeur",
  roomId: "front", ...over,
});

describe("Wat ik weet (E-524)", () => {
  const rows = [
    row({ id: "area:music:role", kind: "area", field: "role", name: "Muziekkamer", what: "Slaapkamer", room: "Muziekkamer", roomId: "music" }),
    row({ id: "entity:c1:function", name: "Dier gezien", device: "Camera voordeur", deviceId: "cam" }),
    row({ id: "entity:c2:function", name: "Is donker", what: "meet het licht", device: "Camera voordeur", deviceId: "cam" }),
    row({ id: "entity:c3:function", name: "Beweging", device: "Camera voordeur", deviceId: "cam" }),
    row({ id: "entity:s:opening", field: "opening", name: "Contact", what: "een deur of raam", state: "open", source: "rules", room: "", roomId: "" }),
    row({ id: "entity:l:status_led", field: "status_led", name: "Lampje", state: "no", room: "Trap", roomId: "stairs", device: "Bewegingsmelder", deviceId: "pir" }),
  ];

  it("counts the three states and shows one tile, or all of them, with the search", () => {
    expect(counts(rows)).toEqual({ open: 1, yes: 4, no: 1 });
    expect(shown(rows, "open", "").map((r) => r.name)).toEqual(["Contact"]);
    expect(shown(rows, null, "").length).toBe(6);
    expect(shown(rows, null, "camera").length).toBe(3); // the device's name counts too
    expect(shown(rows, "yes", "muziek").map((r) => r.id)).toEqual(["area:music:role"]);
  });

  it("groups per room, the rooms by name and the lines without a room last", () => {
    const groups = grouped(rows, true, "Zonder ruimte");
    expect(groups.map((g) => g.name)).toEqual(["Muziekkamer", "Trap", "Voordeur", "Zonder ruimte"]);
    expect(groups.map((g) => g.count)).toEqual([1, 1, 3, 1]);
  });

  it("folds the lines of one device, with what is in it, and leaves one line alone", () => {
    const front = grouped(rows, true, "-").find((g) => g.key === "front");
    expect(front?.entries.length).toBe(1);
    const fold = front?.entries[0] as DeviceFold;
    expect(isFold(fold)).toBe(true);
    expect(fold.name).toBe("Camera voordeur");
    expect(fold.summary).toEqual([{ what: "ziet beweging", count: 2 }, { what: "meet het licht", count: 1 }]);
    const stairs = grouped(rows, true, "-").find((g) => g.key === "stairs");
    expect(isFold(stairs!.entries[0])).toBe(false); // one line of a device stays a line
    expect(grouped(rows, false, "-").map((g) => g.count)).toEqual([6]);
  });
});

describe("Alles weer aan te passen (E-525)", () => {
  it("gives every state its three buttons", () => {
    const room = row({ kind: "area", field: "role" });
    expect(acts({ ...room, state: "open" })).toEqual(["yes", "no", "other"]);
    expect(acts({ ...room, state: "yes" })).toEqual(["other", "no", "back"]);
    expect(acts({ ...room, state: "no" })).toEqual(["yes", "other", "back"]);
  });

  it("offers no other choice where there is none, and no 'toch niet' for a person's own choice", () => {
    const doubt = row({ field: "opening" });
    expect(acts({ ...doubt, state: "yes" })).toEqual(["no", "back"]);
    expect(acts(row({ kind: "area", field: "role", source: "person", state: "yes" }))).toEqual(["other", "back"]);
    expect(acts(row({ kind: "area", field: "role", state: "open", unknown: true }))).toEqual([]);
  });

  it("says 'toch' once it was answered", () => {
    expect(actKey("no", "open")).toBe("ui.knowledge.no");
    expect(actKey("no", "yes")).toBe("ui.knowledge.act.no_after_all");
    expect(actKey("yes", "no")).toBe("ui.knowledge.act.yes_after_all");
    expect(actKey("back", "yes")).toBe("ui.knowledge.act.back");
  });

  it("knows what Iets anders… offers", () => {
    expect(choiceOf(row({ kind: "area", field: "role" }))).toBe("role");
    expect(choiceOf(row({}))).toBe("function");
    expect(choiceOf(row({ kind: "area", field: "temperature", options: [{ id: "a", name: "A" }] }))).toBeNull();
    expect(choiceOf(row({ kind: "area", field: "temperature", options: [{ id: "a", name: "A" }, { id: "b", name: "B" }] }))).toBe("sensor");
    expect(choiceOf(row({ field: "status_led" }))).toBeNull();
  });
});

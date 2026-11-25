import { describe, expect, it } from "vitest";
import { MAX_DEVICES, ROOM_H, curve, grouped, networkLayout, shownDevices, type NetInput } from "./network";

const NAMES = ["Hal", "Keuken", "Woonkamer", "Slaapkamer", "Badkamer", "Garage", "Toilet", "Overloop", "Werkkamer", "Bijkeuken",
  "Zolder", "Tuin", "Oprit", "Kinderkamer", "Logeerkamer", "Berging", "Wasruimte", "Terras", "Eetkamer", "Kelder", "Speelkamer"];

function house(n: number, devices = MAX_DEVICES): NetInput[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `r${i}`,
    name: i === 3 ? "Slaapkamer met een heel lange naam" : NAMES[i % NAMES.length],
    devices: Array.from({ length: devices + (i % 3) }, (_, j) => ({ id: `r${i}.d${j}` })),
  }));
}

type Box = { l: number; r: number; t: number; b: number };
const overlap = (a: Box, b: Box): boolean => a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b;

/** What each room takes on the screen: its block, and its row of devices. */
function boxes(layout: ReturnType<typeof networkLayout>): Box[] {
  return layout.rooms.map((room) => {
    const xs = [room.x - room.w / 2, room.x + room.w / 2, ...room.devices.map((d) => d.x - 6), ...room.devices.map((d) => d.x + 6)];
    const ys = [room.y - ROOM_H / 2, room.y + ROOM_H / 2, ...room.devices.map((d) => d.y - 6), ...room.devices.map((d) => d.y + 6)];
    return { l: Math.min(...xs), r: Math.max(...xs), t: Math.min(...ys), b: Math.max(...ys) };
  });
}

describe("the network of the house (E-439, 'Het huis in beweging')", () => {
  for (const width of [300, 390, 768, 1068]) {
    for (const n of [1, 3, 6, 9, 12, 19, 24]) {
      it(`${n} rooms at ${width} px: nothing lies on anything else, all inside the drawing`, () => {
        const layout = networkLayout(house(n), width);
        expect(layout.upright).toBe(width < 560);
        const all = boxes(layout);
        const vesta = { l: layout.cx - layout.ring, r: layout.cx + layout.ring, t: layout.cy - layout.ring, b: layout.cy + layout.ring };
        all.forEach((a, i) => {
          expect(overlap(a, vesta), `room ${i} on Vesta`).toBe(false);
          all.forEach((b, j) => j > i && expect(overlap(a, b), `rooms ${i} and ${j}`).toBe(false));
          expect(a.l).toBeGreaterThanOrEqual(0);
          expect(a.r).toBeLessThanOrEqual(layout.width);
          expect(a.t).toBeGreaterThanOrEqual(0);
          expect(a.b).toBeLessThanOrEqual(layout.height);
        });
        // Every room stays a 44 px target, also on the smallest phone (M-21).
        for (const room of layout.rooms) expect(room.w).toBeGreaterThanOrEqual(44);
      });
    }
  }

  it("Vesta stands in the middle, larger on a computer than on a phone", () => {
    const wide = networkLayout(house(6), 1000);
    const phone = networkLayout(house(6), 390);
    expect(wide.cx).toBe(500);
    expect(wide.orb).toBeGreaterThan(phone.orb);
    expect(wide.ring).toBeGreaterThan(wide.orb / 2);
    // A few rooms keep the drawing calm: not much taller than wide on a computer.
    expect(wide.height).toBeLessThan(wide.width);
    expect(phone.height).toBeGreaterThan(phone.width * 0.9); // upright on a phone
  });

  it("devices hang on the outer side of their room", () => {
    const layout = networkLayout(house(6), 1000);
    for (const room of layout.rooms) {
      for (const d of room.devices) {
        if (room.below) expect(d.y).toBeGreaterThan(room.y);
        else expect(d.y).toBeLessThan(room.y);
      }
    }
    expect(layout.rooms[0].below).toBe(false); // the room at the top: its devices above it
  });

  it("the device the path goes to is always drawn, even after the first eight", () => {
    const devices = Array.from({ length: 12 }, (_, j) => ({ id: `d${j}` }));
    expect(shownDevices(devices).map((d) => d.id)).toHaveLength(MAX_DEVICES);
    const kept = shownDevices(devices, "d10").map((d) => d.id);
    expect(kept).toHaveLength(MAX_DEVICES);
    expect(kept).toContain("d10");
    expect(shownDevices(devices, "d2")).toEqual(devices.slice(0, MAX_DEVICES));
    expect(shownDevices(devices, "elsewhere")).toEqual(devices.slice(0, MAX_DEVICES));
  });

  it("what it just did and the question that waits are both drawn in the same room", () => {
    const devices = Array.from({ length: 12 }, (_, j) => ({ id: `d${j}` }));
    const both = shownDevices(devices, ["d10", "d11"]).map((d) => d.id);
    expect(both).toHaveLength(MAX_DEVICES);
    expect(both).toEqual(expect.arrayContaining(["d10", "d11"]));
    const one = shownDevices(devices, ["d7", "d11"]).map((d) => d.id);
    expect(one).toEqual(expect.arrayContaining(["d7", "d11"])); // the first eight's own stays
    expect(shownDevices(devices, [null, null])).toEqual(devices.slice(0, MAX_DEVICES));
  });

  it("the path room → Vesta → device is two curves bent to either side", () => {
    const a = curve({ x: 0, y: 0 }, { x: 100, y: 0 }, 1);
    const b = curve({ x: 0, y: 0 }, { x: 100, y: 0 }, -1);
    expect(a).toBe("M0 0 Q50 18 100 0");
    expect(b).toBe("M0 0 Q50 -18 100 0");
  });
});

describe("a group is one dot for its devices (the administrator's wish, 14-11-2026, E-515)", () => {
  const lamp = (id: string) => ({ id });
  const group = (id: string, members: string[], radio = false) => ({ id, members, radio });

  it("the group stands for its lamps, first in the row; the rest stays", () => {
    const { devices, standsFor } = grouped([lamp("l1"), lamp("l2"), lamp("tv"), group("g", ["l1", "l2"])]);
    expect(devices.map((d) => d.id)).toEqual(["g", "tv"]);
    expect(standsFor.get("l1")).toBe("g");
    expect(standsFor.get("tv")).toBeUndefined();
  });

  it("a smaller group inside a bigger one goes with the bigger one, also a group in a group", () => {
    const { devices, standsFor } = grouped([
      lamp("a"), lamp("b"), lamp("c"),
      group("small", ["a", "b"]),
      group("all", ["small", "c"]),
    ]);
    expect(devices.map((d) => d.id)).toEqual(["all"]);
    expect(standsFor.get("a")).toBe("all");
    expect(standsFor.get("small")).toBe("all");
  });

  it("of two groups that hold the same lamps, the one in the radio network stays", () => {
    const { devices } = grouped([lamp("a"), lamp("b"), group("ha", ["a", "b"]), group("radio", ["a", "b"], true)]);
    expect(devices.map((d) => d.id)).toEqual(["radio"]);
  });

  it("curtains and switches too; a group whose devices are in another room is a dot like any other", () => {
    const { devices } = grouped([lamp("cover.left"), lamp("cover.right"), group("cover.all", ["cover.left", "cover.right"]), group("light.upstairs", ["light.elsewhere"])]);
    expect(devices.map((d) => d.id)).toEqual(["cover.all", "light.upstairs"]);
  });

  it("eight groups and lamps fit where eight lamps of one group took all the room", () => {
    const lamps = Array.from({ length: 8 }, (_, i) => lamp(`hall.${i}`));
    const { devices } = grouped([...lamps, group("hall", lamps.map((l) => l.id)), lamp("door")]);
    expect(shownDevices(devices).map((d) => d.id)).toEqual(["hall", "door"]);
  });
});

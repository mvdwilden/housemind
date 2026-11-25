// The house in motion (E-439, design 'Het huis in beweging'): where the brain, the rooms and
// their devices stand in the network, in screen pixels. Vesta in the middle with the AI ring
// around her, the rooms as named blocks on an oval around that, each room's devices as a row
// of small dots on its outer side. On a narrow screen the oval stands upright. Nothing may lie
// on anything else: when the rooms do not fit, the oval grows taller (the page scrolls).

export interface NetInput {
  id: string;
  name: string;
  devices: { id: string }[];
}

export interface NetDevice {
  id: string;
  x: number;
  y: number;
}

export interface NetRoom {
  id: string;
  x: number; // the block's middle
  y: number;
  w: number; // the block's width; its height is ROOM_H
  below: boolean; // the devices lie below the block (else above it)
  edge: number; // y of the block's edge the devices hang from
  devices: NetDevice[];
}

export interface NetLayout {
  width: number;
  height: number;
  upright: boolean;
  cx: number; // Vesta's middle
  cy: number;
  orb: number; // her diameter
  ring: number; // the AI ring's radius
  dot: number; // a device's radius: room for its icon on a computer (E-510)
  rooms: NetRoom[];
}

export const ROOM_H = 44; // a room is a 44 px target (M-21, WCAG 2.5.8)
export const MAX_DEVICES = 8;
export const ROOM_ICON = 18; // the room's icon in its block, on a computer (E-510)
const GAP = 10; // the least space between two rooms
const MARGIN = 8; // from the edge of the drawing
const DOTS_AWAY = 20; // from the block's edge to its row of devices
const DOTS_ROOM = 34; // what a row of devices and its line take beside the block
const LABEL = 18; // the device's name beyond its dot, for the path

/** How wide a name is at a font size: measured in the browser, else about 0.56 em a letter. */
export type Measure = (text: string, size: number) => number;
const guess: Measure = (text, size) => text.length * size * 0.56;

/** How wide a room's block is: its name with some room, never smaller than a target. Names are
 * 15 px, on a phone 14 px. */
export function blockWidth(name: string, upright: boolean, most: number, scale = 1, measure: Measure = guess): number {
  const text = measure(name, (upright ? 14 : 15) * scale);
  // On a computer the room's icon stands before its name (E-510): 18 px and a gap.
  return Math.round(Math.min(most, Math.max(76, text + (upright ? 22 : 34 + ROOM_ICON + 6))));
}

export interface Grouped<T> {
  devices: T[]; // the groups first, then the devices in no group of this room
  standsFor: Map<string, string>; // a device or smaller group → the group whose dot it is in
}

/** Groups go before their devices (the administrator's wish, 14-11-2026, E-515): a group stands
 * for its devices in this room as one dot, as Apparaten en entiteiten nests them (E-312). A
 * group inside a bigger one goes with the bigger one; of two that hold the same, the radio group
 * stays. Any kind of group: lamps, switches, curtains. A group without devices in this room is
 * a dot like any other. */
export function grouped<T extends { id: string; members?: string[]; radio?: boolean }>(devices: T[]): Grouped<T> {
  const byId = new Map(devices.map((d) => [d.id, d]));
  // What a group holds here, its smaller groups opened up (a group of groups).
  const leaves = (g: T, seen: Set<string> = new Set([g.id])): Set<string> => {
    const out = new Set<string>();
    for (const m of g.members ?? []) {
      const inner = byId.get(m);
      if (!inner || seen.has(m)) continue;
      out.add(m);
      if (inner.members?.length) for (const x of leaves(inner, new Set([...seen, m]))) out.add(x);
    }
    return out;
  };
  const held = new Map(devices.filter((d) => d.members?.length).map((g) => [g.id, leaves(g)]));
  const groups = [...held.keys()]
    .filter((id) => held.get(id)!.size > 0)
    .map((id) => byId.get(id)!)
    .sort((a, b) => held.get(b.id)!.size - held.get(a.id)!.size || Number(!!b.radio) - Number(!!a.radio) || a.id.localeCompare(b.id));
  const standsFor = new Map<string, string>();
  const kept: T[] = [];
  for (const g of groups) {
    const mine = held.get(g.id)!;
    const owner = kept.find((o) => [...mine].every((m) => held.get(o.id)!.has(m)));
    if (owner) {
      standsFor.set(g.id, owner.id);
      continue;
    }
    kept.push(g);
    for (const m of mine) if (!standsFor.has(m)) standsFor.set(m, g.id);
  }
  for (const [id, owner] of standsFor) {
    let top = owner;
    while (standsFor.has(top)) top = standsFor.get(top)!; // a group in a group in a group
    standsFor.set(id, top);
  }
  const loose = devices.filter((d) => !standsFor.has(d.id) && !kept.includes(d));
  return { devices: [...devices.filter((d) => kept.includes(d)), ...loose], standsFor };
}

/** The devices of a room in the drawing: at most eight, and the ones a path goes to always. */
export function shownDevices<T extends { id: string }>(devices: T[], keep: string | (string | null)[] | null = null): T[] {
  const keeps = new Set((Array.isArray(keep) ? keep : [keep]).filter((k) => k !== null));
  const shown = devices.slice(0, MAX_DEVICES);
  // A device on a path that is not among the first takes the place of the last one not kept.
  for (const kept of devices.slice(MAX_DEVICES).filter((d) => keeps.has(d.id))) {
    let drop = shown.length - 1;
    while (drop >= 0 && keeps.has(shown[drop].id)) drop--;
    if (drop < 0) break;
    shown.splice(drop, 1);
    shown.push(kept);
  }
  return shown;
}

interface Spot {
  x: number;
  y: number;
  at: number; // how far along the edge, from the top
}

/** The edge of the oval as points, from the top clockwise. ``p`` 2 is an ellipse; 4 is
 * rounder at the corners and straighter at the sides, so more rooms fit under each other on a
 * phone. */
function edge(rx: number, ry: number, p: number): Spot[] {
  const steps = 1440;
  const out: Spot[] = [];
  const pow = (v: number): number => Math.sign(v) * Math.abs(v) ** (2 / p);
  for (let i = 0; i <= steps; i++) {
    const a = -Math.PI / 2 + (i / steps) * Math.PI * 2;
    const x = rx * pow(Math.cos(a));
    const y = ry * pow(Math.sin(a));
    const at = i === 0 ? 0 : out[i - 1].at + Math.hypot(x - out[i - 1].x, y - out[i - 1].y);
    out.push({ x, y, at });
  }
  return out;
}

interface Box {
  l: number;
  r: number;
  t: number;
  b: number;
}

const apart = (a: Box, b: Box): boolean => a.r + GAP <= b.l || b.r + GAP <= a.l || a.b + GAP <= b.t || b.b + GAP <= a.t;

/** A room's box clear of Vesta's ring (a circle, so a corner may come closer). */
function clear(box: Box, ring: number): boolean {
  const x = Math.max(box.l, Math.min(0, box.r));
  const y = Math.max(box.t, Math.min(0, box.b));
  return Math.hypot(x, y) >= ring + GAP;
}

export function networkLayout(rooms: NetInput[], width: number, scale = 1, measure: Measure = guess): NetLayout {
  const W = Math.max(280, Math.round(width));
  const upright = W < 560;
  const orb = upright ? 50 : 88;
  const ring = orb / 2 + (upright ? 21 : 34);
  // Beside Vesta there must be room for a block on either side, also on the smallest phone; a
  // longer name there takes two lines.
  const most = upright ? Math.min(132, Math.floor(W / 2 - ring - GAP - MARGIN)) : 168;
  // Between two devices in a row; a full row is never wider than the widest block. On a
  // computer a device is a circle with its icon (E-510), so the row is wider.
  const spread = Math.min(upright ? 13 : 22, (most - 12) / (MAX_DEVICES - 1));
  const dot = upright ? 5 : Math.min(9, spread / 2 - 1);
  const sizes = rooms.map((room) => {
    const w = blockWidth(room.name, upright, most, scale, measure);
    const k = Math.min(room.devices.length, MAX_DEVICES);
    return { w, k, foot: Math.max(w, k ? (k - 1) * spread + 12 : 0) };
  });
  const widest = Math.max(76, ...sizes.map((s) => s.foot));
  const rxMost = Math.max(ring + GAP + widest / 2, W / 2 - widest / 2 - MARGIN);
  const n = rooms.length;

  const box = (i: number, p: Spot): Box => {
    const s = sizes[i];
    const below = p.y >= -0.5;
    const dots = s.k ? DOTS_ROOM : 0;
    return {
      l: p.x - s.foot / 2,
      r: p.x + s.foot / 2,
      t: p.y - ROOM_H / 2 - (below ? 0 : dots),
      b: p.y + ROOM_H / 2 + (below ? dots : 0),
    };
  };

  /** Each room as close after the one before as it can lie free; then, a few rounds, each
   * room moves towards the middle between its neighbours as far as it stays free – so a calm
   * house is spread evenly round Vesta and a full one still fits. */
  const place = (rx: number, ry: number): Spot[] | null => {
    const line = edge(rx, ry, upright ? 4 : 2);
    const total = line[line.length - 1].at;
    const spotAt = (at: number): Spot => {
      let lo = 0;
      let hi = line.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (line[mid].at <= at) lo = mid;
        else hi = mid - 1;
      }
      return line[lo];
    };
    const at: number[] = [];
    const boxes: Box[] = [];
    let j = 0;
    for (let i = 0; i < n; i++) {
      while (j < line.length && !(clear(box(i, line[j]), ring) && boxes.every((b) => apart(box(i, line[j]), b)))) j++;
      if (j >= line.length) return null;
      at.push(line[j].at);
      boxes.push(box(i, line[j]));
      j++;
    }
    for (let round = 0; round < 40; round++) {
      let moved = false;
      for (let i = 1; i < n; i++) {
        const next = i + 1 < n ? at[i + 1] : total;
        const goal = (at[i - 1] + next) / 2;
        for (const part of [1, 0.5, 0.25]) {
          const want = at[i] + (goal - at[i]) * part;
          if (Math.abs(want - at[i]) < 1) break;
          const mine = box(i, spotAt(want));
          if (clear(mine, ring) && boxes.every((b, m) => m === i || apart(mine, b))) {
            at[i] = want;
            boxes[i] = mine;
            moved = true;
            break;
          }
        }
      }
      if (!moved) break;
    }
    return at.map(spotAt);
  };

  // Start calm and let the oval grow taller (and wider, up to the edges) until all fit; a
  // house too big for any oval gets its rooms at equal distances on the last one.
  let ry = upright ? ring + 60 : ring + 120;
  let rx = rxMost;
  let spots: Spot[] | null = n === 0 ? [] : null;
  for (let tries = 0; spots === null && tries < 300; tries++, ry += 8) {
    rx = upright ? rxMost : Math.min(rxMost, Math.max(ring + GAP + widest / 2 + 24, ry * 1.35));
    spots = place(rx, ry);
  }
  if (spots === null) {
    const line = edge(rx, ry, upright ? 4 : 2);
    spots = rooms.map((_, i) => line[Math.round((i / n) * (line.length - 1))]);
  }
  const found = spots;
  const boxes = found.map((p, i) => box(i, p));
  // Room above and below for the name of the device the path goes to.
  const top = Math.min(-ring - 8, ...boxes.map((b) => b.t)) - LABEL;
  const bottom = Math.max(ring + 8, ...boxes.map((b) => b.b)) + LABEL;
  const cx = W / 2;
  const cy = MARGIN - top;
  const height = Math.ceil(bottom - top + MARGIN * 2);

  const out = rooms.map((room, i) => {
    const p = found[i];
    const s = sizes[i];
    const x = cx + p.x;
    const y = cy + p.y;
    const below = p.y >= -0.5;
    const edge = below ? y + ROOM_H / 2 : y - ROOM_H / 2;
    const dotY = below ? edge + DOTS_AWAY : edge - DOTS_AWAY;
    const row = (s.k - 1) * spread;
    // The row stays inside the drawing, also for a room near its edge.
    const start = Math.min(Math.max(x - row / 2, MARGIN + 6), W - MARGIN - 6 - row);
    const devices = room.devices.slice(0, s.k).map((d, j) => ({ id: d.id, x: start + j * spread, y: dotY }));
    return { id: room.id, x, y, w: s.w, below, edge, devices };
  });
  return { width: W, height, upright, cx, cy, orb, ring, dot, rooms: out };
}

/** A soft curve from one point to another, bent to one side (+1) or the other (−1): the path
 * room → Vesta → device, as two curves that do not lie on each other. */
export function curve(from: { x: number; y: number }, to: { x: number; y: number }, side: 1 | -1): string {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const bend = 0.18 * side;
  const r = (v: number): number => Math.round(v * 10) / 10;
  return `M${r(from.x)} ${r(from.y)} Q${r(mx - dy * bend)} ${r(my + dx * bend)} ${r(to.x)} ${r(to.y)}`;
}

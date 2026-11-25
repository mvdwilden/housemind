// Groups in Apparaten en entiteiten (E-312): per room the groups on top, a smaller group
// inside the bigger one that holds all its devices (the washbasin's lamps inside the whole
// bathroom), each device under the smallest group that holds it, and the rest as before.

import type { DeviceRow } from "./types";

export interface GroupTree {
  top: DeviceRow[]; // groups not inside another group of this room
  children: Map<string, DeviceRow[]>; // group id → the smaller groups inside it
  members: Map<string, DeviceRow[]>; // group id → its own devices here
  elsewhere: Map<string, string[]>; // group id → names of its devices in other rooms
  loose: DeviceRow[]; // devices in no group of this room
}

export function groupTree(list: DeviceRow[], all: DeviceRow[]): GroupTree {
  const size = (d: DeviceRow) => d.group?.members.length ?? 0;
  const radio = (d: DeviceRow) => (d.group?.radio ? 1 : 0);
  // Bigger first; of equals the radio group first (it is the one that switches them).
  const groupsHere = list
    .filter((d) => d.group)
    .sort((a, b) => size(b) - size(a) || radio(b) - radio(a) || a.id.localeCompare(b.id));
  const holds = (outer: DeviceRow, inner: DeviceRow) =>
    outer !== inner && size(outer) >= size(inner) && inner.group!.members.every((m) => outer.group!.members.includes(m));
  const tree: GroupTree = { top: [], children: new Map(), members: new Map(), elsewhere: new Map(), loose: [] };
  for (const g of groupsHere) {
    // Its parent: the smallest other group here that holds all of it (the first of equals).
    const parents = groupsHere.filter((o) => holds(o, g) && !(size(o) === size(g) && groupsHere.indexOf(o) > groupsHere.indexOf(g)));
    const parent = parents.sort((a, b) => size(a) - size(b))[0];
    if (parent) tree.children.set(parent.id, [...(tree.children.get(parent.id) ?? []), g]);
    else tree.top.push(g);
  }
  const here = new Map(list.map((d) => [d.id, d]));
  const byId = new Map(all.map((d) => [d.id, d]));
  const placed = new Set<string>();
  for (const g of [...groupsHere].sort((a, b) => size(a) - size(b))) {
    const own: DeviceRow[] = [];
    const away: string[] = [];
    for (const id of g.group!.members) {
      const row = here.get(id);
      if (row && !row.group) {
        if (!placed.has(id)) own.push(row);
        placed.add(id);
      } else if (!row) away.push(byId.get(id)?.name ?? id);
    }
    tree.members.set(g.id, own);
    if (away.length) tree.elsewhere.set(g.id, away);
  }
  tree.loose = list.filter((d) => !d.group && !placed.has(d.id));
  return tree;
}

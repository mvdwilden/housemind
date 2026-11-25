// The disk ring of Home Assistant netjes (E-451, B-365): what is on the disk, in 100 parts of 1 %.

import type { TidySystem } from "./types";

export type DiskPart = "database" | "backups" | "loose" | "other" | "free";
export const DISK_PARTS: DiskPart[] = ["database", "backups", "loose", "other", "free"];
export const BACKUP_DAYS = 7; // HouseMind tells from 7 days on (E-361)

/** The bytes per part, in the order of the ring: what is used first, what is free last. Null
 * when the disk is unknown. The old loose backups are a part of all backups on the device. */
export function diskParts(sys: TidySystem): Record<DiskPart, number> | null {
  const { total, used } = sys.disk;
  if (!total || used === undefined) return null;
  const database = Math.min(sys.database.bytes ?? 0, used);
  const loose = sys.loose.reduce((n, b) => n + b.size, 0);
  const backups = Math.max(0, (sys.disk.backups ?? 0) - loose);
  const other = Math.max(0, used - database - backups - loose);
  return { database, backups, loose, other, free: Math.max(0, total - used) };
}

/** Whole parts of the ring for these sizes, together ``n``: largest remainders, and a part
 * that is there never shrinks to nothing. */
export function ticks(sizes: number[], n = 100): number[] {
  const sum = sizes.reduce((a, b) => a + b, 0);
  if (sum <= 0) return sizes.map(() => 0);
  const exact = sizes.map((s) => (s / sum) * n);
  const out = exact.map((x, i) => (sizes[i] > 0 ? Math.max(1, Math.floor(x)) : 0));
  const order = exact.map((x, i) => ({ i, rest: x - Math.floor(x) })).sort((a, b) => b.rest - a.rest);
  let left = n - out.reduce((a, b) => a + b, 0);
  for (let k = 0; left > 0; k++) {
    const { i } = order[k % order.length];
    if (sizes[i] > 0) {
      out[i]++;
      left--;
    }
  }
  while (left < 0) {
    const i = out.indexOf(Math.max(...out));
    out[i]--;
    left++;
  }
  return out;
}

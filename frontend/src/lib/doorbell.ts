// The doorbell on a wall tablet (E-187): its camera for 30 seconds, only in kiosk mode. "Sluiten"
// closes it for that ring; ringing again shows it again.
import type { TabletView } from "./types";

export type Doorbell = NonNullable<TabletView["doorbell"]>;

export function doorbellToShow(kiosk: boolean, bell: Doorbell | null | undefined, closedAt: string | null): Doorbell | null {
  return kiosk && bell && bell.left > 0 && bell.at !== closedAt ? bell : null;
}

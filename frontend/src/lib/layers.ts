// One layer at a time (E-259, B-107). What goes first: an emergency, then the Vesta window,
// then a sheet (settings, the introduction), then the tour, then a short message. A short
// message never covers one of the others: over a sheet it shows inside the sheet, at its foot;
// over the Vesta window, the introduction or the tour it waits. The tour only starts when
// nothing else is open and no short message showed in the last 10 seconds.

export type Layer = "emergency" | "vesta" | "sheet" | "tour" | "notice";

export interface Layers {
  emergency: boolean; // an emergency in the house
  vesta: boolean; // the Vesta window is open
  sheet: boolean; // a sheet is open (settings, preferences, help, a room …)
  introduction: boolean; // the introduction is open (a sheet that leads the way)
  tour: boolean; // the tour is running
  notices: number; // short messages in the stack
}

/** The layer on top now; the ones below it wait. */
export function topLayer(state: Layers): Layer | null {
  if (state.emergency) return "emergency";
  if (state.vesta) return "vesta";
  if (state.sheet || state.introduction) return "sheet";
  if (state.tour) return "tour";
  if (state.notices > 0) return "notice";
  return null;
}

/** Where the short messages go now: on the screen, at the foot of the open sheet, or waiting. */
export function noticePlace(state: Layers): "screen" | "sheet" | "wait" {
  if (state.vesta || state.introduction || state.tour) return "wait";
  if (state.sheet) return "sheet";
  return "screen";
}

export const TOUR_QUIET_MS = 10_000;

/** May the tour start now: nothing else open, no emergency, and quiet for 10 seconds. */
export function tourMayStart(state: Layers, lastNoticeAt: number | null, now: number): boolean {
  if (state.emergency || state.vesta || state.sheet || state.introduction || state.tour || state.notices > 0) return false;
  return lastNoticeAt === null || now - lastNoticeAt >= TOUR_QUIET_MS;
}

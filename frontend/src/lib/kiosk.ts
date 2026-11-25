// Kiosk mode for a wall tablet (E-134): only HouseMind on the screen, nothing of Home
// Assistant's menu or top bar, the screen kept on.
//
// HouseMind runs in Home Assistant's App panel, in a frame of the same origin. Instead of
// reaching into Home Assistant's own (changing) page structure, the frame lays itself over
// the whole window – so it keeps working after HA updates. Leaving kiosk mode puts it back.

const KEY = "housemind.kiosk";
const SAVED = "housemindKioskStyle";
const COVER =
  "position:fixed;inset:0;width:100vw;height:100vh;max-width:none;max-height:none;" +
  "border:0;margin:0;z-index:2147483647;background:#000;";

interface Frame {
  style: { cssText: string };
  dataset: Record<string, string | undefined>;
  requestFullscreen?: () => Promise<void>;
}

export interface KioskWindow {
  frameElement: Frame | null;
  document: { documentElement: { requestFullscreen?: () => Promise<void> } };
}

/** On unless someone turned it off on this screen. */
export function kioskWanted(storage: Pick<Storage, "getItem"> | undefined): boolean {
  try {
    return storage?.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setKioskWanted(on: boolean): void {
  try {
    window.localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    // Storage blocked: kiosk stays on (the default).
  }
}

/** Lay HouseMind's frame over the whole window. False when not inside Home Assistant. */
export function cover(win: KioskWindow): boolean {
  let frame: Frame | null = null;
  try {
    frame = win.frameElement;
  } catch {
    return false; // another origin: never touch it
  }
  if (!frame) return false;
  if (frame.dataset[SAVED] === undefined) frame.dataset[SAVED] = frame.style.cssText;
  frame.style.cssText = `${frame.dataset[SAVED] ?? ""};${COVER}`;
  return true;
}

/** Give Home Assistant its menu and top bar back. */
export function uncover(win: KioskWindow): void {
  try {
    const frame = win.frameElement;
    if (frame && frame.dataset[SAVED] !== undefined) {
      frame.style.cssText = frame.dataset[SAVED] ?? "";
      delete frame.dataset[SAVED];
    }
  } catch {
    // nothing to give back
  }
}

/** Fill the whole screen (needs a tap). The frame itself first, else this page. */
export async function fullscreen(win: KioskWindow): Promise<boolean> {
  const targets = [win.frameElement, win.document.documentElement];
  for (const target of targets) {
    try {
      if (target?.requestFullscreen) {
        await target.requestFullscreen();
        return true;
      }
    } catch {
      // not allowed here: try the next
    }
  }
  return false;
}

interface WakeLock {
  request(type: "screen"): Promise<{ released: boolean; release(): Promise<void> }>;
}

/** Keep the screen on while HouseMind is shown (where the browser allows it). */
export async function keepAwake(nav: { wakeLock?: WakeLock }): Promise<(() => void) | null> {
  if (!nav.wakeLock) return null;
  try {
    const lock = await nav.wakeLock.request("screen");
    return () => void lock.release();
  } catch {
    return null;
  }
}

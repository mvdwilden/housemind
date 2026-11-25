import { describe, expect, it } from "vitest";
import { cover, fullscreen, keepAwake, kioskWanted, uncover, type KioskWindow } from "./kiosk";

function frameWindow(css = "height:100%"): KioskWindow & { frameElement: { style: { cssText: string }; dataset: Record<string, string | undefined> } } {
  return { frameElement: { style: { cssText: css }, dataset: {} }, document: { documentElement: {} } };
}

describe("kiosk mode", () => {
  it("lays the frame over the whole window and gives it back", () => {
    const win = frameWindow();
    expect(cover(win)).toBe(true);
    expect(win.frameElement.style.cssText).toContain("position:fixed");
    expect(win.frameElement.style.cssText).toContain("z-index:2147483647");
    cover(win); // twice is still once
    uncover(win);
    expect(win.frameElement.style.cssText).toBe("height:100%");
  });

  it("does nothing outside Home Assistant", () => {
    expect(cover({ frameElement: null, document: { documentElement: {} } })).toBe(false);
  });

  it("is on unless turned off on this screen", () => {
    expect(kioskWanted({ getItem: () => null })).toBe(true);
    expect(kioskWanted({ getItem: () => "off" })).toBe(false);
    expect(kioskWanted(undefined)).toBe(true);
  });

  it("fills the screen with the frame, else the page", async () => {
    const calls: string[] = [];
    const win = frameWindow() as KioskWindow;
    win.document.documentElement.requestFullscreen = async () => void calls.push("page");
    expect(await fullscreen(win)).toBe(true);
    expect(calls).toEqual(["page"]);
  });

  it("keeps the screen on where the browser allows it", async () => {
    expect(await keepAwake({})).toBeNull();
    let released = false;
    const release = await keepAwake({
      wakeLock: { request: async () => ({ released: false, release: async () => void (released = true) }) },
    });
    release?.();
    await Promise.resolve();
    expect(released).toBe(true);
  });
});

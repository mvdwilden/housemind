import { describe, expect, it } from "vitest";
import { INTEGRATIONS_PAGE, openBeside, openInHA } from "./openha";

// A window as far as openInHA looks at it.
function fakeWindows(sameOrigin: boolean, agent = "Mozilla/5.0 Firefox/130", blocked = false) {
  const moved: string[] = [];
  const events: string[] = [];
  const opened: string[] = [];
  const top = {
    location: { origin: sameOrigin ? "https://ha.local" : "https://other.example" },
    history: { pushState: (_s: unknown, _t: string, path: string) => moved.push(path) },
    dispatchEvent: (e: Event) => { events.push(e.type); return true; },
  };
  const tabs: string[] = [];
  const app = {
    top,
    navigator: { userAgent: agent },
    location: { origin: "https://ha.local" },
    open: (path: string, target: string) => {
      if (target === "_blank") {
        if (blocked) return null;
        tabs.push(path);
        return { opener: {} };
      }
      opened.push(path);
      return null;
    },
  };
  return { app: app as unknown as Window, moved, events, opened, tabs };
}

describe("opening a page of Home Assistant from the App (20.23)", () => {
  it("moves Home Assistant itself to the page, as its own links do", () => {
    const w = fakeWindows(true);
    openInHA(INTEGRATIONS_PAGE, w.app);
    expect(w.moved).toEqual(["/config/integrations/dashboard"]);
    expect(w.events).toEqual(["location-changed"]);
    expect(w.opened).toEqual([]);
  });

  it("opens it as a page when the App is not inside Home Assistant", () => {
    const w = fakeWindows(false);
    openInHA(INTEGRATIONS_PAGE, w.app);
    expect(w.moved).toEqual([]);
    expect(w.opened).toEqual(["/config/integrations/dashboard"]);
  });
});

describe("a page of Home Assistant beside the App (Home Assistant netjes)", () => {
  it("opens a new tab in a browser, so the App stays where it was", () => {
    const w = fakeWindows(true);
    openBeside("/config/entities?config_entry=abc", w.app);
    expect(w.tabs).toEqual(["/config/entities?config_entry=abc"]);
    expect(w.moved).toEqual([]);
  });

  it("moves in place in Home Assistant's own app, which has no tabs", () => {
    const w = fakeWindows(true, "Mozilla/5.0 (iPhone) Home Assistant/2026.10 (io.robbie.HomeAssistant)");
    openBeside("/config/entities", w.app);
    expect(w.tabs).toEqual([]);
    expect(w.moved).toEqual(["/config/entities"]);
  });

  it("moves in place when the browser blocks a new tab", () => {
    const w = fakeWindows(true, undefined, true);
    openBeside("/config/backup", w.app);
    expect(w.moved).toEqual(["/config/backup"]);
  });
});

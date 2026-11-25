// A page of Home Assistant itself, from inside the App (Ingress): the same window moves there,
// the way HA's own links do, without loading the whole page again (20.23).

export const INTEGRATIONS_PAGE = "/config/integrations/dashboard";

export function openInHA(path: string, win: Window = window): void {
  try {
    const top = win.top;
    if (top && top !== win && top.location.origin === win.location.origin) {
      top.history.pushState(null, "", path);
      top.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      return;
    }
  } catch {
    // Another origin around the App: open it as a page instead.
  }
  win.open(path, "_top");
}

/** An entity's own window in Home Assistant (E-544: "Naar het alarm", where the code is typed):
 *  the window HA opens itself, from inside the App; elsewhere its page in Home Assistant. */
export function openMoreInfo(entityId: string, win: Window = window): void {
  try {
    const top = win.top;
    if (top && top !== win && top.location.origin === win.location.origin) {
      const root = top.document.querySelector("home-assistant");
      if (root) {
        root.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
        return;
      }
    }
  } catch {
    // Another origin around the App: its page instead.
  }
  openInHA(`/history?entity_id=${encodeURIComponent(entityId)}`, win);
}

/** Home Assistant's own app on a phone or tablet: it has no tabs, so a page opens in place. */
export function inCompanionApp(win: Window = window): boolean {
  if (/Home ?Assistant\//.test(win.navigator?.userAgent ?? "")) return true;
  try {
    const top = win.top as (Window & { externalApp?: unknown; webkit?: { messageHandlers?: { getExternalAuth?: unknown } } }) | null;
    return Boolean(top?.externalApp || top?.webkit?.messageHandlers?.getExternalAuth);
  } catch {
    return false;
  }
}

/** A page of Home Assistant beside the App (Home Assistant netjes): in a browser a new tab, so
 *  the App stays where it was; in the companion app, or when a new tab is blocked, in place. */
export function openBeside(path: string, win: Window = window): void {
  if (!inCompanionApp(win)) {
    const opened = win.open(path, "_blank");
    if (opened) {
      opened.opener = null;
      return;
    }
  }
  openInHA(path, win);
}

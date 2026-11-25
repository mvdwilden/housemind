// A wall tablet (E-134): this one screen remembers which room it hangs in. Only this device
// knows; the server hears it in the hello and then shows the calm, shared view (E-22, E-25).

const KEY = "housemind.tablet";

export function tabletArea(): string | null {
  try {
    const value = window.localStorage.getItem(KEY);
    return value && value.length <= 64 ? value : null;
  } catch {
    return null;
  }
}

export function setTabletArea(area: string | null): void {
  try {
    if (area) window.localStorage.setItem(KEY, area);
    else window.localStorage.removeItem(KEY);
  } catch {
    // Storage blocked: the screen stays a normal screen.
  }
}

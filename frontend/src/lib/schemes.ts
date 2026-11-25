// Colour schemes as files (E-266, E-267, B-113): the server sends the schemes that passed the
// readability check; each device chooses one. "Same as my device" (auto) takes Diep or Licht.
import type { SchemeView } from "./types";

/** The app's colour variables per design-system role (tokens.json, the files' own words). */
export const VARIABLES: Record<string, string> = {
  inkt: "--ink",
  vlak: "--surface",
  verhoogd: "--raised",
  lijn: "--line",
  rand: "--edge",
  tekst: "--text",
  gedempt: "--muted",
  accent: "--accent",
  "op-accent": "--on-accent",
  nood: "--emergency",
  belangrijk: "--important",
  leren: "--learning",
};

const DEFAULTS = { dark: "diep", light: "licht" } as const;

/** The scheme for this device: its own choice when that still exists, else the one that
 * matches the device's light or dark (Diep or Licht, else the first of that base). */
export function pickScheme(schemes: SchemeView[], choice: string, prefersDark: boolean): SchemeView | null {
  const chosen = choice !== "auto" ? schemes.find((s) => s.id === choice) : undefined;
  if (chosen) return chosen;
  const base = prefersDark ? "dark" : "light";
  return schemes.find((s) => s.id === DEFAULTS[base]) ?? schemes.find((s) => s.base === base) ?? schemes[0] ?? null;
}

/** The CSS variables of a scheme, plus how strong Vesta's glow is. */
export function schemeVars(scheme: SchemeView): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const [role, variable] of Object.entries(VARIABLES)) {
    if (scheme.colours[role]) vars[variable] = scheme.colours[role];
  }
  vars["--vesta-glow"] = String(scheme.glow);
  return vars;
}

/** Where a device's choice started: the person's earlier choice (dark/light) when this device
 * never chose itself. */
export function startScheme(personTheme: string | undefined): string {
  return personTheme === "dark" ? "diep" : personTheme === "light" ? "licht" : "auto";
}

const CACHE = "housemind.scheme";

/** Remember the colours this device shows, so the next start does not flash another scheme
 * before the server's list is in. */
export function rememberScheme(vars: Record<string, string>, base: string): void {
  try {
    window.localStorage.setItem(CACHE, JSON.stringify({ vars, base }));
  } catch {
    // Storage blocked: the default colours show until the list is in.
  }
}

/** Put the remembered colours on the page at once (before the first snapshot). */
export function restoreScheme(root: HTMLElement): void {
  try {
    const saved = JSON.parse(window.localStorage.getItem(CACHE) ?? "null") as { vars: Record<string, string>; base: string } | null;
    if (!saved) return;
    for (const [variable, value] of Object.entries(saved.vars)) root.style.setProperty(variable, value);
    root.setAttribute("data-theme", saved.base);
    root.style.colorScheme = saved.base;
  } catch {
    // Nothing remembered, or storage blocked.
  }
}

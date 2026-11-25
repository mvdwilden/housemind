import { describe, expect, it } from "vitest";
import { pickScheme, schemeVars, startScheme } from "./schemes";
import type { SchemeView } from "./types";

const scheme = (id: string, base: "dark" | "light", glow = 1): SchemeView => ({
  id, name: id, hint: "", base, level: "AA", glow, own: false,
  colours: { inkt: "#000000", vlak: "#111111", tekst: "#ffffff", accent: "#5ed4c4", leren: "#f3b65b" },
});
const all = [scheme("diep", "dark"), scheme("licht", "light"), scheme("contrast", "dark"), scheme("nacht", "dark", 0.5)];

describe("colour schemes per device (E-266, E-267)", () => {
  it("follows the device with Diep or Licht unless the device chose one", () => {
    expect(pickScheme(all, "auto", true)?.id).toBe("diep");
    expect(pickScheme(all, "auto", false)?.id).toBe("licht");
    expect(pickScheme(all, "nacht", false)?.id).toBe("nacht");
  });

  it("goes back to the device's own light or dark when the chosen file is gone", () => {
    expect(pickScheme(all, "zee", false)?.id).toBe("licht");
    expect(pickScheme([scheme("bos", "dark")], "auto", false)?.id).toBe("bos");
    expect(pickScheme([], "auto", true)).toBeNull();
  });

  it("gives the app's colour variables and Vesta's glow, never her own colours", () => {
    const vars = schemeVars(all[3]);
    expect(vars["--ink"]).toBe("#000000");
    expect(vars["--learning"]).toBe("#f3b65b");
    expect(vars["--vesta-glow"]).toBe("0.5");
    expect(Object.keys(vars).some((v) => v.startsWith("--orb"))).toBe(false);
  });

  it("starts from the person's earlier choice of dark or light", () => {
    expect(startScheme("dark")).toBe("diep");
    expect(startScheme("light")).toBe("licht");
    expect(startScheme("auto")).toBe("auto");
    expect(startScheme(undefined)).toBe("auto");
  });
});

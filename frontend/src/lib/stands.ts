// Standen voor lampen (E-490 – E-496, design 73): the four standen, their start values, and
// a colour temperature as a colour on the screen (only to show it, never sent to a lamp).

export const STANDS = ["dim", "normal", "bright", "full"] as const;
export const START = [15, 35, 60, 100];

export interface StandColour {
  rhythm?: boolean;
  kelvin?: number;
  name?: string;
}

export const START_COLOURS: StandColour[] = [{ kelvin: 2200 }, { rhythm: true }, { rhythm: true }, { rhythm: true }];

const POINTS: [number, [number, number, number]][] = [
  [2000, [255, 150, 60]],
  [2200, [255, 166, 82]],
  [2700, [255, 190, 126]],
  [3000, [255, 201, 148]],
  [4000, [255, 228, 206]],
  [5000, [255, 240, 233]],
  [6500, [248, 248, 255]],
];

/** A colour temperature as a screen colour, roughly. */
export function kelvinCss(kelvin: number): string {
  for (let i = 1; i < POINTS.length; i++) {
    if (kelvin <= POINTS[i][0]) {
      const [k0, a] = POINTS[i - 1];
      const [k1, b] = POINTS[i];
      const f = Math.max(0, (kelvin - k0) / (k1 - k0));
      return `rgb(${a.map((x, j) => Math.round(x + (b[j] - x) * f)).join(", ")})`;
    }
  }
  return "rgb(248, 248, 255)";
}

/** Warm, warm white, neutral or cool white: how a colour temperature is named. */
export function tone(kelvin: number): "warm" | "warm_white" | "neutral" | "cool" {
  return kelvin < 2600 ? "warm" : kelvin < 3400 ? "warm_white" : kelvin < 4600 ? "neutral" : "cool";
}

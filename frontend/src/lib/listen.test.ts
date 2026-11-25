import { describe, expect, it } from "vitest";
import { afterListening, loudness, to16k } from "./listen";

describe("listening for speech recognition (B-83)", () => {
  it("turns 48 kHz sound into 16 kHz 16-bit", () => {
    const input = new Float32Array(4800).fill(0.5);
    const out = to16k(input, 48000);
    expect(out.length).toBe(1600);
    expect(out[0]).toBe(Math.floor(0.5 * 0x7fff));
    expect(to16k(new Float32Array([-1, -1, -1]), 48000)[0]).toBe(-0x8000);
  });

  it("knows silence from speech", () => {
    expect(loudness(new Float32Array(100))).toBe(0);
    expect(loudness(new Float32Array(100).fill(0.3))).toBeCloseTo(0.3);
  });
});

describe("a conversation with Vesta (E-240)", () => {
  it("sends what was said, and ends quietly when nobody spoke or Stop was pressed", () => {
    expect(afterListening({ cancelled: false, spoke: true })).toBe("listen_end");
    expect(afterListening({ cancelled: false, spoke: false })).toBe("listen_cancel");
    expect(afterListening({ cancelled: true, spoke: true })).toBe("listen_cancel");
  });
});

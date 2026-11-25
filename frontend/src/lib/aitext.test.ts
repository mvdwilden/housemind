import { describe, expect, it } from "vitest";
import { readable } from "./aitext";

describe("what went to an AI, readable (E-169, E-348)", () => {
  it("shows the data as indented JSON, anything else as it is", () => {
    expect(readable('Antwoord kort.\n<data>{"lamp":"lamp 3"}</data>')).toBe('{\n  "lamp": "lamp 3"\n}');
    expect(readable("not json")).toBe("not json");
  });
});

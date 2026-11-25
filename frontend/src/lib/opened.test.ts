import { describe, expect, it } from "vitest";
import { Opened } from "./opened.svelte";

// A summary inside a details element, as the browser has them.
function summaryIn(open: boolean): Event {
  const details = { open } as HTMLDetailsElement;
  const summary = { closest: () => details } as unknown as HTMLElement;
  return { currentTarget: summary, preventDefault: () => undefined } as unknown as Event;
}

describe("a group opened by hand stays open (found in the real house)", () => {
  it("keeps what was chosen by hand over the usual state", () => {
    const opened = new Opened();
    expect(opened.is("autonomous", false)).toBe(false);
    opened.flip("autonomous", summaryIn(false)); // tapped while closed: open
    expect(opened.is("autonomous", false)).toBe(true); // the list drawn again: still open
    opened.flip("autonomous", summaryIn(true)); // tapped again: closed
    expect(opened.is("autonomous", true)).toBe(false);
  });

  it("stops the browser's own toggle, so the two never undo each other", () => {
    const opened = new Opened();
    let stopped = false;
    const details = { open: false } as HTMLDetailsElement;
    const summary = { closest: () => details } as unknown as HTMLElement;
    opened.flip("x", { currentTarget: summary, preventDefault: () => (stopped = true) } as unknown as Event);
    expect(stopped).toBe(true);
    expect(opened.is("x", false)).toBe(true);
  });

  it("starts over with a new search", () => {
    const opened = new Opened();
    opened.flip("propose", summaryIn(false));
    opened.reset();
    expect(opened.is("propose", false)).toBe(false);
  });
});

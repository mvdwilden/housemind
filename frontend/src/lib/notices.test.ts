import { describe, expect, it } from "vitest";
import { addNotice, type Notice } from "./notices";

const saved = { kind: "saved" as const, text: "Saved.", ms: 5000 };

describe("the notice stack", () => {
  it("uses a showing Saved. again and starts its bar over", () => {
    let stack: Notice[] = addNotice([], saved, 1);
    stack = addNotice(stack, saved, 2);
    stack = addNotice(stack, { ...saved, text: "Done." }, 3);
    expect(stack).toHaveLength(1);
    expect(stack[0]).toMatchObject({ id: 1, text: "Done.", round: 2 });
  });

  it("shows a new Saved. once the old one is gone", () => {
    const stack = addNotice([], saved, 1);
    expect(addNotice(stack.filter((n) => n.id !== 1), saved, 2)).toMatchObject([{ id: 2, round: 0 }]);
  });

  it("stacks different messages, at most three, and never merges a question", () => {
    let stack: Notice[] = [];
    stack = addNotice(stack, { kind: "said", text: "All is quiet.", ms: 10000 }, 1);
    stack = addNotice(stack, { kind: "said", text: "All is quiet.", ms: 10000 }, 2);
    expect(stack).toHaveLength(1);
    stack = addNotice(stack, { kind: "said", text: "Lights off?", confirm: "x", ms: null }, 3);
    stack = addNotice(stack, { kind: "said", text: "Lights off?", confirm: "x", ms: null }, 4);
    stack = addNotice(stack, saved, 5);
    expect(stack.map((n) => n.id)).toEqual([3, 4, 5]);
    stack = addNotice(stack, { kind: "said", text: "Something else.", ms: 10000 }, 6);
    expect(stack.map((n) => n.id)).toEqual([3, 4, 6]); // the questions stay, "Saved." goes
  });
});

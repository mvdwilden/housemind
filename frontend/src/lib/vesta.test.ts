import { describe, expect, it } from "vitest";
import { askedTo, CLOSED, CLOSE_MS, closeDelay, doing, mayAsk, partialTo, saidTo, thumbed, TYPED_CLOSE_MS, typedTo, type VestaAsk, type VestaChoice } from "./vesta";

describe("the Vesta window, the same on every level (E-241)", () => {
  it("opens for a typed question, with your words first", () => {
    const open = typedTo(CLOSED, "Is the back door shut?");
    expect(open).toMatchObject({ open: true, phase: "thinking", typed: true });
    expect(open.turns).toEqual([{ who: "you", text: "Is the back door shut?" }]);
  });

  it("shows her answer in the window and keeps typing going in the same conversation", () => {
    let window = saidTo(typedTo(CLOSED, "Hi"), "Good evening, how can I help?");
    window = saidTo(typedTo(window, "What's your name?"), "I'm Vesta, the voice of this home.");
    expect(window.turns.map((turn) => turn.who)).toEqual(["you", "vesta", "you", "vesta"]);
    expect(closeDelay(window)).toBe(TYPED_CLOSE_MS); // time to read and type
  });

  it("puts what you said aloud before her answer, and closes soon after", () => {
    const window = saidTo({ ...CLOSED, open: true }, "It is calm.", "How are things?");
    expect(window.turns).toEqual([{ who: "you", text: "How are things?" }, { who: "vesta", text: "It is calm." }]);
    expect(closeDelay(window)).toBe(CLOSE_MS);
  });

  it("asks yes or no in the window, and stays open until it is answered", () => {
    const window = saidTo(typedTo(CLOSED, "Never turn on the hall light"), "Shall I remember that?", undefined, "t1");
    expect(window.confirm).toBe("t1");
    expect(closeDelay(window)).toBeNull();
  });

  it("keeps 👍/👎 under her why until one is given, then only her answer stays (E-298)", () => {
    const why = saidTo({ ...CLOSED, open: true }, "I turned it on because you came in.", undefined, undefined, null, undefined, false, 42);
    expect(why.turns.at(-1)?.thumbs).toBe(42);
    const given = thumbed(why, 42);
    expect(given.turns.at(-1)).toEqual({ who: "vesta", text: "I turned it on because you came in." });
    expect(thumbed(why, 7).turns.at(-1)?.thumbs).toBe(42); // another decision: unchanged
  });
});

describe("her answer as it is written (E-159)", () => {
  it("grows in one turn while she thinks, and her answer replaces it", () => {
    let window = typedTo(CLOSED, "Why was the bathroom cold?");
    window = partialTo(window, "Yesterday the");
    window = partialTo(window, "Yesterday the bathroom was");
    expect(window.phase).toBe("thinking");
    expect(window.turns).toEqual([
      { who: "you", text: "Why was the bathroom cold?" },
      { who: "vesta", text: "Yesterday the bathroom was", partial: true },
    ]);
    const done = saidTo(window, "I looked it up. Yesterday the bathroom was 16.5 degrees.", undefined, undefined, null, undefined, true);
    expect(done.turns).toEqual([
      { who: "you", text: "Why was the bathroom cold?" },
      { who: "vesta", text: "I looked it up. Yesterday the bathroom was 16.5 degrees.", ai: true },
    ]);
  });

  it("puts what you said aloud before her answer, not after what came so far", () => {
    const window = partialTo({ ...CLOSED, open: true, phase: "thinking" }, "It is");
    const done = saidTo(window, "It is calm.", "How are things?");
    expect(done.turns.map((turn) => turn.text)).toEqual(["How are things?", "It is calm."]);
  });

  it("does not open a closed window", () => {
    expect(partialTo(CLOSED, "Hello")).toBe(CLOSED);
  });
});

describe("Vesta asks what waits for an answer (E-252)", () => {
  const ask: VestaAsk = {
    key: "n7", kind: "proposal", text: "Shall I turn off the lamp in the kitchen?", actions: ["do", "not_now"],
    proposal: 3, notification: 7, follow: false,
  };
  const ready = { visible: true, busy: false, typing: false, window: CLOSED };

  it("only when you are ready: the app in view, nothing else open, not typing", () => {
    expect(mayAsk(ready)).toBe(true);
    expect(mayAsk({ ...ready, visible: false })).toBe(false);
    expect(mayAsk({ ...ready, busy: true })).toBe(false);
    expect(mayAsk({ ...ready, typing: true })).toBe(false);
    expect(mayAsk({ ...ready, window: { ...CLOSED, open: true, phase: "listening" } })).toBe(false);
    expect(mayAsk({ ...ready, window: askedTo(CLOSED, ask) })).toBe(false); // one at a time
  });

  it("never over a question or an answer you read: only the next one after your answer (E-464)", () => {
    const question = askedTo(CLOSED, ask);
    const answer = saidTo({ ...CLOSED, open: true }, "It is calm.", "How are things?");
    expect(mayAsk({ ...ready, window: question, follow: true })).toBe(false); // first this one
    expect(mayAsk({ ...ready, window: answer })).toBe(false); // the window is still open
    expect(mayAsk({ ...ready, window: answer, follow: true })).toBe(true); // you just answered
  });

  it("gives you time to read an answer: longer by the person's own choice (E-299, E-464)", () => {
    const answer = saidTo({ ...CLOSED, open: true }, "It is calm.", "How are things?");
    expect(closeDelay(answer)).toBe(CLOSE_MS);
    expect(closeDelay(answer, TYPED_CLOSE_MS, 30000)).toBe(30000);
  });

  it("opens her window with the question and its buttons, and it stays until answered", () => {
    const window = askedTo(CLOSED, ask);
    expect(window).toMatchObject({ open: true, phase: "idle", ask });
    expect(window.turns).toEqual([{ who: "vesta", text: ask.text }]);
    expect(closeDelay(window)).toBeNull();
  });

  it("continues the conversation when her window is already open", () => {
    const open = saidTo(typedTo(CLOSED, "Hi"), "Good evening.");
    expect(askedTo(open, ask).turns.map((turn) => turn.who)).toEqual(["you", "vesta", "vesta"]);
  });
});


describe("Vesta listens: her question and her choices stay while they are open (E-349)", () => {
  const ask: VestaAsk = {
    key: "p1", kind: "proposal", text: "Shall I turn off the reading lamp?",
    actions: ["do", "not_now", "why"], proposal: 1, notification: 0, follow: false,
  };
  const choices: VestaChoice[] = [
    { id: "status", text: "How is the house?", kind: "tap" },
    { id: "help", text: "Help", kind: "tap" },
  ];

  it("a question after a tap has its buttons; an answer that keeps it open keeps them", () => {
    const tapped = saidTo({ ...CLOSED, open: true }, ask.text, undefined, undefined, null, undefined, false, undefined, ask);
    expect(tapped.ask).toEqual(ask);
    expect(closeDelay(tapped)).toBeNull(); // to answer
    const why = saidTo(tapped, "Because nobody is there. Shall I turn it off?", "Why?", undefined, null, undefined, false, undefined, ask);
    expect(why.ask).toEqual(ask);
    const done = saidTo(why, "Done.", "Yes");
    expect(done.ask).toBeNull(); // answered: the buttons go
  });

  it("her choices after a tap without an AI: time to choose, and no other question meanwhile", () => {
    const window = saidTo({ ...CLOSED, open: true }, "Good evening, just choose.", undefined, undefined, null, undefined, false, undefined, null, choices);
    expect(window.choices).toEqual(choices);
    expect(closeDelay(window)).toBe(TYPED_CLOSE_MS);
    expect(mayAsk({ visible: true, busy: false, typing: false, window })).toBe(false);
    expect(saidTo(window, "It is calm.").choices).toBeNull();
    expect(askedTo(window, ask).choices).toBeNull(); // a question of hers comes in their place
  });
});

describe("next to her name, in words, what Vesta does now (E-269)", () => {
  it("says what her Orb shows: thinking, learning, a pause, the night", () => {
    expect(doing("thinking", "thinking", false)).toBe("thinking");
    expect(doing("learning", "idle", false)).toBe("learning");
    expect(doing("paused", "idle", false)).toBe("paused");
    expect(doing("sleeping", "idle", false)).toBe("sleeping");
    expect(doing("calm", "idle", false)).toBe("calm");
  });

  it("says when she listens and when she speaks", () => {
    expect(doing("calm", "listening", false)).toBe("listening");
    expect(doing("learning", "idle", true)).toBe("speaking");
    expect(doing("calm", "speaking", false)).toBe("speaking");
  });

  it("always says an emergency first, also while she listens or speaks", () => {
    expect(doing("emergency", "listening", false)).toBe("emergency");
    expect(doing("emergency", "idle", true)).toBe("emergency");
  });

  it("says calm for a state it does not know", () => {
    expect(doing("something-new", "idle", false)).toBe("calm");
  });
});

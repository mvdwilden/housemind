// A spoken answer in the introduction (E-253): the one to use now, or none.

export interface Heard {
  step: string;
  value: number | boolean | null;
  heard: string | null;
}

/** Only a new answer, said at this step. Found in the real house: the introduction again (and
 * the one after the factory settings) skipped its first step, because the answer said aloud in
 * the earlier introduction ("niveau twee") was still there and counted once more. */
export function answerNow(heard: Heard | null, used: unknown, atStart: Heard | null, step: string): Heard | null {
  if (!heard || heard === used || heard === atStart) return null;
  return heard.step === step && heard.value !== null ? heard : null;
}

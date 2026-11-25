// Setting up as one row of pages (E-395): the introduction's questions, the house questions,
// then the link, Vesta, the AI and Klaar – one count over all of them, and Volgende only
// once a page has its answer.

/** The pages after the introduction's questions: the house questions, link, Vesta, AI, Klaar. */
export const PAGES_AFTER_INTRO = 5;

export function wizardTotal(introCount: number): number {
  return introCount + PAGES_AFTER_INTRO;
}

/** Which page this is, counted from 1: a question of the introduction while it shows, the
 * house questions after it, then the steps link (1) to finish (4). */
export function wizardPage(at: string, introOpen: boolean, introIndex: number, introCount: number, stepIndex: number): number {
  if (at === "intro") return introOpen ? introIndex + 1 : introCount + 1;
  return introCount + 1 + stepIndex;
}

/** A page of the introduction has its answer: the level always (one is chosen already), the
 * others once chosen now – or, in the introduction once more, the answer of before. */
/** The introduction pages: the level, the emergency question on Ontzorgd, learning and pets in
 * the house questions, and – setting up on Expert – the list of parts (E-401). */
export function introSteps(level: number, house: boolean, setup: boolean): string[] {
  const steps = [
    "level",
    level === 1 ? "emergency" : null,
    house ? "learning" : null,
    house ? "pets" : null,
    setup && level >= 4 ? "parts" : null,
  ];
  return steps.filter((s): s is string => s !== null);
}

export function introAnswered(
  step: string,
  chosen: Record<string, boolean>,
  before: { again: boolean; emergencyPush?: boolean | null; consentLearning?: boolean | null; hasPets: boolean | null },
): boolean {
  if (step === "level" || step === "parts") return true; // the parts have a default (E-401)
  if (step === "pets") return before.hasPets !== null;
  if (step in chosen) return true;
  if (!before.again) return false;
  const was = step === "emergency" ? before.emergencyPush : step === "learning" ? before.consentLearning : null;
  return was !== null && was !== undefined;
}

/** On the last page (E-280, E-395): Vesta's own Assist pipeline is not there yet while the link
 * works – the question with its button, once more. Found in the real house: Home Assistant was
 * still restarting at Naam en stem, so the button was not there yet, and it was passed. */
export function pipelineStillOpen(info: { contact?: string; pipeline?: { state: string } } | undefined): boolean {
  if (!info || info.contact !== "ok" || !info.pipeline) return false;
  return info.pipeline.state === "none" || info.pipeline.state === "other";
}

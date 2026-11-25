// Time to talk and to read (E-299, 20.20): one choice per person – Normaal, Langer or Lang –
// that goes with them to every device. "Lang" keeps short messages until you tap them away.

export type TimingChoice = "normal" | "longer" | "long";

export interface Timing {
  listenMs: number; // Vesta listens this long (silence ends it sooner)
  noticeMs: number | null; // a short message; null: until it is tapped away
  savedMs: number | null; // a longer message told after an action, with Oké
  briefMs: number | null; // "Bewaard" as plain text in the header (E-174): 2 seconds
  typedCloseMs: number; // her window after a typed answer
  answerCloseMs: number; // her window after a spoken answer (E-464)
}

export const TIMINGS: Record<TimingChoice, Timing> = {
  normal: { listenMs: 6000, noticeMs: 10000, savedMs: 5000, briefMs: 2000, typedCloseMs: 60000, answerCloseMs: 15000 },
  longer: { listenMs: 12000, noticeMs: 20000, savedMs: 10000, briefMs: 4000, typedCloseMs: 120000, answerCloseMs: 30000 },
  long: { listenMs: 20000, noticeMs: null, savedMs: null, briefMs: null, typedCloseMs: 300000, answerCloseMs: 300000 },
};

export function timing(choice: string | null | undefined): Timing {
  return TIMINGS[(choice ?? "normal") as TimingChoice] ?? TIMINGS.normal;
}

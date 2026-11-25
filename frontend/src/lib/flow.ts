// One decision as a stream (E-434, design 52): saw → thought → did. A line that flows is done,
// dots that wait are for you to decide, a line that stops was not done – with the reason.
import type { T } from "./ui";

export type FlowKind = "done" | "wait" | "stop";
export interface FlowStep {
  label: string;
  text: string;
  mono?: boolean; // the label is data, such as a time
}
interface Decided {
  at?: string;
  outcome: string;
  text: string;
  saw?: string[];
  thought?: string;
  did?: string;
  cause?: string;
  effect?: string;
}

export function flowKind(outcome: string): FlowKind {
  return outcome === "executed" ? "done" : outcome === "proposed" ? "wait" : "stop";
}

/** Betrokken and up, in the Logboek (E-437): what it saw, what it thought, and how it ended. */
export function storySteps(item: Decided, t: T): FlowStep[] {
  const steps: FlowStep[] = [];
  if (item.saw?.length) steps.push({ label: t("ui.mind.saw"), text: item.saw.join(" · ") });
  if (item.thought) steps.push({ label: t("ui.mind.thought"), text: item.thought });
  const kind = flowKind(item.outcome);
  if (kind === "done") steps.push({ label: t("ui.mind.did"), text: item.did ?? item.text });
  else if (kind === "wait") steps.push({ label: t("ui.flow.you_decide"), text: item.effect ?? item.did ?? item.text });
  else steps.push({ label: t("ui.flow.not_done"), text: item.text });
  return steps;
}

/** Geïnformeerd, on Huis (E-436): cause → effect; null when the line has no such pair. */
export function causeEffect(item: Decided, t: T): FlowStep[] | null {
  if (!item.cause || !item.effect) return null;
  const kind = flowKind(item.outcome);
  const end = kind === "done" ? "ui.flow.did" : kind === "wait" ? "ui.flow.asked" : "ui.flow.not_done";
  return [
    { label: item.at ?? "", text: item.cause, mono: true },
    { label: t(end), text: item.effect },
  ];
}

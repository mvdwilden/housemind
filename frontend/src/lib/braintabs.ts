// Brein in tabjes (E-398, B-315 – B-316): one part at a time instead of everything below each
// other. Which tabs a level has, which one opens, and the short count on a tab.
import type { ReadyPart, RuleView } from "./types";

export type BrainTab = "readiness" | "rules" | "rhythm" | "knowledge" | "devices";

const INVOLVED: BrainTab[] = ["readiness", "rules", "rhythm"];
const EXPERT: BrainTab[] = ["devices"];

/** The tabs in their order: Involved has three (Ritme only when there is a rhythm) and, for an
 * administrator, Wat ik weet (E-524); Expert also Apparaten. 'Wat ik nu zou doen' is gone: Straks
 * under Ritme says what HouseMind will do, Test het huis what it would do now. The decision
 * trail and the stream of thoughts went too (QA 3): the Logboek has every decision. */
export function brainTabs(level: number, rhythm: boolean, admin = false): BrainTab[] {
  const tabs: BrainTab[] = [...INVOLVED.filter((t) => t !== "rhythm" || rhythm), ...(admin ? (["knowledge"] as BrainTab[]) : [])];
  return level >= 4 ? [...tabs, ...EXPERT] : tabs;
}

/** The tab that opens: the one asked for or kept for this person, if this level has it;
 * otherwise the first. */
export function openTab(wanted: string | null | undefined, tabs: BrainTab[]): BrainTab {
  return tabs.find((t) => t === wanted) ?? tabs[0] ?? "readiness";
}

/** The tab a jump to a part of Brein lands on: "Laat zien" from Vesta, the Help or the
 * readiness of one part ("ready-lighting"). */
export function tabFor(section: string): BrainTab | null {
  if (section === "readiness" || section.startsWith("ready-")) return "readiness";
  if (section === "rules") return "rules";
  if (section === "knowledge" || section.startsWith("knowledge:")) return "knowledge"; // E-524
  return null;
}

/** The short count on a tab: how many skills per room are on Autonomous of all, how many
 * house rules there are, and what waits in Wat ik weet (E-524). Nothing where a number says
 * little. */
export function tabCount(tab: BrainTab, readiness: ReadyPart[] = [], rules: RuleView[] = [], waiting = 0): string {
  if (tab === "knowledge") return waiting ? String(waiting) : "";
  if (tab === "readiness") {
    let on = 0;
    let all = 0;
    for (const part of readiness) {
      for (const [step, n] of Object.entries(part.counts)) {
        all += n;
        if (step === "autonomous") on += n;
      }
    }
    return all ? `${on}/${all}` : "";
  }
  if (tab === "rules") return rules.length ? String(rules.length) : "";
  return "";
}

/** Arrow keys move along the tabs, Home and End to the first and the last (WAI-ARIA tabs). */
export function nextTab(tabs: BrainTab[], current: BrainTab, key: string): BrainTab | null {
  const i = tabs.indexOf(current);
  if (key === "ArrowRight") return tabs[(i + 1) % tabs.length];
  if (key === "ArrowLeft") return tabs[(i - 1 + tabs.length) % tabs.length];
  if (key === "Home") return tabs[0];
  if (key === "End") return tabs[tabs.length - 1];
  return null;
}

// Learning from the history in Home Assistant (E-257, B-101): how the reading stands.
import type { T } from "./ui";

export interface HistoryView {
  state: "idle" | "reading" | "done" | "off" | "failed";
  progress: number; // 0..1 while reading
  days: number; // days read
  available: number | null; // days Home Assistant keeps
  readAt: string | null;
  stalled?: boolean; // the reading stands still: Klaar can after all (E-396)
}

/** The line under "Leren uit de historie": reading, read, nothing yet, failed – or nothing. */
export function historyLine(view: HistoryView | undefined, t: T, locale = "nl"): string {
  if (!view) return "";
  if (view.state === "reading") return t("ui.history.reading", { days: view.available ?? view.days });
  if (view.state === "failed") return t("ui.history.failed");
  if (view.state === "done" && view.readAt) {
    if (!view.days) return t("ui.history.none");
    const when = new Date(view.readAt).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
    return t("ui.history.read", { when, days: view.days });
  }
  return "";
}

/** The width of the progress bar, 0–100, never backwards past the end. */
export function progressWidth(view: HistoryView | undefined): number {
  return view ? Math.max(0, Math.min(100, Math.round(view.progress * 100))) : 0;
}

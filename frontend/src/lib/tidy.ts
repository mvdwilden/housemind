// The lines under a card of Home Assistant netjes (E-352 – E-354, B-245): what is wrong, what
// depends on it, and whether HouseMind itself uses it.
import type { TidyItem, TidyKeep } from "./types";
import type { T } from "./ui";

/** The title of a card: a count where the kind has one (B-258, B-265 – B-267). */
export function tidyTitle(item: TidyItem, t: T): string {
  const key = `ui.tidy.kind.${item.kind}`;
  if (item.kind === "energy_issues") return t(key, {}, item.issues?.length ?? 0);
  return t(key, { name: item.name, count: item.followsCount });
}

const ISSUES = [
  "entity_not_defined", "statistics_not_defined", "entity_unavailable", "entity_state_non_numeric",
  "entity_negative_state", "entity_unexpected_unit_energy", "entity_unexpected_unit_gas",
  "entity_unexpected_unit_water", "entity_unexpected_state_class", "entity_unexpected_device_class",
  "recorder_untracked",
];

export function tidyLines(item: TidyItem, t: T): string[] {
  const lines: string[] = [];
  if (item.kind === "meter_still" && item.meter) {
    const value = [item.meter.value, item.meter.unit].filter(Boolean).join(" ");
    lines.push(t("ui.tidy.what.meter_still", { role: t(`ui.tidy.role.${item.meter.role}`), value }));
    if (item.meter.expected_wh) lines.push(t("ui.tidy.expected", { kwh: (item.meter.expected_wh / 1000).toFixed(1) }));
    lines.push(t("ui.tidy.used_meter"));
    return lines;
  }
  if (item.kind === "energy_no_grid") {
    lines.push(t("ui.tidy.what.energy_no_grid"));
    const found = item.candidates ?? [];
    for (const c of found) lines.push(t("ui.tidy.like", { name: `${c.name} (${c.entityId})` }));
    if (!found.length) lines.push(t("ui.tidy.no_grid_none"));
    return lines;
  }
  if (item.kind === "energy_issues") {
    lines.push(t("ui.tidy.what.energy_issues"));
    for (const i of item.issues ?? []) {
      const problem = ISSUES.includes(i.type) ? t(`ui.tidy.issue.${i.type}`) : t("ui.tidy.issue.other", { type: i.type });
      lines.push(t("ui.tidy.issue_line", { name: i.name || i.entityId, problem }));
    }
    return lines;
  }
  if (item.kind === "integration_down") {
    lines.push(t("ui.tidy.what.integration_down", { state: t(`ui.tidy.state.${item.state ?? "not_loaded"}`) }));
  } else if (item.kind === "duplicate") {
    const other = item.suggest ? `${item.suggest.name} (${item.suggest.entityId})` : "";
    lines.push(t("ui.tidy.what.duplicate", { name: other }));
  } else if (item.kind === "missing_ref") {
    const missing = item.missing ?? [];
    lines.push(t("ui.tidy.what.missing_ref", { ids: missing.slice(0, 3).join(", "), count: missing.length }, missing.length));
  } else {
    lines.push(t(`ui.tidy.what.${item.kind}`));
  }
  if (item.dependsCount > 0) lines.push(t("ui.tidy.depends", { count: item.dependsCount }, item.dependsCount));
  if (item.used > 0) {
    lines.push(item.followsCount > 0 ? t("ui.tidy.used", { count: item.used }, item.used) : t("ui.tidy.used_self"));
  }
  return lines;
}

/** A size the way Home Assistant shows it: GB from 1 GB on, else MB (B-241). */
export function size(bytes: number, lang: string): string {
  const gib = bytes / 2 ** 30;
  if (gib >= 1) return `${gib.toLocaleString(lang, { maximumFractionDigits: 1 })} GB`;
  return `${Math.round(bytes / 2 ** 20).toLocaleString(lang)} MB`;
}

/** 'every 2 seconds', '5 minutes' or '3 hours': how often an entity changes. */
export function every(seconds: number, t: T, lang: string): string {
  const n = (value: number) => value.toLocaleString(lang, { maximumFractionDigits: value < 10 ? 1 : 0 });
  if (seconds < 90) return t("ui.tidy.system.seconds", { n: n(seconds) });
  if (seconds < 90 * 60) return t("ui.tidy.system.minutes", { n: n(seconds / 60) });
  return t("ui.tidy.system.hours", { n: n(seconds / 3600) });
}

/** The line under an automation that has not run for a long time (B-240). */
export function unusedLine(item: TidyItem, t: T): string {
  const days = item.days ?? 0;
  if (item.kind === "unused_off") return t("ui.tidy.unused.off", { count: days });
  if (item.never) return t("ui.tidy.unused.never", { count: days });
  return t("ui.tidy.unused.on", { count: days }, days);
}

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

/** 'Home Assistant maakt elke dag een back-up en bewaart de laatste 3.' (E-397) */
export function keepLine(keep: TidyKeep, t: T, lang: string): string {
  if (keep.recurrence === "never") return t("ui.tidy.system.keep_never");
  const names = DAYS.filter((d) => keep.days.includes(d)).map((d) =>
    new Date(Date.UTC(2024, 0, 1 + DAYS.indexOf(d))).toLocaleDateString(lang, { weekday: "long", timeZone: "UTC" }),
  );
  const when =
    keep.recurrence === "daily" || !names.length
      ? t("ui.tidy.system.keep_when_daily")
      : t("ui.tidy.system.keep_when_days", { days: new Intl.ListFormat(lang, { type: "conjunction" }).format(names) });
  const what = keep.copies
    ? t("ui.tidy.system.keep_copies", { count: keep.copies }, keep.copies)
    : keep.keepDays
      ? t("ui.tidy.system.keep_days", { count: keep.keepDays }, keep.keepDays)
      : t("ui.tidy.system.keep_all");
  return t("ui.tidy.system.keep_line", { when, what });
}

const AFTER = ["m30", "h2", "h6", "h24"];

/** 'Nagekeken na 30 minuten, 2 uur en 6 uur' (B-242). */
export function checkedAfter(done: number, t: T, lang: string): string {
  const times = AFTER.slice(0, done).map((k) => t(`ui.tidy.update.after.${k}`));
  return t("ui.tidy.update.checked", { times: new Intl.ListFormat(lang, { type: "conjunction" }).format(times) });
}

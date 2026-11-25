<script lang="ts">
  // The system as a ring (E-451, B-365), in the style of the day clock: outside the disk in 100
  // parts of 1 % – the database, the backups, the old loose backups, the rest and what is free –
  // with marks where HouseMind tells (85 % and 95 %); inside the days since the newest backup,
  // of 7. Touching a part says it in words; the legend says it all.
  import { BACKUP_DAYS, DISK_PARTS, diskParts, ticks, type DiskPart } from "../lib/systemring";
  import { size } from "../lib/tidy";
  import type { TidySystem } from "../lib/types";
  import type { T } from "../lib/ui";

  let { sys, lang, t }: { sys: TidySystem; lang: string; t: T } = $props();
  const C = 160;
  const at = (r: number, a: number) => [C + r * Math.sin(a), C - r * Math.cos(a)];
  const turn = (share: number) => share * 2 * Math.PI;
  const percent = (share: number) => share.toLocaleString(lang, { style: "percent", maximumFractionDigits: 0 });

  function arc(r1: number, r2: number, from: number, to: number): string {
    const [a1, a2] = [turn(from), turn(to)];
    const [x1, y1] = at(r2, a1);
    const [x2, y2] = at(r2, a2);
    const [x3, y3] = at(r1, a2);
    const [x4, y4] = at(r1, a1);
    return `M${x1} ${y1} A${r2} ${r2} 0 0 1 ${x2} ${y2} L${x3} ${y3} A${r1} ${r1} 0 0 0 ${x4} ${y4} Z`;
  }

  const parts = $derived(diskParts(sys));
  const total = $derived(sys.disk.total ?? 0);
  // Which part each of the 100 pieces shows, in the order of the ring.
  const pieces = $derived.by(() => {
    if (!parts) return [] as DiskPart[];
    const counts = ticks(DISK_PARTS.map((p) => parts[p]));
    return DISK_PARTS.flatMap((p, i) => Array<DiskPart>(counts[i]).fill(p));
  });
  const days = $derived(sys.backup.days);
  const old = $derived(sys.backup.state === "old");

  function partLine(part: DiskPart): string {
    const bytes = parts?.[part] ?? 0;
    return t("ui.tidy.system.ring.part_line", {
      part: t(`ui.tidy.system.ring.part.${part}`), size: size(bytes, lang), pct: percent(total ? bytes / total : 0),
    });
  }
  const backupLine = $derived(
    days === undefined ? "" : t("ui.tidy.system.ring.backup_line", {
      age: days === 0 ? t("ui.tidy.system.ring.age_today") : t("ui.tidy.system.ring.age", { count: days }, days),
    }),
  );

  let picked = $state<string | null>(null);
  function pick(event: PointerEvent): void {
    const box = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * 348 - 14 - C;
    const y = ((event.clientY - box.top) / box.height) * 332 - 6 - C;
    const d = Math.hypot(x, y);
    let a = Math.atan2(x, -y);
    if (a < 0) a += 2 * Math.PI;
    const share = a / (2 * Math.PI);
    if (d >= 114 && d <= 150 && pieces.length) picked = partLine(pieces[Math.min(99, Math.floor(share * 100))]);
    else if (d >= 80 && d <= 112 && days !== undefined) picked = backupLine;
  }
</script>

{#if parts}
  <div class="ring">
    <svg viewBox="-14 -6 348 332" role="img" aria-label={t("ui.tidy.system.ring.title")} onpointermove={pick} onpointerdown={pick}>
      {#each pieces as part, i (i)}
        <path d={arc(118, 146, (i + 0.08) / 100, (i + 0.92) / 100)} class="part {part}" />
      {/each}
      {#each [0.85, 0.95] as mark (mark)}
        {@const [x1, y1] = at(149, turn(mark))}
        {@const [x2, y2] = at(157, turn(mark))}
        {@const [lx, ly] = at(168, turn(mark))}
        <line {x1} {y1} {x2} {y2} class="mark" />
        <text x={lx} y={ly + 4} class="small">{percent(mark)}</text>
      {/each}
      {#if days !== undefined}
        {#each Array(BACKUP_DAYS) as _, i (i)}
          <path d={arc(84, 108, (i + 0.04) / BACKUP_DAYS, (i + 0.96) / BACKUP_DAYS)} class="day" class:age={i < days} class:old={old && i < days} />
        {/each}
      {/if}
      <circle cx={C} cy={C} r="70" class="middle" />
      <text x={C} y={C + 8} class="big">{percent(sys.disk.share ?? 0)}</text>
      <text x={C} y={C + 30} class="small">{t("ui.tidy.system.ring.full")}</text>
    </svg>
    <div class="side">
      <p class="line" aria-live="polite">{picked ?? t("ui.tidy.system.ring.hint")}</p>
      <ul class="legend">
        {#each DISK_PARTS.filter((p) => parts[p] > 0) as part (part)}
          <li><i class={part}></i>{partLine(part)}</li>
        {/each}
        {#if days !== undefined}<li><i class="day age" class:old></i>{t("ui.tidy.system.ring.days")}</li>{/if}
      </ul>
    </div>
  </div>
{/if}

<style>
  .ring { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; }
  svg { width: 300px; max-width: 100%; height: auto; touch-action: none; }
  .side { flex: 1; min-width: 200px; display: flex; flex-direction: column; gap: 8px; }
  .line { margin: 0; min-height: 2.6em; color: var(--text); font-variant-numeric: tabular-nums; }
  .part.database, .legend i.database { fill: #9c8cff; background: #9c8cff; }
  .part.backups, .legend i.backups { fill: var(--accent); background: var(--accent); }
  .part.loose, .legend i.loose { fill: #f3b65b; background: #f3b65b; }
  .part.other, .legend i.other { fill: var(--muted); background: var(--muted); opacity: 0.55; }
  .part.free, .legend i.free { fill: var(--line); background: var(--line); }
  .day { fill: var(--line); }
  .day.age, .legend i.day { fill: var(--accent); background: var(--accent); }
  .day.old, .legend i.day.old { fill: #ff9150; background: #ff9150; }
  .mark { stroke: var(--muted); stroke-width: 2; }
  .middle { fill: var(--surface); stroke: var(--line); }
  .small, .big { text-anchor: middle; fill: var(--muted); font-size: 12px; }
  .big { fill: var(--text); font-size: 26px; }
  .legend { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .legend i { display: inline-block; width: 12px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: middle; }
</style>

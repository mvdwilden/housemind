<script lang="ts">
  // The day clock (E-346, B-223, B-253): the coming 24 hours as a clock. Outside the price per
  // quarter (blue when negative, orange when dear), in the middle the sun, inside what HouseMind
  // does; the now and how full the battery is in the centre (measured: HouseMind does not steer
  // it, E-363). Touching a quarter says it in words; the sentence below says it all.
  import { kw, price, priceBand } from "../lib/energyplan";
  import type { EnergyClock } from "../lib/types";
  import type { T } from "../lib/ui";

  let { clock, currency, t }: { clock: EnergyClock; currency: string; t: T } = $props();
  const C = 160;
  const prices = $derived(clock.points.map((p) => p.price));
  const maxSun = $derived(Math.max(1, ...clock.points.map((p) => p.solar)));
  const angle = (i: number) => (((clock.start + i * 15) % 1440) / 1440) * 2 * Math.PI;
  const at = (r: number, a: number) => [C + r * Math.sin(a), C - r * Math.cos(a)];

  function arc(r1: number, r2: number, i: number, j: number): string {
    const [a1, a2] = [angle(i), angle(i) + ((j - i) / 96) * 2 * Math.PI];
    const [x1, y1] = at(r2, a1);
    const [x2, y2] = at(r2, a2);
    const [x3, y3] = at(r1, a2);
    const [x4, y4] = at(r1, a1);
    return `M${x1} ${y1} A${r2} ${r2} 0 0 1 ${x2} ${y2} L${x3} ${y3} A${r1} ${r1} 0 0 0 ${x4} ${y4} Z`;
  }

  let picked = $state<number | null>(null);
  function pick(event: PointerEvent): void {
    const box = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * 348 - 14 - C;
    const y = ((event.clientY - box.top) / box.height) * 332 - 6 - C;
    const d = Math.hypot(x, y);
    if (d < 54 || d > 150) return;
    let a = Math.atan2(x, -y);
    if (a < 0) a += 2 * Math.PI;
    const minute = (a / (2 * Math.PI)) * 1440;
    picked = Math.floor((((minute - clock.start + 1440) % 1440) / 15)) % clock.points.length;
  }
  const line = $derived.by(() => {
    if (picked === null) return t("ui.energy.clock.hint");
    const p = clock.points[picked];
    const parts = [p.at];
    if (p.price !== null) parts.push(t("ui.energy.per_kwh", { price: price(p.price, currency, t) }));
    parts.push(t("ui.energy.readout.solar", { kw: kw(p.solar, t) }));
    if (p.doing) parts.push(t(`ui.energy.block.${p.doing}`));
    return parts.join(" · ");
  });
  const hand = $derived(at(150, angle(0)));
</script>

<div class="clock">
  <svg viewBox="-14 -6 348 332" role="img" aria-label={t("ui.energy.clock.title")} onpointermove={pick} onpointerdown={pick}>
    {#each clock.points as p, i (i)}
      {@const band = priceBand(p.price, prices)}
      {#if band}<path d={arc(118, 146, i + 0.06, i + 0.94)} class="band {band}" />{/if}
      {#if p.solar > 1}<path d={arc(84, 84 + (p.solar / maxSun) * 30, i, i + 1)} class="sun" />{/if}
      {#if p.doing}<path d={arc(62, 78, i, i + 1)} class="doing {p.doing}" />{/if}
    {/each}
    <circle cx={C} cy={C} r="84" class="ring" />
    {#each [0, 6, 12, 18] as h (h)}
      {@const [x, y] = at(157, (h / 24) * 2 * Math.PI)}
      <text {x} y={y + 4} class="hour">{String(h).padStart(2, "0")}</text>
    {/each}
    <line x1={C} y1={C} x2={hand[0]} y2={hand[1]} class="hand" />
    {#if picked !== null}
      {@const [px, py] = at(150, angle(picked) + Math.PI / 96)}
      <line x1={C} y1={C} x2={px} y2={py} class="hand picked" />
    {/if}
    <circle cx={C} cy={C} r="54" class="middle" />
    <text x={C} y={C - 16} class="small">{clock.now}</text>
    {#if clock.soc !== null}<text x={C} y={C + 10} class="big">{clock.soc}%</text>{/if}
    {#if clock.doing}<text x={C} y={clock.soc !== null ? C + 30 : C + 6} class="small">{clock.doing}</text>{/if}
  </svg>
  <div class="side">
    <p class="line" aria-live="polite">{line}</p>
    <ul class="legend">
      <li><i class="negative"></i>{t("ui.energy.legend.negative")}</li>
      <li><i class="peak"></i>{t("ui.energy.legend.expensive")}</li>
      <li><i class="sun"></i>{t("ui.energy.legend.solar")}</li>
      <li><i class="heat"></i>{t("ui.energy.block.heat")}</li>
      <li><i class="limit"></i>{t("ui.energy.block.limit")}</li>
    </ul>
  </div>
</div>
{#if clock.summary}<p class="summary">{clock.summary}</p>{/if}

<style>
  .clock { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; }
  svg { width: 300px; max-width: 100%; height: auto; touch-action: none; }
  .side { flex: 1; min-width: 200px; display: flex; flex-direction: column; gap: 8px; }
  .line { margin: 0; min-height: 2.6em; color: var(--text); font-variant-numeric: tabular-nums; }
  .band.negative, .legend i.negative { fill: #3d8bfd; background: #3d8bfd; }
  .band.low { fill: var(--line); }
  .band.mid { fill: var(--muted); opacity: 0.55; }
  .band.high { fill: #ff9150; }
  .band.peak, .legend i.peak { fill: #e8590c; background: #e8590c; }
  .sun, .legend i.sun { fill: #f3b65b; background: #f3b65b; }
  .doing.heat, .doing.preheat, .legend i.heat { fill: #ff9150; background: #ff9150; }
  .doing.limit, .legend i.limit { fill: #74c0fc; background: #74c0fc; }
  .doing.start { fill: #f3b65b; }
  .ring { fill: none; stroke: var(--line); }
  .hand { stroke: var(--muted); stroke-width: 1.5; }
  .hand.picked { stroke: var(--text); }
  .middle { fill: var(--surface); stroke: var(--line); }
  .hour, .small, .big { text-anchor: middle; fill: var(--muted); font-size: 12px; }
  .big { fill: var(--text); font-size: 26px; }
  .legend { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .legend i { display: inline-block; width: 12px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: middle; }
  .summary { margin: 4px 0 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
</style>

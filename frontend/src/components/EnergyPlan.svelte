<script lang="ts">
  // The plan in one chart (E-346, B-224, B-225, B-253), on one time axis: the price per point
  // (blue when negative, below the line), power in above the line and out below it, and a
  // timeline of what HouseMind does per device. Before now what was measured today, lighter,
  // with the home battery and the car; after now they are not planned (E-363). Stretches with
  // a negative price are light blue all the way down. Touching shows that moment in words.
  import { flows, hourTicks, negativeRuns, nowIndex, price, priceBand, readout, timelineRows, total, type Flow } from "../lib/energyplan";
  import type { EnergyBlock, EnergyPoint } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    points: EnergyPoint[];
    blocks: EnergyBlock[];
    measuring: boolean;
    expert: boolean;
    currency: string;
    t: T;
  }
  let { points, blocks, measuring, expert, currency, t }: Props = $props();
  const UP: Flow[] = ["solar", "from_battery", "from_grid"];
  const DOWN: Flow[] = ["house", "to_battery", "boiler", "car", "to_grid"];

  let width = $state(720);
  const L = 44;
  const R = 8;
  const dx = $derived((width - L - R) / Math.max(1, points.length));
  const x = (i: number) => L + i * dx;

  const prices = $derived(points.map((p) => p.price));
  const known = $derived(prices.filter((p): p is number => p !== null));
  const lo = $derived(Math.min(0, ...known, ...(expert ? points.map((p) => p.sell ?? 0) : [])));
  const hi = $derived(Math.max(0.01, ...known));
  const P = { top: 22, h: 40 };
  const yp = (v: number) => P.top + P.h - ((v - lo) / (hi - lo || 1)) * P.h;

  const F = { top: 78, h: 172 };
  const mid = F.top + F.h / 2;
  const maxW = $derived(
    Math.max(1000, ...points.map((p) => Math.max(total(flows(p).up), total(flows(p).down), p.solarHigh ?? 0))),
  );
  const sc = (w: number) => (Math.max(w, 0) / maxW) * (F.h / 2);

  const rows = $derived(timelineRows(blocks));
  const T0 = F.top + F.h + 24;
  const past = $derived(nowIndex(points));
  const H = $derived(T0 + rows.length * 24 + 22);
  const negative = $derived(negativeRuns(points));
  const ticks = $derived(hourTicks(points, width < 480 ? 4 : 8));
  const low = $derived(known.length ? prices.indexOf(Math.min(...known)) : -1);
  const high = $derived(known.length ? prices.indexOf(Math.max(...known)) : -1);

  function marginPath(): string {
    if (!expert || !points.some((p) => p.solarHigh !== undefined)) return "";
    const top = points.map((p, i) => `${i ? "L" : "M"} ${x(i) + dx / 2} ${mid - sc(p.solarHigh ?? p.solar)}`).join(" ");
    const bottom = [...points].reverse().map((p, k) => `L ${x(points.length - 1 - k) + dx / 2} ${mid - sc(p.solarLow ?? p.solar)}`).join(" ");
    return `${top} ${bottom} Z`;
  }
  function sellPath(): string {
    if (!expert || !points.some((p) => p.sell != null && Math.abs(p.sell - (p.price ?? 0)) > 0.0005)) return "";
    return points.map((p, i) => (p.sell == null ? "" : `${i ? "L" : "M"} ${x(i) + dx / 2} ${yp(p.sell)}`)).join(" ");
  }

  let picked = $state<number | null>(null);
  let detail = $state<string | null>(null);
  function pick(event: PointerEvent): void {
    const box = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    picked = Math.max(0, Math.min(points.length - 1, Math.floor((px - L) / dx)));
    detail = null;
  }
  function choose(block: EnergyBlock, event: Event): void {
    event.stopPropagation();
    const cost = block.price ? ` · ${block.price}` : "";
    const brings = block.expected ? ` · ${block.expected}` : "";
    detail = `${block.label} ${block.start}–${block.end} · ${block.why}${cost}${brings}`;
  }
  const line = $derived(detail ?? (picked === null ? t("ui.energy.readout.hint") : readout(points[picked], currency, t, expert)));
</script>

<ul class="legend">
  <li><i class="negative"></i>{t("ui.energy.legend.negative")}</li>
  <li><i class="peak"></i>{t("ui.energy.legend.expensive")}</li>
  {#each UP as f (f)}<li><i class={f}></i>{t(`ui.energy.legend.${f}`)}</li>{/each}
  {#each DOWN as f (f)}<li><i class={f}></i>{t(`ui.energy.legend.${f}`)}</li>{/each}
  {#if expert && marginPath()}<li><i class="margin"></i>{t("ui.energy.legend.margin")}</li>{/if}
  {#if expert && sellPath()}<li><i class="sell"></i>{t("ui.energy.legend.sell")}</li>{/if}
</ul>
<p class="line" aria-live="polite">{line}</p>
<div class="wrap" bind:clientWidth={width}>
  <!-- A group, not an image: the blocks on the timeline are buttons, and an image hides what is
       in it (axe: nested-interactive). -->
  <svg viewBox="0 0 {width} {H}" width={width} height={H} role="group" aria-label={t("ui.energy.plan")} onpointermove={pick} onpointerdown={pick}>
    {#each negative as [a, b] (a)}<rect x={x(a)} y={P.top - 6} width={(b - a) * dx} height={H - P.top - 16} class="neg-band" />{/each}
    <text x={L - 6} y={P.top + 10} class="axis-label end">{t("ui.energy.price_strip")}</text>
    {#if low >= 0}<text x={x(low) + dx / 2} y={P.top - 8} class="axis-label mid">{price(Math.min(...known), currency, t)}</text>{/if}
    {#if high >= 0 && high !== low}<text x={x(high) + dx / 2} y={P.top - 8} class="axis-label mid">{price(Math.max(...known), currency, t)}</text>{/if}
    {#each points as p, i (i)}
      {@const band = priceBand(p.price, prices)}
      {#if band && p.price !== null}
        {@const y1 = yp(Math.max(p.price, 0))}
        <rect x={x(i) + 0.5} y={y1} width={Math.max(1, dx - 1)} height={Math.max(1.5, yp(Math.min(p.price, 0)) - y1)} class="band {band}" />
      {/if}
    {/each}
    <line x1={L} x2={width - R} y1={yp(0)} y2={yp(0)} class="zero" />
    {#if sellPath()}<path d={sellPath()} class="sell-line" />{/if}

    <text x={L - 6} y={mid - F.h / 4} class="axis-label end">{t("ui.energy.flow_in")}</text>
    <text x={L - 6} y={mid + F.h / 4 + 8} class="axis-label end">{t("ui.energy.flow_out")}</text>
    {#if marginPath()}<path d={marginPath()} class="margin-band" />{/if}
    {#each points as p, i (i)}
      {@const f = flows(p)}
      {#if p.past}<rect x={x(i)} y={F.top} width={dx} height={F.h} class="past-band" />{/if}
      {#each f.up as [kind, w], k (kind)}
        {#if w > 1}<rect x={x(i) + 0.5} y={mid - sc(total(f.up.slice(0, k + 1)))} width={Math.max(1, dx - 1)} height={sc(w)} class={kind} />{/if}
      {/each}
      {#each f.down as [kind, w], k (kind)}
        {#if w > 1}<rect x={x(i) + 0.5} y={mid + sc(total(f.down.slice(0, k)))} width={Math.max(1, dx - 1)} height={sc(w)} class={kind} />{/if}
      {/each}
    {/each}
    <line x1={L} x2={width - R} y1={mid} y2={mid} class="zero strong" />
    {#if past > 0}
      <line x1={x(past)} x2={x(past)} y1={P.top - 6} y2={F.top + F.h} class="now-line" />
      <text x={x(past) - 4} y={F.top + 10} class="axis-label end">{t("ui.energy.measured")}</text>
    {/if}

    {#each rows as row, r (row)}
      {@const y = T0 + r * 24}
      <text x={L - 6} y={y + 15} class="axis-label end">{t(`ui.energy.row.${row}`)}</text>
      <line x1={L} x2={width - R} y1={y + 21} y2={y + 21} class="grid" />
      {#each blocks.filter((b) => b.row === row) as b, k (k)}
        {@const w = Math.max(3, (b.to - b.from) * dx - 2)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <g class="block {b.action}" class:measuring role="button" tabindex="0" aria-label={`${b.label} ${b.start}–${b.end}`} onpointerdown={(e) => choose(b, e)} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && choose(b, e)}>
          <rect x={x(b.from) + 1} y={y + 2} width={w} height="17" rx="5" />
          {#if w > b.label.length * 6.5 + 10}<text x={x(b.from) + 6} y={y + 14}>{b.label}</text>{/if}
        </g>
      {/each}
    {/each}

    {#each ticks as i (i)}<text x={x(i)} y={H - 6} class="axis-label {i === 0 ? '' : 'mid'}">{points[i].at}</text>{/each}
    <text x={past > 0 ? x(past) : L} y={12} class="axis-label">{t("ui.energy.now_line")}</text>
    {#if picked !== null}<line x1={x(picked) + dx / 2} x2={x(picked) + dx / 2} y1={P.top - 6} y2={H - 18} class="cross" />{/if}
  </svg>
</div>

<style>
  .legend { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 14px; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .legend i { display: inline-block; width: 12px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: middle; }
  .line { margin: 0; min-height: 1.5em; color: var(--text); font-variant-numeric: tabular-nums; }
  .wrap { width: 100%; }
  svg { display: block; width: 100%; height: auto; touch-action: pan-y; background: var(--ink); border: 1px solid var(--line); border-radius: 14px; }
  .neg-band { fill: #3d8bfd; opacity: 0.1; }
  .band.negative, .legend i.negative { fill: #3d8bfd; background: #3d8bfd; }
  .band.low { fill: var(--line); }
  .band.mid { fill: var(--muted); opacity: 0.6; }
  .band.high { fill: #ff9150; }
  .band.peak, .legend i.peak { fill: #e8590c; background: #e8590c; }
  .solar, .legend i.solar { fill: #f3b65b; background: #f3b65b; }
  .from_battery, .legend i.from_battery { fill: #9c8cff; background: #9c8cff; }
  .from_grid, .legend i.from_grid { fill: var(--muted); background: var(--muted); opacity: 0.7; }
  .house, .legend i.house { fill: var(--text); background: var(--text); opacity: 0.45; }
  .to_battery, .legend i.to_battery { fill: #5ed4c4; background: #5ed4c4; }
  .boiler, .legend i.boiler { fill: #ff9150; background: #ff9150; }
  .car, .legend i.car { fill: #f06595; background: #f06595; }
  .to_grid, .legend i.to_grid { fill: #74c0fc; background: #74c0fc; }
  .margin-band, .legend i.margin { fill: #f3b65b; background: #f3b65b; opacity: 0.22; }
  .sell-line { fill: none; stroke: var(--text); stroke-width: 1.2; stroke-dasharray: 3 3; }
  .legend i.sell { height: 2px; background: var(--text); }
  .zero { stroke: var(--line); }
  .zero.strong { stroke: var(--muted); }
  .grid { stroke: var(--line); stroke-width: 0.6; }
  .past-band { fill: var(--text); opacity: 0.04; }
  .now-line { stroke: var(--text); opacity: 0.5; stroke-dasharray: 2 3; }
  .cross { stroke: var(--text); opacity: 0.5; }
  .axis-label { fill: var(--muted); font-size: 11px; font-family: var(--font-mono); }
  .axis-label.end { text-anchor: end; }
  .axis-label.mid { text-anchor: middle; }
  .block { cursor: pointer; }
  .block rect { fill: var(--muted); }
  .block.heat rect, .block.preheat rect { fill: #ff9150; }
  .block.limit rect { fill: #74c0fc; }
  .block.start rect { fill: #f3b65b; }
  .block.measuring rect { opacity: 0.5; }
  .block text { fill: #10141a; font-size: 11px; pointer-events: none; }
  .block:focus-visible rect { stroke: var(--text); stroke-width: 2; }
</style>

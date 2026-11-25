<script lang="ts">
  // Where the power of the house flows now (E-436, design 52): what comes in on the left (sun,
  // grid, a battery giving), the house in the middle, what goes out on the right (grid, a
  // battery or the car charging). A line flows while more than 50 W goes that way.
  import DeviceIcon from "./DeviceIcon.svelte";
  import { kw } from "../lib/energyplan";
  import { powerFlow, type Power } from "../lib/dayview";
  import type { T } from "../lib/ui";

  interface Props {
    live: { solar: number | null; grid: number | null; house: number | null; batteries: { name: string; pct: number | null; watts: number | null }[]; car: { watts: number | null } | null };
    t: T;
  }
  let { live, t }: Props = $props();
  const flow = $derived(powerFlow(live));
  const ROW = 52;
  const GAP = 8;
  const rows = $derived(Math.max(flow.into.length, flow.out.length, 1));
  const height = $derived(rows * ROW + (rows - 1) * GAP);
  const mid = (i: number) => i * (ROW + GAP) + ROW / 2;
  // The boxes of the flow as icons (E-512): the sun, the grid, a battery, the car.
  const FLOW_ICONS: Record<string, string> = { solar: "solar", from_grid: "grid", to_grid: "grid", grid: "grid", battery: "battery", car: "car", house: "home" };
  const name = (p: Power) => p.name ?? t(p.key === "car" ? "ui.energy.now.car" : `ui.energy.now.${p.key}`);
  const value = (p: Power) => (p.watts === null ? "" : kw(Math.abs(p.watts), t));
</script>

<div class="flow" style:height="{height}px">
  <div class="side">
    {#each flow.into as p (p.key + (p.name ?? ""))}<div class="box" class:sun={p.key === "solar"}><span class="muted"><span class="ic" class:warm={p.key === "solar"}><DeviceIcon name={FLOW_ICONS[p.key] ?? "energy"} size={14} /></span>{name(p)}</span><b class="mono">{value(p)}</b></div>{/each}
  </div>
  <svg width="56" height={height} viewBox="0 0 56 {height}" aria-hidden="true">
    {#each flow.into as p, i (p.key + (p.name ?? ""))}<path d="M0 {mid(i)} C28 {mid(i)} 28 {height / 2} 56 {height / 2}" class="line" class:on={p.active} class:sun={p.key === "solar"} />{/each}
  </svg>
  <div class="house box"><span class="muted"><span class="ic"><DeviceIcon name="home" size={14} /></span>{t("ui.energy.now.house")}</span><b class="mono">{live.house === null ? "" : kw(live.house, t)}</b></div>
  <svg width="56" height={height} viewBox="0 0 56 {height}" aria-hidden="true">
    {#each flow.out as p, i (p.key + (p.name ?? ""))}<path d="M0 {height / 2} C28 {height / 2} 28 {mid(i)} 56 {mid(i)}" class="line" class:on={p.active} />{/each}
  </svg>
  <div class="side">
    {#each flow.out as p (p.key + (p.name ?? ""))}<div class="box"><span class="muted"><span class="ic"><DeviceIcon name={FLOW_ICONS[p.key] ?? "energy"} size={14} /></span>{name(p)}</span><b class="mono">{value(p)}</b></div>{/each}
  </div>
</div>

<style>
  .ic { display: inline-flex; vertical-align: -2px; margin-right: 5px; color: var(--muted); }
  .ic.warm { color: var(--learning); }
  .flow { display: grid; grid-template-columns: minmax(0, 1fr) 56px minmax(0, 1fr) 56px minmax(0, 1fr); align-items: center; }
  .side { display: flex; flex-direction: column; gap: 8px; height: 100%; }
  .box { height: 52px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; padding: 4px 10px; border-radius: 14px; background: var(--raised); border: 1px solid var(--line); font-size: calc(13px * var(--text-scale)); min-width: 0; }
  .box span, .box b { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .box.sun { border-color: var(--learning); }
  .house { align-self: center; }
  .line { fill: none; stroke: var(--line); stroke-width: 2; }
  .line.on { stroke: var(--accent); stroke-width: 2.5; stroke-dasharray: 6 4; animation: flow 1s linear infinite; }
  .line.on.sun { stroke: var(--learning); }
  @keyframes flow { to { stroke-dashoffset: -20; } }
</style>

<script lang="ts">
  // Energy per level (E-73, E-346, E-364): a sentence, now and today and the day clock (level
  // 2); the plan in one chart with a timeline, tips, costs and base load (level 3); more detail
  // and the calculation per quarter (level 4). The home battery and the car are only shown
  // (E-363): there is nothing here to steer them with.
  import DeviceIcon from "../components/DeviceIcon.svelte";
  import EnergyClock from "../components/EnergyClock.svelte";
  import EnergyFlow from "../components/EnergyFlow.svelte";
  import EnergyPlan from "../components/EnergyPlan.svelte";
  import { kw } from "../lib/energyplan";
  import type { EnergyView } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    energy: EnergyView;
    level: number;
    t: T;
    onmore?: (id: string) => void; // Lees meer: the manual at that part (E-388)
  }
  let { energy, level, t, onmore }: Props = $props();
  const TODAY = ["solar_kwh", "import_kwh", "export_kwh", "battery_in_kwh", "battery_out_kwh", "car_kwh"] as const;

  function flow(watts: number | null): string {
    if (watts === null) return "";
    if (watts > 50) return t("ui.energy.now.charging", { kw: kw(watts, t) });
    if (watts < -50) return t("ui.energy.now.discharging", { kw: kw(watts, t) });
    return t("ui.energy.now.idle");
  }
  function kwh(value: number): string {
    return `${value.toFixed(1).replace(".", t("format.decimal"))} kWh`;
  }
  const live = $derived(energy.live);
  const today = $derived(TODAY.filter((k) => energy.today?.[k] !== undefined && (energy.today?.[k] ?? 0) > 0.05));
</script>

<section class="card">
  <p class="sentence">{energy.sentence}</p>
  {#if energy.measuringDay}
    <!-- The first weeks (E-408): how far the measuring is. -->
    {@const m = energy.measuringDay}
    {@const label = t("ui.energy.measuring_day", { day: Math.min(m.day, m.of), of: m.of })}
    <p class="measuring">{label}</p>
    <!-- WCAG: a progressbar needs a name (found by axe); the same words as above it. -->
    <div class="bar" role="progressbar" aria-valuemin={0} aria-valuemax={m.of} aria-valuenow={Math.min(m.day, m.of)} aria-label={label}><span style:width="{(100 * Math.min(m.day, m.of)) / m.of}%"></span></div>
  {/if}
  {#if energy.more && onmore}
    {@const more = energy.more}
    <button class="more" onclick={() => onmore(more)}>{t("ui.vesta.more")}</button>
  {/if}
</section>

{#if live}
  <section class="block">
    <h2>{t("ui.energy.now.title")}</h2>
    <!-- Where the power flows now, as lines (E-436, design 52). -->
    <EnergyFlow {live} {t} />
    {#if today.length}
      <h3>{t("ui.energy.today.title")}</h3>
      <ul class="tiles">
        {#each today as key (key)}<li><span>{t(`ui.energy.today.${key}`)}</span><b class="mono">{kwh(energy.today?.[key] ?? 0)}</b></li>{/each}
      </ul>
    {/if}
  </section>
{/if}

{#if level >= 2 && energy.clock}
  <section class="block">
    <h2>{t("ui.energy.clock.title")}</h2>
    <EnergyClock clock={energy.clock} currency={energy.currency ?? "€"} {t} />
  </section>
{/if}

{#if level >= 3 && energy.chart}
  <section class="block">
    <h2>{t(energy.savings?.measuring ? "ui.energy.steps_measuring" : "ui.energy.plan")}</h2>
    <EnergyPlan
      points={energy.chart}
      blocks={energy.blocks ?? []}
      measuring={energy.savings?.measuring ?? false}
      expert={level >= 4}
      currency={energy.currency ?? "€"}
      {t}
    />
    {#if energy.priceRange}<p>{energy.priceRange}</p>{/if}
    {#if energy.priceBasis}<p class="muted">{energy.priceBasis}</p>{/if}
  </section>
{/if}

{#if level >= 3 && energy.parts?.length}
  <section class="block">
    <h2>{t("ui.energy.parts.title")} <span class="muted">· {t("ui.energy.parts.weeks")}</span></h2>
    <ul class="users">{#each energy.parts as part (part.name)}<li class:resting={part.resting}><span>{#if part.icon}<span class="ic" aria-hidden="true"><DeviceIcon name={part.icon} size={18} /></span>{/if}{part.name}</span><span class="mono" class:minus={part.amount < 0}>{part.text}</span></li>{/each}</ul>
    {#if level >= 4 && energy.steps?.some((s) => s.calc)}
      <ul class="calc">{#each energy.steps.filter((s) => s.calc) as s, i (i)}<li><b>{s.what}</b> {s.start}–{s.end} · {s.calc}</li>{/each}</ul>
    {/if}
  </section>
{/if}

{#if level >= 3 && energy.tips?.length}
  <section class="block">
    <h2>{t("ui.energy.tips")}</h2>
    <ul class="tips">{#each energy.tips as tip, i (i)}<li>{tip}</li>{/each}</ul>
  </section>
{/if}

{#if level >= 3 && energy.costs}
  <section class="block">
    <h2>{t("ui.energy.costs.title")}</h2>
    <ul class="tiles">
      <li><span>{t("ui.energy.costs.today")}</span><b class="mono">{energy.costs.today}</b></li>
      <li><span>{t("ui.energy.costs.month")}</span><b class="mono">{energy.costs.month}</b></li>
    </ul>
    {#if energy.savings && !energy.savings.measuring && energy.savings.days.length}
      <h3>{t("ui.energy.savings")}</h3>
      <p class="muted">{t("ui.energy.savings_what")}</p>
      <table>
        <thead><tr><th></th><th>{t("ui.energy.cost")}</th><th>{t("ui.energy.expected")}</th><th>{t("ui.energy.difference")}</th></tr></thead>
        <tbody>
          {#each energy.savings.days as day (day.day)}
            <tr><td class="mono">{day.label}</td><td class="mono">{day.cost.toFixed(2)}</td><td class="mono">{day.expected.toFixed(2)}</td><td class="mono" class:minus={day.saving < 0}>{day.saving.toFixed(2)}</td></tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
{/if}

{#if level >= 3 && energy.baseLoad}
  <section class="block">
    <h2>{t("ui.energy.base")}</h2>
    <p>{energy.baseLoad}</p>
    {#if energy.usersToday?.length}
      <h3>{t("ui.energy.users_today")}</h3>
      <ul class="users">{#each energy.usersToday as u (u.name)}<li><span>{#if u.icon}<span class="ic" aria-hidden="true"><DeviceIcon name={u.icon} size={18} pct={u.pct} level={u.level} /></span>{/if}{u.name}</span><span class="mono">{kwh(u.kwh)}</span></li>{/each}</ul>
    {/if}
    {#if level >= 4 && energy.users?.length}
      <h3>{t("ui.energy.users")}</h3>
      <ul class="users">{#each energy.users as u (u.name)}<li><span>{#if u.icon}<span class="ic on" aria-hidden="true"><DeviceIcon name={u.icon} size={18} pct={u.pct} level={u.level} /></span>{/if}{u.name}</span><span class="mono">{u.watts} W</span></li>{/each}</ul>
    {/if}
  </section>
{/if}

<style>
  .ic { display: inline-flex; vertical-align: -4px; margin-right: 8px; color: var(--muted); }
  .ic.on { color: var(--accent); }
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 18px; padding: 14px 16px; display: flex; flex-direction: column; gap: 6px; }
  .more { align-self: flex-start; padding: 0; border: 0; background: none; color: var(--accent); font: inherit; font-size: calc(14px * var(--text-scale)); cursor: pointer; text-decoration: underline; min-height: 32px; }
  .sentence { margin: 0; font-size: calc(17px * var(--text-scale)); line-height: 1.45; }
  .tiles { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 8px; }
  .tiles li { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .tiles b { color: var(--text); font-size: calc(16px * var(--text-scale)); font-weight: 600; }
  .tips li::before { content: "→ "; color: var(--muted); }
  .block { display: flex; flex-direction: column; gap: 8px; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  h3 { margin: 6px 0 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); }
  .muted { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .users { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .users li { display: flex; justify-content: space-between; padding: 6px 2px; border-bottom: 1px solid var(--line); }
  .mono { font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); white-space: nowrap; }
  table { width: 100%; border-collapse: collapse; font-size: calc(14px * var(--text-scale)); }
  th { text-align: left; color: var(--muted); font-weight: 500; padding: 4px; border-bottom: 1px solid var(--line); }
  td { padding: 4px; border-bottom: 1px solid var(--line); }
  .minus { color: var(--important); }
  .resting span:first-child { color: var(--muted); }
  .calc { margin: 6px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .measuring { margin: 8px 0 4px; font-weight: 600; }
  .bar { height: 6px; border-radius: 3px; background: var(--line); overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--accent); }
</style>

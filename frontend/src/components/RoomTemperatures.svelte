<script lang="ts">
  // Temperaturen per ruimte (E-583): Thuis, Slapen and Weg for heating and, where the room's
  // thermostats cool too, for cooling; in half degrees, as far as they can. "Pas na" says how
  // long nobody is home before Weg. Nothing changes until Bewaren: one button for everything
  // in the window. "Terug naar wat ik leer" gives the room back to what HouseMind learns.
  import { untrack } from "svelte";
  import { afterText, degrees, keepOrder, MOMENTS, type Mode, type Moment } from "../lib/temps";
  import type { TempsView } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    temps: TempsView;
    lang: string; // numbers as the reader writes them: 18,5° (E-583)
    t: T;
    onsend: (message: Record<string, unknown>) => void;
    onclose: () => void;
  }
  let { temps, lang, t, onsend, onclose }: Props = $props();

  const modes = $derived(Object.keys(temps.values) as Mode[]);
  // A copy to change: the window's own until Bewaren (the window opens anew for another room).
  // It opens on what the thermostats do now: cooling when they cool (E-583).
  let mode = $state<Mode>(
    untrack(() => (temps.now === "cool" && temps.values.cool ? "cool" : temps.values.heat ? "heat" : "cool")),
  );
  let values = $state(untrack(() => structuredClone($state.snapshot(temps.values))));
  let after = $state(untrack(() => temps.after));
  let changed = $state(false);
  const steps = $derived(temps.afterSteps);
  const afterAt = $derived(Math.max(0, steps.indexOf(after)));

  function set(moment: Moment, value: number): void {
    const now = values[mode];
    if (!now) return;
    now[moment] = { value, from: "own" };
    keepOrder(now, mode);
    changed = true;
  }

  function fromText(moment: Moment): string {
    const from = values[mode]?.[moment].from ?? "start";
    return from === "start_away" ? t(`ui.temps.from.start_away_${mode}`) : t(`ui.temps.from.${from}`);
  }


  function save(): void {
    const out: Record<string, Record<Moment, number>> = {};
    for (const m of modes) {
      const v = values[m]!;
      out[m] = { home: v.home.value, sleep: v.sleep.value, away: v.away.value };
    }
    onsend({ do: "save", area: temps.area, temps: { ...out, after_h: after } });
    onclose();
  }

  function learn(): void {
    onsend({ do: "learn", area: temps.area });
    onclose();
  }
</script>

<div class="temps">
  <p class="note">{temps.count > 1 ? t("ui.temps.room_note_more", { count: temps.count }) : t("ui.temps.room_note")}</p>
  {#if temps.one}<p class="note">{t("ui.temps.one_note")}</p>{/if}

  {#if modes.length > 1}
    <div class="seg" role="group" aria-label={`${t("ui.temps.heat")} / ${t("ui.temps.cool")}`}>
      {#each modes as m (m)}
        <button type="button" aria-pressed={mode === m} onclick={() => (mode = m)}>{t(`ui.temps.${m}`)}</button>
      {/each}
    </div>
  {/if}

  {#if values[mode]}
    {@const [low, high] = temps.limits[mode] ?? [5, 30]}
    {#each MOMENTS as moment (moment)}
      {@const v = values[mode]![moment]}
      <label class="line">
        <span class="label">{t(`ui.temps.${moment}`)}<small>{t(`ui.temps.${moment}_sub`)}</small></span>
        <input type="range" min={low} max={high} step="0.5" value={v.value}
          oninput={(e) => set(moment, Number(e.currentTarget.value))}
          aria-label={t(`ui.temps.${moment}`)} aria-valuetext={degrees(v.value, lang)} />
        <span class="value" class:own={v.from === "own"}>{degrees(v.value, lang)}</span>
      </label>
      <p class="from">{fromText(moment)}</p>
    {/each}
  {/if}

  <section class="after" aria-label={t("ui.temps.after_title")}>
    <h3>{t("ui.temps.after_title")}</h3>
    <label class="line">
      <span class="label">{t("ui.temps.after_label")}<small>{t("ui.temps.away_sub")}</small></span>
      <input type="range" min="0" max={steps.length - 1} step="1" value={afterAt}
        oninput={(e) => { after = steps[Number(e.currentTarget.value)] ?? after; changed = true; }}
        aria-label={t("ui.temps.after_label")} aria-valuetext={afterText(after, t)} />
      <span class="value" class:own={temps.own || changed}>{afterText(after, t)}</span>
    </label>
    <p class="note">{t("ui.temps.after_note")}</p>
    {#if after < 4}<p class="note warn">{t("ui.temps.after_slow")}</p>{/if}
  </section>

  <div class="row">
    <span class="note grow">{temps.own || changed ? t("ui.temps.own_note") : t("ui.temps.learned_note")}</span>
    {#if temps.own}<button type="button" onclick={learn}>{t("ui.temps.learn")}</button>{/if}
  </div>
  <div class="buttons">
    <button type="button" onclick={onclose}>{t("ui.temps.cancel")}</button>
    <button type="button" class="primary" onclick={save}>{t("ui.temps.save")}</button>
  </div>
</div>

<style>
  .temps { display: flex; flex-direction: column; gap: 4px; }
  .note { color: var(--muted); font-size: calc(13px * var(--text-scale)); margin: 0; }
  .note.warn { color: var(--learning); }
  .seg { display: inline-flex; align-self: flex-start; border: 1px solid var(--edge); border-radius: 20px; overflow: hidden; margin: 8px 0 4px; }
  .seg button { border: 0; border-radius: 0; min-height: 40px; background: transparent; }
  .seg button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); font-weight: 600; }
  .line { display: grid; grid-template-columns: 92px minmax(0, 1fr) 64px; gap: 10px; align-items: center; padding-top: 10px; border-top: 1px solid var(--line); }
  .label { font-weight: 600; }
  .label small { display: block; font-weight: 400; color: var(--muted); font-size: calc(12px * var(--text-scale)); line-height: 1.3; }
  .value { text-align: right; font-family: var(--font-display); font-size: calc(18px * var(--text-scale)); }
  .value.own { color: var(--accent); }
  input[type="range"] { width: 100%; accent-color: var(--accent); min-height: 44px; }
  .from { margin: -6px 0 4px 102px; color: var(--muted); font-size: calc(12px * var(--text-scale)); }
  .after { margin-top: 8px; }
  .after h3 { margin: 8px 0 0; font-size: calc(15px * var(--text-scale)); }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
  .grow { flex: 1 1 200px; }
  .buttons { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
  /* Its own buttons, as in the Standen window: each window styles what is in it. */
  button { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); cursor: pointer; }
  button.primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 600; }
</style>

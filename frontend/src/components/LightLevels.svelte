<script lang="ts">
  // Standen voor lampen (E-490 – E-496, B-413): per stand a brightness that feels like it on
  // this lamp, and a colour as far as the lamp can; Proef on the lamp itself. While this window
  // is open HouseMind keeps off the lamp and a Proef stays on it; Klaar or closing the window
  // puts it back as it was (E-516). A lamp that only goes on and off has one choice, at Gedimd.
  import DeviceIcon from "./DeviceIcon.svelte";
  import { onDestroy, onMount, untrack } from "svelte";
  import { STANDS, START, START_COLOURS, kelvinCss, tone, type StandColour } from "../lib/stands";
  import type { DeviceRow } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    lamp: DeviceRow;
    t: T;
    onsend: (message: Record<string, unknown>) => void;
  }
  let { lamp, t, onsend }: Props = $props();
  const levels = $derived(lamp.levels!);

  // A copy to change, from what the lamp had when the window opened; Bewaren sends it.
  const first = untrack(() => lamp.levels);
  let pct = $state<number[]>([...(first?.pct ?? START)]);
  let colour = $state<StandColour[]>((first?.colour ?? START_COLOURS).map((c) => ({ ...c })));
  let dim = $state<"keep" | "off">(first?.dim === "off" ? "off" : "keep");
  let rhythm = $state(first?.rhythm?.by === "own" ? first.rhythm.on !== false : true);
  let trying = $state<number | null>(null);
  // Uit na … zonder beweging (E-575): its own wait, or null for what the room learned. Like
  // everything in this window it only changes here until Bewaren (E-582).
  let wait = $state<number | null>(untrack(() => lamp.levels?.offWait?.minutes ?? null));
  let waitChanged = $state(false);
  let fromStart = $state(false); // Terug naar startwaarden, until Bewaren
  let showing = false; // Laat alle vier zien runs
  let timer: ReturnType<typeof setTimeout> | undefined;
  let follow: ReturnType<typeof setTimeout> | undefined;
  let holding: ReturnType<typeof setInterval> | undefined;

  const can = $derived(levels.can);
  const colours = $derived(can === "white" || can === "colour");

  function draft(): Record<string, unknown> {
    return { pct: [...pct], colour: colour.map((c) => ({ ...c })), dim, rhythm };
  }

  function name(i: number): string {
    return t(`stands.stand.${STANDS[i]}`);
  }

  // Each stand brighter than the one before: the slider stops at its neighbours.
  function setPct(i: number, value: number): void {
    const low = i > 0 ? pct[i - 1] + 1 : 1;
    const high = i < 3 ? pct[i + 1] - 1 : 100;
    pct[i] = Math.max(low, Math.min(high, Math.round(value)));
    if (trying === i) again();
  }

  function setMode(i: number, mode: "rhythm" | "kelvin" | "name"): void {
    if (mode === "rhythm") colour[i] = { rhythm: true };
    else if (mode === "kelvin") colour[i] = { kelvin: colour[i].kelvin ?? (i === 0 ? levels.kelvin[0] : 2700) };
    else colour[i] = { name: colour[i].name ?? levels.colours[0]?.key ?? "orange" };
    if (trying === i) again();
  }

  function mode(c: StandColour): "rhythm" | "kelvin" | "name" {
    return c.name ? "name" : c.kelvin !== undefined ? "kelvin" : "rhythm";
  }

  // Proef: the lamp itself on this stand, with what the screen shows now (E-491). It stays so
  // while this window is open (E-516).
  function put(i: number): void {
    trying = i;
    onsend({ do: "try", id: lamp.id, stand: i, levels: draft() });
  }

  function tryStand(i: number): void {
    stopShowing();
    put(i);
  }

  function stopShowing(): void {
    showing = false;
    clearTimeout(timer);
  }

  // A slider moved while its stand is on the lamp: the lamp follows, a moment later.
  function again(): void {
    clearTimeout(follow);
    follow = setTimeout(() => { if (trying !== null) onsend({ do: "try", id: lamp.id, stand: trying, levels: draft() }); }, 400);
  }

  function done(): void {
    stopShowing();
    trying = null;
    onsend({ do: "back", ids: [lamp.id] });
  }

  function showAll(): void {
    stopShowing();
    showing = true;
    let i = 0;
    put(0);
    const next = (): void => {
      if (!showing) return;
      i += 1;
      if (i > 3) { done(); return; }
      put(i);
      timer = setTimeout(next, 5000);
    };
    timer = setTimeout(next, 5000);
  }

  function reset(): void {
    pct = [...START];
    colour = START_COLOURS.map((c) => ({ ...c }));
    dim = "keep";
    rhythm = true;
    fromStart = true;
  }

  // Still exactly the start values, as Terug naar startwaarden left them.
  function atStart(): boolean {
    return pct.every((p, i) => p === START[i]) && dim === "keep" && rhythm
      && JSON.stringify(colour) === JSON.stringify(START_COLOURS);
  }

  // One Bewaren for the whole window (E-582): the standen – or back to where they come from –
  // and the wait before off, if it changed.
  function save(): void {
    onsend({
      do: "save",
      id: lamp.id,
      levels: draft(),
      ...(fromStart && atStart() ? { reset: true } : {}),
      ...(waitChanged ? { wait } : {}),
    });
    fromStart = false;
    waitChanged = false;
  }

  function setWait(value: number): void {
    wait = Math.max(0, Math.min(levels.offWait?.max ?? 30, Math.round(value)));
    waitChanged = true;
  }

  function learnWait(): void {
    wait = null;
    waitChanged = true;
  }

  function waitText(minutes: number): string {
    return minutes === 0 ? t("ui.stands.wait.now") : t("ui.stands.wait.minutes", { n: minutes });
  }

  // While this window is open HouseMind keeps off the lamp; it says so again every 20 seconds,
  // so a screen that went away lets go by itself (E-516).
  onMount(() => {
    const id = lamp.id;
    onsend({ do: "hold", id });
    holding = setInterval(() => onsend({ do: "hold", id }), 20_000);
  });

  onDestroy(() => {
    clearTimeout(follow);
    clearInterval(holding);
    stopShowing();
    onsend({ do: "release", id: lamp.id }); // closing the window: back as it was, HouseMind again
  });

  const sourceText = $derived(t(`ui.stands.source.${levels.source}`, { name: levels.sourceName }));
  const canText = $derived(t(`ui.stands.can.${can}`, { low: levels.kelvin[0], high: levels.kelvin[1] }));
</script>

<div class="levels">
  <p class="sub"><span class="now-lamp" aria-hidden="true"><DeviceIcon name="lamp" size={28} pct={lamp.pct ?? null} /></span>{lamp.area}{lamp.area ? " · " : ""}{canText}{#if can !== "onoff"} · <span class="tag" class:own={levels.source === "own"} class:start={levels.source === "start"}>{sourceText}</span>{/if}</p>

  {#if can !== "onoff"}
    <!-- Where the values and the colour come from, in one short block at the top (E-582). -->
    <div class="from">
      {#if levels.rhythm?.by === "ha"}
        <p>{levels.rhythm.name && levels.rhythm.ours ? t("ui.stands.rhythm_ha_ours", { name: levels.rhythm.name })
          : levels.rhythm.name ? t("ui.stands.rhythm_ha", { name: levels.rhythm.name }) : t("ui.stands.rhythm_ha_none")}</p>
      {:else if levels.rhythm?.by === "own"}
        <label class="check"><input type="checkbox" bind:checked={rhythm} /> <span>{t("ui.stands.rhythm_own")}<small>{t("ui.stands.rhythm_own_hint")}</small></span></label>
      {/if}
      <p>{t("ui.stands.start_note")}</p>
    </div>
  {/if}

  {#if can === "onoff"}
    <fieldset>
      <legend>{t("ui.stands.plain.legend")}</legend>
      <label class="choice"><input type="radio" name="dim-{lamp.id}" value="keep" bind:group={dim} /> <span>{t("ui.stands.plain.keep")}<small>{t("ui.stands.plain.keep_hint")}</small></span></label>
      <label class="choice"><input type="radio" name="dim-{lamp.id}" value="off" bind:group={dim} /> <span>{t("ui.stands.plain.off")}<small>{t("ui.stands.plain.off_hint")}</small></span></label>
    </fieldset>
  {:else}
    <div class="preview" role="status" aria-live="polite">
      <p>{trying !== null ? t("ui.stands.trying", { name: lamp.name, stand: name(trying) }) : t("ui.stands.proof_note")}</p>
      <div class="row">
        <button type="button" onclick={showAll}>{t("ui.stands.try_all")}</button>
        {#if trying !== null}<button type="button" class="primary" onclick={done}>{t("ui.stands.done")}</button>{/if}
      </div>
    </div>

    <div class="scale" aria-hidden="true">
      <div class="track"></div>
      {#each pct as p, i (i)}<span class="mark" style="left: {p}%; --fill: {25 * (i + 1)}%"></span>{/each}
      {#each [0, 25, 50, 75, 100] as tick (tick)}<span class="tick" style="left: {tick}%">{tick}</span>{/each}
    </div>

    {#each STANDS as stand, i (stand)}
      <div class="stand" data-stand={stand}>
        <div class="head">
          <!-- The lamp as it is in this stand, with its percentage in it (E-511). -->
          <span class="stand-lamp" aria-hidden="true"><DeviceIcon name="lamp" size={32} pct={pct[i]} /></span>
          <div class="what"><strong>{name(i)}</strong><span>{t(`ui.stands.what.${stand}`)}</span></div>
          <button type="button" aria-label={t("ui.stands.try_label", { stand: name(i) })} aria-pressed={trying === i} onclick={() => tryStand(i)}>{t("ui.stands.try")}</button>
        </div>
        <label class="line">
          <span class="label">{t("ui.stands.brightness")}</span>
          <input type="range" min="1" max="100" value={pct[i]} oninput={(e) => setPct(i, Number(e.currentTarget.value))} aria-label="{name(i)}: {t('ui.stands.brightness')}" />
          <span class="value">{pct[i]}%</span>
        </label>
        {#if colours}
          <div class="line">
            <span class="label">{t("ui.stands.colour")}</span>
            <div class="seg" role="group" aria-label="{name(i)}: {t('ui.stands.colour')}">
              <button type="button" aria-pressed={mode(colour[i]) === "rhythm"} onclick={() => setMode(i, "rhythm")}>{t("ui.stands.rhythm")}</button>
              <button type="button" aria-pressed={mode(colour[i]) === "kelvin"} onclick={() => setMode(i, "kelvin")}>{t("ui.stands.fixed")}</button>
              {#if can === "colour"}<button type="button" aria-pressed={mode(colour[i]) === "name"} onclick={() => setMode(i, "name")}>{t("ui.stands.named")}</button>{/if}
            </div>
            <span class="value small">{colour[i].kelvin !== undefined ? t(`ui.stands.tone.${tone(colour[i].kelvin!)}`) : ""}</span>
          </div>
          {#if colour[i].kelvin !== undefined}
            <label class="line">
              <span class="label"></span>
              <input class="warm" type="range" min={levels.kelvin[0]} max={levels.kelvin[1]} step="100" value={colour[i].kelvin}
                style="--from: {kelvinCss(levels.kelvin[0])}; --to: {kelvinCss(levels.kelvin[1])}"
                oninput={(e) => { colour[i] = { kelvin: Number(e.currentTarget.value) }; if (trying === i) again(); }}
                aria-label="{name(i)}: {t('ui.stands.colour')}" />
              <span class="value">{colour[i].kelvin} K</span>
            </label>
          {:else if colour[i].name}
            <div class="swatches" role="group" aria-label={t("ui.stands.choose_colour", { stand: name(i) })}>
              {#each levels.colours as c (c.key)}
                <button type="button" class="swatch" style="background: {c.key}" aria-label={c.name} aria-pressed={colour[i].name === c.key}
                  onclick={() => { colour[i] = { name: c.key }; if (trying === i) again(); }}></button>
              {/each}
            </div>
          {/if}
        {/if}
      </div>
    {/each}
  {/if}

  {#if levels.offWait && levels.edit}
    {@const ow = levels.offWait}
    <section class="wait" aria-label={t("ui.stands.wait.title")}>
      <h3>{t("ui.stands.wait.title")}</h3>
      {#if !ow.sensed}
        <p class="note">{t("ui.stands.wait.no_sensor")}</p>
      {:else}
        <label class="line wait-line">
          <span class="label">{t("ui.stands.wait.label")}</span>
          <input type="range" min="0" max={ow.max} step="1" value={wait ?? Math.round(ow.room)}
            oninput={(e) => setWait(Number(e.currentTarget.value))} aria-label={t("ui.stands.wait.label")}
            aria-valuetext={wait !== null ? waitText(wait) : ow.roomText} />
          <span class="value" class:own={wait !== null}>{wait !== null ? waitText(wait) : ow.roomText}</span>
        </label>
        <p class="note">{t("ui.stands.wait.hint")}</p>
        {#if wait !== null}
          <div class="row">
            <span class="note grow">{t("ui.stands.wait.yours")}</span>
            <button type="button" onclick={learnWait}>{t("ui.stands.wait.learn")}</button>
          </div>
        {:else}
          <p class="note">{ow.learned ? t("ui.stands.wait.learned", { minutes: ow.roomText }) : t("ui.stands.wait.start", { minutes: ow.roomText })}</p>
        {/if}
        {#if ow.motionOnly}<p class="note warn">{t("ui.stands.wait.motion_only")}</p>{/if}
      {/if}
    </section>
  {/if}

  {#if levels.today}
    <section class="today" aria-label={t("ui.stands.day.title")}>
      <h3>{t("ui.stands.day.title")}</h3>
      {#if levels.today.events.length}
        <ol>
          {#each levels.today.events as event, i (i)}
            <li>
              <span class="time">{event.time}</span>
              <span class="said">{event.what}<span class="who">{event.who}</span></span>
              <span class="got">
                {#if event.on}{event.pct !== null ? `${event.pct}%` : ""}{event.kelvin !== null ? ` · ${event.kelvin} K` : ""}{:else}–{/if}
                <span class="glow" style="background: {event.on ? (event.kelvin !== null ? kelvinCss(event.kelvin) : 'var(--learning)') : 'var(--line)'}; opacity: {event.on ? 0.55 + Math.min(0.45, (event.pct ?? 100) / 40) : 0.6}" aria-hidden="true"></span>
              </span>
            </li>
          {/each}
        </ol>
      {:else}
        <p class="note">{t("ui.stands.day.empty")}</p>
      {/if}
      {#if levels.today.since}<p class="note">{t("ui.stands.day.since", { time: levels.today.since })}</p>{/if}
    </section>
  {/if}

  <div class="buttons">
    <button type="button" onclick={reset}>{t("ui.stands.reset")}</button>
    <button type="button" class="primary" onclick={save}>{t("ui.stands.save")}</button>
  </div>
</div>

<style>
  .stand-lamp { display: inline-flex; flex: none; color: var(--learning); }
  .now-lamp { display: inline-flex; vertical-align: -9px; margin-right: 6px; color: var(--learning); }
  .levels { display: flex; flex-direction: column; gap: 12px; }
  .sub { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .from { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; border-radius: 12px; background: var(--raised); color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .from p { margin: 0; }
  .from .check { padding: 0; border: 0; background: none; }
  .tag { font-size: calc(12px * var(--text-scale)); padding: 2px 8px; border-radius: 10px; border: 1px solid var(--line); white-space: nowrap; }
  .tag.own { color: var(--accent); border-color: var(--accent); }
  .tag.start { color: var(--learning); border-color: var(--learning); }
  button { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--edge); background: var(--raised); cursor: pointer; }
  button.primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 600; }
  .preview { padding: 12px; border-radius: 14px; background: var(--ink); border: 1px solid var(--line); display: flex; flex-direction: column; gap: 8px; }
  .preview p { margin: 0; }
  .row, .buttons { display: flex; gap: 8px; flex-wrap: wrap; }
  .buttons { justify-content: space-between; }
  .scale { position: relative; height: 34px; margin: 0 8px; }
  .track { position: absolute; left: 0; right: 0; top: 12px; height: 6px; border-radius: 3px; background: linear-gradient(90deg, var(--line), var(--learning)); }
  .mark { position: absolute; top: 8px; width: 14px; height: 14px; margin-left: -7px; border-radius: 50%; border: 2px solid var(--text); background: color-mix(in srgb, var(--learning) var(--fill), var(--surface)); }
  .tick { position: absolute; top: 24px; transform: translateX(-50%); font-family: var(--font-mono); font-size: calc(11px * var(--text-scale)); color: var(--muted); }
  .stand { border-top: 1px solid var(--line); padding-top: 10px; display: flex; flex-direction: column; gap: 6px; }
  .head { display: flex; gap: 10px; align-items: flex-start; }
  .what { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .what span { color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .head button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
  .line { display: grid; grid-template-columns: 92px minmax(0, 1fr) 72px; gap: 10px; align-items: center; padding-left: 24px; }
  .label { color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .value { font-family: var(--font-mono); font-size: calc(14px * var(--text-scale)); text-align: right; }
  .value.small { font-family: var(--font-body); font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  input[type="range"] { width: 100%; accent-color: var(--accent); min-height: 32px; }
  .warm { -webkit-appearance: none; appearance: none; height: 8px; min-height: 8px; border-radius: 4px; background: linear-gradient(90deg, var(--from), var(--to)); border: 1px solid var(--line); }
  .warm::-webkit-slider-thumb { -webkit-appearance: none; width: 22px; height: 22px; border-radius: 50%; background: var(--surface); border: 2px solid var(--text); cursor: pointer; }
  .warm::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: var(--surface); border: 2px solid var(--text); cursor: pointer; }
  .seg { display: inline-flex; border: 1px solid var(--edge); border-radius: 18px; overflow: hidden; width: max-content; max-width: 100%; }
  .seg button { border: none; border-radius: 0; min-height: 34px; padding: 0 10px; background: transparent; font-size: calc(13px * var(--text-scale)); }
  .seg button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); font-weight: 600; }
  .swatches { display: flex; gap: 8px; flex-wrap: wrap; padding-left: 126px; }
  .swatch { width: 32px; height: 32px; min-height: 32px; padding: 0; border-radius: 50%; border: 2px solid transparent; }
  .swatch[aria-pressed="true"] { border-color: var(--text); box-shadow: inset 0 0 0 2px var(--surface); }
  fieldset { border: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  legend { font-weight: 600; margin-bottom: 8px; padding: 0; }
  .choice, .check { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border-radius: 14px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; }
  .choice input, .check input { width: 20px; height: 20px; margin-top: 2px; accent-color: var(--accent); flex: none; }
  .choice small, .check small { display: block; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .note { margin: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  /* Uit na … zonder beweging (E-575) */
  .wait { border-top: 1px solid var(--line); padding-top: 10px; display: flex; flex-direction: column; gap: 8px; }
  .wait h3 { margin: 0; font-size: calc(15px * var(--text-scale)); }
  .wait .wait-line { padding-left: 0; grid-template-columns: 120px minmax(0, 1fr) 96px; }
  .wait .value.own { color: var(--accent); font-weight: 600; }
  .wait .row { align-items: center; }
  .wait .grow { flex: 1; min-width: 0; }
  .note.warn { color: var(--text); }
  /* Vandaag met deze lamp (E-500) */
  .today h3 { margin: 4px 0 8px; font-size: calc(15px * var(--text-scale)); }
  .today ol { list-style: none; margin: 0 0 6px; padding: 0; border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
  .today li { display: grid; grid-template-columns: 48px minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 8px 12px; border-top: 1px solid var(--line); }
  .today li:first-child { border-top: none; }
  .time { font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .said { min-width: 0; }
  .who { display: block; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .got { display: flex; gap: 8px; align-items: center; font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); white-space: nowrap; }
  .glow { width: 20px; height: 20px; border-radius: 50%; border: 1px solid var(--edge); flex: none; }
  @media (max-width: 460px) {
    .line { grid-template-columns: 74px minmax(0, 1fr) 60px; padding-left: 0; }
    .swatches { padding-left: 0; }
  }
</style>

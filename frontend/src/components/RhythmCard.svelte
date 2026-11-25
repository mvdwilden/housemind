<script lang="ts">
  // The rhythm of the house (E-366 – E-368, chapter 35): first the part of the day now (E-455,
  // chapter 59); per type of day a bar of the 24 hours – sleeping, the morning, away, at home –
  // as in the manual, also while HouseMind is still learning, with what is not sure yet striped
  // and a line "nu" on today's bar (E-507, chapter 78); then the moments in words, out of the
  // ordinary, and what is not known yet with the reason. Colour is never the only sign: the
  // legend has an icon and a word, the bar a sentence for a screen reader (E-46).
  import { barLabel, barParts, nowAt, unsureParts } from "../lib/rhythmbar";
  import type { Snippet } from "svelte";
  import type { Rhythm, RhythmOther } from "../lib/types";
  import type { T } from "../lib/ui";
  import Icon from "./Icon.svelte";
  import DeviceIcon from "./DeviceIcon.svelte";

  interface Props {
    rhythm: Rhythm;
    lang: string;
    t: T;
    children?: Snippet; // Straks, right under the bars
  }
  let { rhythm, lang, t, children }: Props = $props();

  const KINDS = ["sleep", "morning", "away", "home"] as const;
  const ICONS = { sleep: "moon", morning: "sunrise", away: "away", home: "home" } as const;
  const unsure = $derived(rhythm.days.some((d) => (d.unsure ?? []).length > 0));

  function date(iso: string): string {
    return new Date(iso).toLocaleDateString(lang, { day: "numeric", month: "long" });
  }

  function clock(iso: string): string {
    return new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
  }

  function other(item: RhythmOther): string {
    if (item.value) return item.value;
    if (!item.from) return "";
    if (item.key === "guests" && item.to) {
      const day = new Date(item.from).toLocaleDateString(lang, { weekday: "long" });
      return t("rhythm.other.at", { day, from: clock(item.from), to: clock(item.to) });
    }
    return item.to
      ? t("rhythm.from_to", { from: date(item.from), to: date(item.to) })
      : t("rhythm.other.since", { from: date(item.from) });
  }
</script>

<section class="rhythm" aria-labelledby="rhythm-title">
  <h2 id="rhythm-title">{t("ui.brain.rhythm")}</h2>
  <p class="muted">{t("rhythm.intro")}</p>
  {#if rhythm.now}<p class="now">{rhythm.now}</p>{/if}
  {#if rhythm.note}<p class="note">{rhythm.note}</p>{/if}
  {#if rhythm.days.length}
    <div class="bars">
      {#each rhythm.days as day (day.key)}
        {@const at = nowAt(day, rhythm.today, rhythm.minute)}
        <span class="name"><b>{day.label}</b>{#if day.learning}<small>{t("rhythm.bar.learning")}</small>{/if}</span>
        <div class="track" class:empty={!day.bar.length} role="img" aria-label={barLabel(day, t)}>
          {#if !day.bar.length}<span class="empty-text" aria-hidden="true">{t("rhythm.bar.not_enough")}</span>{/if}
          {#each barParts(day, t) as part, i (i)}
            <i class="part {part.kind}" style:left="{part.left}%" style:width="{part.width}%" title={part.title}>
              {#if part.icon}<Icon name={part.icon} size={14} />{/if}
            </i>
          {/each}
          {#each unsureParts(day) as part, i (i)}
            <i class="unsure" style:left="{part.left}%" style:width="{part.width}%"></i>
          {/each}
          {#if at !== null}<span class="now-line" style:left="{at}%"><span>{t("rhythm.bar.now")}</span></span>{/if}
        </div>
      {/each}
      <span></span>
      <div class="axis mono" aria-hidden="true">
        <span style:left="0%">0:00</span><span style:left="25%">6:00</span><span style:left="50%">12:00</span><span style:left="75%">18:00</span><span style:left="100%">24:00</span>
      </div>
    </div>
    <ul class="legend" aria-hidden="true">
      {#each KINDS as kind (kind)}<li><i class={kind}></i><Icon name={ICONS[kind]} size={16} />{t(`rhythm.legend.${kind}`)}</li>{/each}
      {#if unsure}<li><i class="unsure"></i>{t("rhythm.legend.unsure")}</li>{/if}
    </ul>
  {/if}
  {@render children?.()}
  {#each rhythm.days as day (day.key)}
    <h3>{day.label}</h3>
    <dl>
      {#each day.moments as m (m.key)}
        <dt>{m.label}</dt>
        <dd>
          {#if m.value}<span class="mono">{m.value}</span>{:else}<span class="muted">{m.learning}</span>{/if}
          {#if m.detail}<span class="muted detail">· {m.detail}</span>{/if}
        </dd>
      {/each}
    </dl>
  {/each}
  <!-- Only with an AI's story (QA 3, schermen 20): without one these are the same sentences as
       in Klaar voor autonomie?. -->
  {#if rhythm.habits?.some((h) => h.text)}
    <h3>{t("rhythm.habits_title")}</h3>
    <dl class="habits">
      {#each rhythm.habits.filter((h) => h.text) as habit (habit.area)}
        <dt>{#if habit.icon}<span class="room-icon" aria-hidden="true"><DeviceIcon name={habit.icon} size={18} /></span>{/if}{habit.room}</dt>
        <dd>
          {#if habit.text}
            <p>{habit.text}</p>
            <p class="muted small">{t("rhythm.habits_ai")}</p>
          {:else}
            <ul class="lines">{#each habit.lines as line (line)}<li>{line}</li>{/each}</ul>
          {/if}
        </dd>
      {/each}
    </dl>
  {/if}
  {#if rhythm.other.length}
    <h3>{t("rhythm.other_title")}</h3>
    <dl>
      {#each rhythm.other as item (item.key)}
        <dt>{item.label}</dt>
        <dd>{other(item)}</dd>
      {/each}
    </dl>
  {/if}
</section>

<style>
  .rhythm { display: flex; flex-direction: column; gap: 8px; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  h3 { margin: 8px 0 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; }
  p { margin: 0; line-height: 1.45; }
  .muted { color: var(--muted); }
  .small { font-size: calc(13px * var(--text-scale)); }
  .lines { margin: 0; padding-left: 1.1em; line-height: 1.45; }
  .habits { grid-template-columns: 1fr; }
  .habits dt { color: var(--text); font-weight: 600; display: flex; align-items: center; gap: 6px; }
  .room-icon { display: inline-flex; color: var(--muted); }
  .habits dd { margin: 0 0 6px; }
  .note { line-height: 1.5; }
  .now { font-weight: 600; line-height: 1.45; }
  .mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
  /* The bars as in the manual (E-507, B-433): the name beside the bar, on a phone above it. */
  .bars { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 18px 16px; align-items: center; margin-top: 10px; }
  @media (max-width: 560px) { .bars { grid-template-columns: minmax(0, 1fr); gap: 18px; } }
  .name { display: flex; flex-direction: column; line-height: 1.25; }
  .name b { font-weight: 600; }
  .name small { color: var(--learning); font-size: calc(12px * var(--text-scale)); }
  .track { position: relative; height: 22px; border-radius: 11px; background: var(--raised); }
  .track.empty { background: none; border: 1.5px dashed var(--line); }
  .empty-text { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: calc(12px * var(--text-scale)); }
  .part { position: absolute; top: 0; bottom: 0; border-radius: 11px; display: flex; align-items: center; justify-content: center; }
  .part + .part { box-shadow: -2px 0 0 var(--surface); }
  .part.sleep { color: #fff; }
  .part.morning { color: var(--surface); }
  .part.away { color: var(--muted); }
  .part.home { color: var(--on-accent); }
  .unsure { position: absolute; top: 0; bottom: 0; border-radius: 11px; background: repeating-linear-gradient(135deg, var(--rhythm-sleep) 0 4px, transparent 4px 8px), var(--raised); }
  .now-line { position: absolute; top: -6px; bottom: -6px; width: 2px; margin-left: -1px; background: var(--text); border-radius: 1px; }
  .now-line span { position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); font-size: calc(11px * var(--text-scale)); font-weight: 600; line-height: 1; padding-bottom: 2px; }
  .axis { position: relative; height: 16px; color: var(--muted); font-size: calc(11px * var(--text-scale)); }
  .axis span { position: absolute; top: 0; transform: translateX(-50%); }
  .axis span:first-child { transform: none; }
  .axis span:last-child { transform: translateX(-100%); }
  .sleep { background: var(--rhythm-sleep); }
  .morning { background: var(--learning); }
  .away { background: var(--rhythm-away); }
  .home { background: var(--accent); }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  .detail { margin-left: 4px; }
  .legend { list-style: none; display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 2px 0 0; padding: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .legend li { display: inline-flex; align-items: center; gap: 6px; }
  .legend i { display: inline-block; width: 22px; height: 10px; border-radius: 5px; }
  .legend i.unsure { position: static; border-radius: 5px; border: 1px solid var(--line); }
</style>

<script lang="ts" generics="I">
  // The overview per kind above a long list (E-325, B-199): how many of each, which part of
  // the whole as one thin bar, and how many are not available; a tap turns that filter on.
  // Each kind is a small tile, side by side, so the list stays in sight (E-454).
  // Counted over the whole list, so it does not jump while choosing. Folded on a phone.
  import { toggle, type Chosen, type FilterDef } from "../lib/filters";
  import { overview } from "../lib/lists";
  import type { T } from "../lib/ui";
  import DeviceIcon from "./DeviceIcon.svelte";

  interface Props {
    items: I[];
    def: FilterDef<I>;
    chosen: Chosen;
    problem?: (item: I) => boolean;
    problemKey?: string; // the filter that holds "not available", when the list has one
    lang: string;
    t: T;
    iconOf?: (value: string) => string | null; // a kind's icon (E-510), where the list has one
  }
  let { items, def, chosen = $bindable(), problem, problemKey, lang, t, iconOf }: Props = $props();

  const TOP = 6; // the biggest kinds; the rest in one line until "Toon alle"
  let all = $state(false);
  const kinds = $derived(overview(items, def, chosen, problem));
  const listed = $derived(all ? kinds : kinds.slice(0, TOP));
  const rest = $derived(kinds.slice(listed.length));
  const bad = $derived(kinds.reduce((sum, k) => sum + k.problem, 0));
  const number = $derived(new Intl.NumberFormat(lang));
  const percent = $derived(new Intl.NumberFormat(lang, { style: "percent", maximumFractionDigits: 1 }));
  const wide = typeof matchMedia === "undefined" || matchMedia("(min-width: 640px)").matches;
  // The shades of the accent, darker to lighter: the order says which part is which, the
  // label in the table says it in words (E-46).
  const shade = (i: number): string => `color-mix(in srgb, var(--accent) ${Math.max(25, 100 - i * 15)}%, var(--surface))`;
</script>

{#if kinds.length > 1}
  <details class="overview" open={wide}>
    <summary>{t("ui.list.overview")} <span class="n">{number.format(items.length)}</span>
      {#if bad}<span class="bad-n">· {t("ui.list.unavailable")} {number.format(bad)}</span>{/if}</summary>
    <div class="bar" aria-hidden="true">
      {#each kinds as k, i (k.value)}<span style:flex-grow={k.count} style:background={shade(i)}></span>{/each}
    </div>
    <!-- Per kind a tile (E-454): how many and which part; a tap turns that filter on. -->
    <ul class="tiles" aria-label="{t('ui.list.overview')}: {def.title}">
      {#each listed as k, i (k.value)}
        {@const icon = iconOf?.(k.value) ?? null}
        <li class="tile" class:on={k.on}>
          <button aria-pressed={k.on} onclick={() => (chosen = toggle(chosen, def.key, k.value))}>
            <span class="label"><span class="swatch" style:background={shade(i)}></span>{k.label}</span>
            <!-- The kind's icon on the second line (E-510): the name keeps the whole width. -->
            <span class="num">{#if icon}<span class="kind-icon" aria-hidden="true"><DeviceIcon name={icon} size={16} /></span>{/if}<b>{number.format(k.count)}</b> · {percent.format(k.share)}</span>
          </button>
          {#if problem && k.problem}
            {#if problemKey}
              <button class="bad" onclick={() => { chosen = toggle(toggle(chosen, def.key, k.value), problemKey, "unavailable"); }}>{t("ui.list.unavailable")} {number.format(k.problem)}</button>
            {:else}<span class="bad">{t("ui.list.unavailable")} {number.format(k.problem)}</span>{/if}
          {/if}
        </li>
      {/each}
      {#if rest.length}
        <li class="tile rest">
          <span class="label">{t("ui.list.others", { n: number.format(rest.length) })}</span>
          <span class="num"><b>{number.format(rest.reduce((sum, k) => sum + k.count, 0))}</b> · {percent.format(rest.reduce((sum, k) => sum + k.share, 0))}</span>
        </li>
      {/if}
    </ul>
    {#if kinds.length > TOP}
      <button class="more" onclick={() => (all = !all)}>{all ? t("ui.list.fewer") : t("ui.list.show_all", { n: number.format(kinds.length) })}</button>
    {/if}
  </details>
{/if}

<style>
  .overview { border: 1px solid var(--line); border-radius: 14px; padding: 4px 12px 10px; background: var(--surface); }
  summary { min-height: 44px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; cursor: pointer; font-weight: 600; }
  .n { color: var(--muted); font-weight: 400; font-variant-numeric: tabular-nums; }
  .bad-n { color: var(--important); font-weight: 400; font-size: calc(14px * var(--text-scale)); }
  .bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; gap: 2px; margin: 4px 0 10px; }
  .bar span { flex-basis: 0; min-width: 3px; }
  .tiles { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 8px; }
  .tile { display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
  .tile.on { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); background: var(--raised); }
  .tile > button:first-child { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; min-height: 44px; padding: 8px 10px; border: none; background: none; color: var(--text); font: inherit; text-align: left; cursor: pointer; }
  .label { display: inline-flex; align-items: center; gap: 8px; font-size: calc(14px * var(--text-scale)); overflow-wrap: break-word; }
  .tile.on .label { color: var(--accent); font-weight: 600; }
  .num { color: var(--muted); font-size: calc(13px * var(--text-scale)); font-variant-numeric: tabular-nums; }
  .num b { color: var(--text); font-family: var(--font-mono); font-weight: 600; font-size: calc(16px * var(--text-scale)); }
  .rest { padding: 8px 10px; gap: 2px; color: var(--muted); }
  .swatch { width: 10px; height: 10px; border-radius: 3px; flex: none; }
  .kind-icon { display: inline-flex; color: var(--muted); vertical-align: -2px; margin-right: 6px; }
  .bad { min-height: 36px; padding: 0 10px; border: none; border-top: 1px solid var(--line); background: none; color: var(--important); text-align: left; text-decoration: underline; cursor: pointer; font: inherit; font-size: calc(13px * var(--text-scale)); }
  span.bad { display: flex; align-items: center; text-decoration: none; cursor: default; }
  .more { min-height: 40px; margin-top: 6px; padding: 0 4px; border: none; background: none; color: var(--accent); font: inherit; cursor: pointer; }
</style>

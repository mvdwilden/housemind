<script lang="ts" generics="I">
  // Finding things in a long list (E-258, E-276, B-123), like Home Assistant's own tables:
  // one bar with a search field, the Filter button – with how many filters are on – and,
  // where a list can be grouped, "Groeperen". Filter opens a panel under the bar with a
  // section per filter and boxes to tick, each with what is left; a long section has its own
  // search field. What is chosen stays visible as small chips above the list: a tap takes one
  // away, "Wis alles" takes them all. Within one filter "or", between filters "and". The same
  // on every screen: Apparaten en entiteiten, Automatiseringen, Scènes en scripts.
  import Icon from "./Icon.svelte";
  import StepPill from "./StepPill.svelte";
  import { active, chips, filtered, howMany, matching, picked, toggle, type Chosen, type FilterDef } from "../lib/filters";
  import { Opened } from "../lib/opened.svelte";
  const byHand = new Opened(); // a section opened or closed by hand stays so
  import type { T } from "../lib/ui";

  interface Props {
    items: I[];
    defs: FilterDef<I>[];
    text: (item: I) => string; // what the search field looks in
    chosen: Chosen;
    search: string;
    searchLabel: string;
    t: T;
    group?: string; // the chosen grouping, with its choices
    groups?: { value: string; label: string }[];
    // A period next to Groeperen, the same look (Logboek, E-323); "day" adds a date field.
    period?: string;
    periods?: { value: string; label: string }[];
    periodLabel?: string;
    day?: string;
    dayMin?: string;
    dayMax?: string;
  }
  let {
    items, defs, text, chosen = $bindable(), search = $bindable(), searchLabel, t, group = $bindable(), groups,
    period = $bindable(), periods, periodLabel = "", day = $bindable(), dayMin, dayMax,
  }: Props = $props();

  const LONG = 10; // a section with more values than this gets its own search field
  const LONG_LIST = 20; // rows from which a list gets Filter and Groeperen
  const id = `filters-${Math.random().toString(36).slice(2, 8)}`;
  let open = $state(false);
  let within = $state<Record<string, string>>({});

  const keep = (item: I): boolean => filtered([item], [], {}, search, text).length === 1;
  // Filter and Groeperen only where a list is long enough to need them (QA 3, schermen 24);
  // what is chosen already stays reachable.
  const long = $derived(items.length >= LONG_LIST || howMany(chosen) > 0);
  const rows = $derived(
    defs
      .map((def) => ({ def, chips: chips(items, def, defs, chosen, keep) }))
      .filter((row) => row.chips.length > 1 || (row.def.hideNone && row.chips.length === 1)),
  );
  const total = $derived(rows.reduce((sum, row) => sum + row.chips.length, 0));
  const count = $derived(howMany(chosen));
  const shown = $derived(filtered(items, defs, chosen, search, text).length);
  const chosenChips = $derived(picked(defs, chosen));

  function tick(key: string, value: string): void {
    chosen = toggle(chosen, key, value);
  }
  function clear(): void {
    chosen = {};
  }
</script>

<div class="finder">
  <div class="bar">
    <label class="search">
      <span class="sr">{searchLabel}</span>
      <Icon name="search" size={18} />
      <input type="search" bind:value={search} placeholder={searchLabel} autocomplete="off" />
    </label>
    {#if rows.length && long}
      <button class="filter" class:on={count > 0} aria-expanded={open} aria-controls={id} onclick={() => (open = !open)}
        aria-label={count ? `${t("ui.filter.button")}, ${count}` : undefined}>
        <Icon name="filter" size={18} /> {t("ui.filter.button")}
        {#if count}<span class="badge">{count}</span>{/if}
      </button>
    {/if}
    {#if periods && period !== undefined}
      <label class="group">
        <span>{periodLabel}</span>
        <select bind:value={period}>
          {#each periods as option (option.value)}<option value={option.value}>{option.label}</option>{/each}
        </select>
      </label>
      {#if period === "day"}
        <label class="group"><span class="sr">{periodLabel}</span><input type="date" bind:value={day} min={dayMin} max={dayMax} /></label>
      {/if}
    {/if}
    {#if groups && group !== undefined && long}
      <label class="group">
        <span>{t("ui.filter.group")}</span>
        <select bind:value={group}>
          {#each groups as option (option.value)}<option value={option.value}>{option.label}</option>{/each}
        </select>
      </label>
    {/if}
  </div>

  {#if open && rows.length}
    <div class="panel" {id} role="region" aria-label={t("ui.filter.button")}>
      {#each rows as row, i (row.def.key)}
        {@const n = (chosen[row.def.key] ?? []).length}
        {#if row.def.hideNone}
          <label class="box single">
            <input type="checkbox" checked={row.chips[0].on} onchange={() => tick(row.def.key, row.chips[0].value)} />
            <span class="label">{row.def.title}</span> <span class="n">{row.chips[0].count}</span>
          </label>
        {:else}
          <!-- The first filter is open; the others open with a tap, or when something in them is chosen. -->
          <details class="section" open={byHand.is(row.def.key, i === 0 || n > 0 || total <= 12)}>
            <summary onclick={(e) => byHand.flip(row.def.key, e)}><span class="arrow"><Icon name="chevron" size={16} /></span>{row.def.title}{#if n}<span class="badge">{n}</span>{/if}<span class="many">{row.chips.length}</span></summary>
            {#if row.chips.length > LONG}
              <input class="within" type="search" placeholder={t("ui.filter.within", { name: row.def.title })} aria-label={t("ui.filter.within", { name: row.def.title })}
                value={within[row.def.key] ?? ""} oninput={(e) => (within = { ...within, [row.def.key]: e.currentTarget.value })} />
            {/if}
            <ul>
              {#each matching(row.chips, within[row.def.key] ?? "") as chip (chip.value)}
                <li>
                  <label class="box" class:empty={!chip.count && !chip.on}>
                    <input type="checkbox" checked={chip.on} disabled={!chip.count && !chip.on} onchange={() => tick(row.def.key, chip.value)} />
                    <span class="label">{#if row.def.pill}<StepPill step={chip.value} name={chip.label} />{:else}{chip.label}{/if}</span> <span class="n">{chip.count}</span>
                  </label>
                </li>
              {/each}
            </ul>
          </details>
        {/if}
      {/each}
      <div class="foot">
        <button class="link" onclick={clear} disabled={!active(chosen)}>{t("ui.filter.clear")}</button>
        <button class="primary" onclick={() => (open = false)}>{t("ui.filter.show", {}, shown)}</button>
      </div>
    </div>
  {:else if chosenChips.length}
    <div class="chosen" role="group" aria-label={t("ui.filter.chosen")}>
      {#each chosenChips as chip (`${chip.key}:${chip.value}`)}
        <button class="chip" aria-label={t("ui.filter.remove", { name: chip.label })} onclick={() => tick(chip.key, chip.value)}>
          {chip.label} <Icon name="close" size={14} />
        </button>
      {/each}
      <button class="link" onclick={clear}>{t("ui.filter.clear")}</button>
    </div>
  {/if}
</div>

<style>
  .finder { display: flex; flex-direction: column; gap: 8px; }
  .bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .search { flex: 1 1 220px; min-width: 0; display: flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 14px; border-radius: 22px; border: 1px solid var(--edge); background: var(--surface); color: var(--muted); }
  .search:focus-within { border-color: var(--accent); }
  .search input { flex: 1; min-width: 0; border: none; background: none; color: var(--text); font: inherit; outline: none; min-height: 42px; }
  .filter { display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--edge); background: var(--surface); color: var(--text); font: inherit; cursor: pointer; }
  .filter.on, .filter[aria-expanded="true"] { border-color: var(--accent); }
  .badge { min-width: 20px; height: 20px; padding: 0 6px; border-radius: 10px; background: var(--accent); color: var(--on-accent); font-size: calc(12px * var(--text-scale)); font-weight: 700; line-height: 20px; text-align: center; margin-left: 4px; }
  .group { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .group select, .group input { min-height: 44px; padding: 0 12px; border-radius: 12px; border: 1px solid var(--edge); background: var(--surface); color: var(--text); font: inherit; }
  .group input { color-scheme: inherit; }
  .panel { display: flex; flex-direction: column; gap: 2px; padding: 6px 14px 12px; border-radius: 16px; border: 1px solid var(--line); background: var(--surface); }
  .section { border-bottom: 1px solid var(--line); }
  .section summary { min-height: 44px; display: flex; align-items: center; gap: 8px; cursor: pointer; font-weight: 600; list-style: none; }
  .section summary::-webkit-details-marker { display: none; }
  .arrow { display: grid; place-items: center; color: var(--muted); transition: transform 0.15s; }
  .section[open] .arrow { transform: rotate(90deg); }
  .many { margin-left: auto; font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); font-weight: 400; color: var(--muted); }
  .within { width: 100%; box-sizing: border-box; min-height: 44px; margin: 2px 0 6px; padding: 0 14px; border-radius: 22px; border: 1px solid var(--edge); background: var(--ink); color: var(--text); font: inherit; }
  ul { list-style: none; margin: 0 0 8px; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 0 12px; }
  .box { display: flex; align-items: center; gap: 10px; min-height: 44px; cursor: pointer; }
  .box.single { min-height: 44px; border-bottom: 1px solid var(--line); }
  .box input { width: 20px; height: 20px; flex: none; accent-color: var(--accent); }
  .box .label { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .box.empty { color: var(--muted); cursor: default; }
  .n { font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); color: var(--muted); }
  .foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding-top: 10px; }
  .primary { min-height: 44px; padding: 0 20px; border-radius: 22px; border: 1px solid var(--accent); background: var(--accent); color: var(--on-accent); font: inherit; font-weight: 600; cursor: pointer; }
  .link { min-height: 44px; border: none; background: none; color: var(--accent); font: inherit; font-weight: 600; cursor: pointer; padding: 0; }
  .link:disabled { color: var(--muted); cursor: default; }
  .chosen { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .chip { display: inline-flex; align-items: center; gap: 6px; min-height: 44px; padding: 0 12px 0 16px; border-radius: 22px; border: 1px solid var(--accent); background: var(--raised); color: var(--text); font: inherit; font-size: calc(14px * var(--text-scale)); cursor: pointer; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>

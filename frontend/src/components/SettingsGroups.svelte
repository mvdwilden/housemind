<script lang="ts" module>
  export interface SettingsGroup {
    key: string;
    title: string;
    hint: string; // what is in it, in a few words
    sections: string[]; // the data-section of each part, so Vesta can open the right group
  }
</script>

<script lang="ts">
  // Settings in groups (E-265, B-112). On a phone you open a group, with a way back; on a
  // computer the groups are on the left and the open group on the right. With a search, every
  // part that matches shows, under the name of its group. Vesta's "Zal ik het voor je openen?"
  // (E-244) opens the group of that part.
  import type { Snippet } from "svelte";

  interface Props {
    groups: SettingsGroup[];
    content: Snippet<[string]>; // the parts of one group
    back: string; // "← Mijn voorkeuren"
    focus?: string | null; // a part to open (data-section)
    query?: string; // the search; only the House settings have one
    none?: string; // "Niets gevonden"
  }
  let { groups, content, back, focus = null, query = "", none = "" }: Props = $props();

  let chosen = $state<string | null>(null);
  $effect(() => {
    if (!focus) return;
    const group = groups.find((g) => g.sections.includes(focus));
    if (group) chosen = group.key;
  });

  // On a computer one group is always open: the first, until you choose.
  let wide = $state(typeof matchMedia !== "undefined" && matchMedia("(min-width: 900px)").matches);
  if (typeof matchMedia !== "undefined") {
    matchMedia("(min-width: 900px)").addEventListener("change", (e) => (wide = e.matches));
  }
  const open = $derived(groups.some((g) => g.key === chosen) ? chosen : wide ? (groups[0]?.key ?? null) : null);
  const searching = $derived(query.trim().length > 0);

  // Searching: a part shows when its words contain the search; a group when one of its parts does.
  let hits = $state(1);
  function search(node: HTMLElement, text: string) {
    const apply = (value: string) => {
      const q = value.trim().toLocaleLowerCase();
      let count = 0;
      for (const group of node.querySelectorAll<HTMLElement>("[data-group]")) {
        let inGroup = 0;
        for (const part of group.querySelectorAll<HTMLElement>("section[data-section]")) {
          const match = !q || (part.textContent ?? "").toLocaleLowerCase().includes(q);
          part.hidden = !match;
          if (match) inGroup++;
        }
        group.hidden = inGroup === 0;
        count += inGroup;
      }
      hits = count;
    };
    apply(text);
    return { update: apply };
  }
</script>

{#if searching}
  <div class="results" use:search={query}>
    {#each groups as group (group.key)}
      <div class="found" data-group={group.key}>
        <h3 class="group-title">{group.title}</h3>
        {@render content(group.key)}
      </div>
    {/each}
    {#if hits === 0}<p class="muted" role="status">{none}</p>{/if}
  </div>
{:else}
  <div class="groups" class:wide>
    {#if wide || open === null}
      <nav class="list" aria-label={back.replace(/^←\s*/, "")}>
        {#each groups as group (group.key)}
          <button class="group" aria-current={open === group.key ? "true" : undefined} onclick={() => (chosen = group.key)}>
            <strong>{group.title}</strong>
            <span>{group.hint}</span>
          </button>
        {/each}
      </nav>
    {/if}
    {#if open !== null}
      <div class="open">
        {#if !wide}<button class="back" onclick={() => (chosen = null)}>{back}</button>{/if}
        {#each groups.filter((g) => g.key === open) as group (group.key)}
          <h3 class="group-title">{group.title}</h3>
          {@render content(group.key)}
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .groups { display: flex; flex-direction: column; gap: 14px; }
  .groups.wide { display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 20px; align-items: start; }
  .list { display: flex; flex-direction: column; gap: 6px; }
  .wide .list { position: sticky; top: 0; }
  .group { text-align: left; padding: 10px 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; display: flex; flex-direction: column; gap: 2px; min-height: 52px; color: var(--text); }
  .group span { color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .group[aria-current="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .open, .results, .found { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
  .results :global([data-group][hidden]) { display: none; }
  .open :global(section[hidden]), .results :global(section[hidden]) { display: none; }
  .group-title { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); color: var(--text); }
  .back { align-self: flex-start; min-height: 44px; padding: 0 4px; border: none; background: none; color: var(--accent); font-weight: 600; cursor: pointer; }
  .muted { margin: 0; color: var(--muted); }
</style>

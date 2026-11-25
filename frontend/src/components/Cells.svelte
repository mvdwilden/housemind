<script lang="ts" generics="I">
  // The details a row of a long list shows (E-325, B-198): the columns a person chose, as one
  // quiet line with a dot between them (QA 3), which wraps on a phone.
  import StepPill from "./StepPill.svelte";
  import type { Column } from "../lib/lists";

  interface Props {
    columns: Column<I>[];
    item: I;
  }
  let { columns, item }: Props = $props();
</script>

{#if columns.length}
  <!-- One quiet line, "18:29 · Woonkamer · gedaan" (QA 3, schermen 11): the labels only for a
       screen reader; Kolommen still chooses what is in it. -->
  <dl class="cells">
    {#each columns as c, i (c.key)}
      {@const value = c.value(item)}
      <div>
        <dt class="sr">{c.title}</dt>
        <dd>{#if i > 0}<span aria-hidden="true" class="dot">·</span>{/if}{#if c.step}<StepPill step={c.step(item)} name={value} />{:else}{value || "–"}{/if}</dd>
      </div>
    {/each}
  </dl>
{/if}

<style>
  .cells { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 6px; margin: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .cells div { min-width: 0; display: contents; }
  dd { margin: 0; display: inline-flex; align-items: center; gap: 6px; overflow-wrap: anywhere; }
  .dot { color: var(--muted); }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>

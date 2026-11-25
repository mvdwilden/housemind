<script lang="ts">
  // What the brain thought (E-218), on Huis in one sentence: the newest, with the time
  // (E-261, B-109). "Meer in Logboek" opens the whole story – saw · thought · did per level,
  // with Why? and Undo – in Logboek (E-263, B-117). From level 2 the newest as cause → effect, a
  // small flow (E-436).
  import Flow from "./Flow.svelte";
  import { causeEffect, flowKind } from "../lib/flow";
  import type { MindItem } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    items: MindItem[];
    t: T;
    onmore?: () => void;
    rhythm?: string; // Informed: the rhythm of the house in one sentence (E-368)
  }
  let { items, t, onmore, rhythm }: Props = $props();
</script>

<section class="card mind" data-tour="mind" aria-label={t("ui.mind.title")}>
  <h2>{t("ui.mind.title")}</h2>
  {#if items.length === 0}
    <p class="muted">{t("ui.mind.none")}</p>
  {:else}
    {@const pair = causeEffect(items[0], t)}
    {#if pair}
      <Flow direction="row" kind={flowKind(items[0].outcome)} steps={pair} />
    {:else}
      <p class="text"><span class="mono when">{items[0].at}</span> {items[0].text}</p>
    {/if}
  {/if}
  {#if rhythm}<p class="text rhythm">{rhythm}</p>{/if}
  {#if onmore}<button class="link" onclick={onmore}>{t("ui.mind.in_logbook")}</button>{/if}
</section>

<style>
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 18px; padding: 14px 16px; }
  h2 { margin: 0 0 8px; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  p { margin: 0; }
  .muted { color: var(--muted); }
  .mono { font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); }
  .text { font-size: calc(15px * var(--text-scale)); line-height: 1.45; }
  .when { color: var(--muted); margin-right: 4px; }
  .rhythm { margin-top: 6px; color: var(--muted); }
  .link { align-self: flex-start; margin-top: 6px; border: none; background: none; color: var(--accent); font-weight: 600; cursor: pointer; min-height: 40px; padding: 0; }
</style>

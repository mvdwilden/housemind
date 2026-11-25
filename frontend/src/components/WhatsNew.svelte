<script lang="ts">
  // What is new in this version (E-150, QA 2 M-36): the server sends only what fits the level –
  // one sentence on Informed, a short list on Involved and Expert, in the person's own language.
  // Once, until Gelezen.
  import type { News } from "../lib/types";
  import type { T } from "../lib/ui";

  let { news, t, onread }: { news: News; t: T; onread: () => void } = $props();
</script>

<section class="news" aria-labelledby="news-title">
  <h2 id="news-title">{t("ui.news.title", { version: news.version })}</h2>
  {#if news.one}<p>{news.one}</p>{/if}
  {#if news.list?.length}
    <ul>{#each news.list as item, i (i)}<li>{item}</li>{/each}</ul>
  {/if}
  <button class="read" onclick={onread}>{t("ui.news.read")}</button>
</section>

<style>
  .news { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; border: 1px solid var(--edge); border-radius: 16px; background: var(--surface); }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  p, li { margin: 0; line-height: 1.5; }
  ul { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; }
  .read { align-self: flex-start; min-height: 44px; padding: 0 18px; border-radius: 22px; border: 1px solid var(--accent); background: var(--accent); color: var(--on-accent); font-weight: 600; cursor: pointer; }
</style>

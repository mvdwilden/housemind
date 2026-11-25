<script lang="ts">
  // Under a long list (E-325, B-196): Vorige, "Pagina 1 van 16", Volgende and Rijen per
  // pagina. "Alle" in a big house shows a part first, then "Toon de rest" (E-322).
  import { listChoice, setListChoice } from "../lib/device.svelte";
  import { ALL_AT_ONCE, ROWS } from "../lib/lists";
  import type { T } from "../lib/ui";

  interface Props {
    list: string; // the list's key: rows per page are kept per list and device
    page: number;
    pages: number;
    total: number; // after search and filter
    more?: boolean; // "Alle": the rest shown too
    lang: string;
    t: T;
    ongo: (page: number) => void;
    onmore?: () => void;
  }
  let { list, page, pages, total, more = false, lang, t, ongo, onmore }: Props = $props();
  const rows = $derived(listChoice(list).rows ?? ROWS[0]);
  const number = $derived(new Intl.NumberFormat(lang));
</script>

{#if total > ROWS[0]}
  <nav class="pager" aria-label={t("ui.list.pages")}>
    {#if rows > 0}
      <button disabled={page <= 1} onclick={() => ongo(page - 1)}>{t("ui.list.previous")}</button>
      <span class="where">{t("ui.list.page", { n: number.format(page), m: number.format(pages) })}</span>
      <button disabled={page >= pages} onclick={() => ongo(page + 1)}>{t("ui.list.next")}</button>
    {:else if total > ALL_AT_ONCE && !more}
      <button onclick={() => onmore?.()}>{t("ui.list.rest", { n: number.format(total - ALL_AT_ONCE) })}</button>
    {/if}
    <label class="rows">
      <span>{t("ui.list.rows")}</span>
      <select value={String(rows)} onchange={(e) => { setListChoice(list, { rows: Number(e.currentTarget.value) }); ongo(1); }}>
        {#each ROWS as n (n)}<option value={String(n)}>{n ? number.format(n) : t("ui.list.all")}</option>{/each}
      </select>
    </label>
  </nav>
{/if}

<style>
  .pager { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 8px 0; }
  .pager button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); cursor: pointer; font: inherit; }
  .pager button:disabled { opacity: 0.45; cursor: default; }
  .where { color: var(--muted); font-variant-numeric: tabular-nums; font-size: calc(14px * var(--text-scale)); }
  .rows { margin-left: auto; display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .rows select { min-height: 44px; padding: 0 10px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); font: inherit; }
  @media (max-width: 639px) {
    .rows { margin-left: 0; width: 100%; justify-content: space-between; }
  }
</style>

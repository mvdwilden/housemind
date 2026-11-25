<script lang="ts" generics="I">
  // Under the filter bar of a long list (E-325, B-196 – B-198): how many there are, which
  // details a row shows (and compact), and Exporteren – of what search and filter show now,
  // made in this browser; nothing leaves the house (E-15). No export on a phone. Without a
  // def only the count: Klaar voor autonomie? has no Kolommen and no Exporteren (E-501).
  import Icon from "./Icon.svelte";
  import { listChoice, setListChoice } from "../lib/device.svelte";
  import { chosenColumns, csv, fileName, KINDS, markdown, table, type ExportKind, type ListDef, type Page } from "../lib/lists";
  import type { T } from "../lib/ui";

  interface Props {
    def?: ListDef<I>; // none: only the count, no buttons
    items: I[]; // after search and filter, all pages
    total: number; // before search and filter
    page?: Page<I> | null; // none: grouped, only the number
    lang: string;
    t: T;
  }
  let { def, items, total, page = null, lang, t }: Props = $props();

  let width = $state(typeof innerWidth === "number" ? innerWidth : 1024);
  let menu = $state<"" | "columns" | "export">("");
  const id = `list-${Math.random().toString(36).slice(2, 8)}`;
  const choice = $derived(def ? listChoice(def.key) : {});
  const shown = $derived(def ? chosenColumns(def, choice.columns) : []);
  const number = $derived(new Intl.NumberFormat(lang));
  const count = $derived.by(() => {
    const n = number.format(items.length);
    const range = page && items.length
      ? t("ui.list.range", { from: number.format(page.from), to: number.format(page.to), n })
      : t("ui.list.count", { n });
    return items.length < total ? `${range} ${t("ui.list.of_total", { total: number.format(total) })}` : range;
  });

  function column(key: string, on: boolean): void {
    if (!def) return;
    const now = new Set(shown.map((c) => c.key));
    if (on) now.add(key);
    else now.delete(key);
    setListChoice(def.key, { columns: def.columns.map((c) => c.key).filter((k) => now.has(k)) });
  }

  function save(kind: ExportKind): void {
    if (!def) return;
    const data = table(items, def, shown, t("ui.list.id"));
    const text = kind === "csv" ? csv(data, lang) : markdown(data); // no JSON: CSV and Markdown are enough (QA 3)
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type: KINDS[kind] }));
    link.download = fileName(def.key, kind, new Date());
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    menu = "";
  }

  function outside(event: MouseEvent): void {
    if (menu && !(event.target as HTMLElement).closest(`#${id}`)) menu = "";
  }
</script>

<svelte:window bind:innerWidth={width} onclick={outside} onkeydown={(e) => e.key === "Escape" && menu && (menu = "")} />

<div class="tools" {id}>
  <p class="count" aria-live="polite">{count}</p>
  {#if def}
  <div class="buttons">
    {#if def.columns.length}
      <div class="holder">
        <button aria-expanded={menu === "columns"} onclick={() => (menu = menu === "columns" ? "" : "columns")}>
          <Icon name="columns" size={18} /> {t("ui.list.columns")}
        </button>
        {#if menu === "columns"}
          <div class="menu" role="group" aria-label={t("ui.list.columns")}>
            {#each def.columns as c (c.key)}
              <label><input type="checkbox" checked={shown.some((s) => s.key === c.key)} onchange={(e) => column(c.key, e.currentTarget.checked)} /> {c.title}</label>
            {/each}
            <hr />
            <label><input type="checkbox" checked={choice.compact === true} onchange={(e) => setListChoice(def.key, { compact: e.currentTarget.checked })} /> {t("ui.list.compact")}</label>
          </div>
        {/if}
      </div>
    {/if}
    {#if width >= 640}
      <div class="holder">
        <button aria-expanded={menu === "export"} disabled={!items.length} onclick={() => (menu = menu === "export" ? "" : "export")}>
          <Icon name="export" size={18} /> {t("ui.list.export")}
        </button>
        {#if menu === "export"}
          <div class="menu" role="menu">
            {#each ["csv", "md"] as const as kind (kind)}
              <button role="menuitem" class="item" onclick={() => save(kind)}>
                <strong>{t(`ui.list.as.${kind}`)}</strong><span>{t(`ui.list.as.${kind}_hint`)}</span>
              </button>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>
  {/if}
</div>

<style>
  .tools { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
  .count { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); font-variant-numeric: tabular-nums; }
  .buttons { display: flex; gap: 8px; }
  .holder { position: relative; }
  .holder > button { display: inline-flex; align-items: center; gap: 6px; min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); cursor: pointer; font: inherit; font-size: calc(14px * var(--text-scale)); }
  .holder > button[aria-expanded="true"] { border-color: var(--accent); }
  .holder > button:disabled { opacity: 0.5; cursor: default; }
  .menu { position: absolute; right: 0; top: calc(100% + 6px); z-index: 5; min-width: 220px; max-width: min(320px, 90vw); padding: 8px; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3); display: flex; flex-direction: column; gap: 2px; }
  .menu label { display: flex; align-items: center; gap: 10px; min-height: 40px; padding: 0 8px; border-radius: 10px; cursor: pointer; }
  .menu label:hover, .item:hover { background: var(--raised); }
  .menu input { width: 20px; height: 20px; accent-color: var(--accent); }
  .menu hr { width: 100%; border: none; border-top: 1px solid var(--line); margin: 4px 0; }
  .item { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; min-height: 44px; padding: 8px 10px; border: none; border-radius: 10px; background: transparent; color: var(--text); text-align: left; cursor: pointer; font: inherit; }
  .item span { color: var(--muted); font-size: calc(13px * var(--text-scale)); }
</style>

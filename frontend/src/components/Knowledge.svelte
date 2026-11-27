<script lang="ts">
  // Wat ik weet in Brein (E-524, B-448), for administrators on Betrokken and Expert: what
  // HouseMind knows of the house – from the rules, from behaviour (E-203), from AI or from a
  // person – with four tiles on top (Te bevestigen, Bevestigd, Klopt niet, Scènes en scripts),
  // a search and Groeperen, per room and folded per device. A tap on a line shows beside the list
  // (in a sheet on a phone) what HouseMind thinks, where it came from and why, and its three
  // buttons: everything can be taken back (E-525, B-449). It replaces the long list in
  // Huisinstellingen (E-207).
  import DeviceIcon from "./DeviceIcon.svelte";
  import Sheet from "./Sheet.svelte";
  import { acts, actKey, choiceOf, counts, grouped, isFold, shown, TILES, type Act, type DeviceFold, type KnowledgeTile } from "../lib/knowledge";
  import type { HouseKnowledgeView, KnowledgeRow } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    view: HouseKnowledgeView | undefined;
    lang: string;
    t: T;
    send: (message: Record<string, unknown>) => void;
    get: (what: string) => void;
    focus?: string | null; // "knowledge:area:music:role": open at that line (E-486)
    roomIcons?: Record<string, string>;
  }
  let { view, lang, t, send, get, focus = null, roomIcons = {} }: Props = $props();

  // Asked when the tab opens; while AI reads the house, what it finds comes in by itself.
  $effect(() => get("house_knowledge"));
  $effect(() => {
    if (!view?.busy) return;
    const timer = setInterval(() => get("house_knowledge"), 3000);
    return () => clearInterval(timer);
  });

  const rows = $derived(view?.rows ?? []);
  const n = $derived(counts(rows));
  // What waits first; nothing waiting: everything (a tap on a tile again shows all of it).
  let tile = $state<KnowledgeTile | null | undefined>(undefined);
  const current = $derived(tile === undefined ? (n.open ? "open" : null) : tile);
  let query = $state("");
  let byRoom = $state(true);
  const list = $derived(current === "scenes" ? [] : shown(rows, current, query));
  const groups = $derived(grouped(list, byRoom, t("ui.knowledge.no_room")));
  const scenes = $derived(
    (view?.scenes ?? []).filter((s) => !query.trim() || `${s.name} ${s.rooms.join(" ")} ${s.does?.text ?? ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())),
  );

  // Beside the list from 980 px of its own width, else in a sheet from below (as Klaar voor
  // autonomie?, B-424).
  const WIDE = 980;
  let width = $state(0);
  const wide = $derived(width >= WIDE);
  let picked = $state<string | null>(null);
  let sheetOpen = $state(false);
  let choosing = $state(false);
  let said = $state("");
  const first = $derived.by(() => {
    const entry = groups[0]?.entries[0];
    return entry ? (isFold(entry) ? entry.rows[0] : entry) : undefined;
  });
  // The picked line stays, also when its state moved it out of the tile shown.
  const row = $derived(rows.find((r) => r.id === picked) ?? (wide && !said ? first : undefined));

  // Vesta's "Kiezen" for a room (E-486) lands here, at that line.
  $effect(() => {
    if (!focus?.startsWith("knowledge:")) return;
    picked = focus.slice("knowledge:".length);
    tile = null;
    if (!wide) sheetOpen = true;
  });

  function pick(line: KnowledgeRow): void {
    picked = line.id;
    choosing = false;
    said = "";
    if (!wide) sheetOpen = true;
  }
  function choose(next: KnowledgeTile): void {
    tile = current === next ? null : next;
    said = "";
  }

  /** One answer, and what it does now, in words (E-525). */
  function act(line: KnowledgeRow, what: Act, choice?: string): void {
    if (what === "other" && choice === undefined) {
      choosing = !choosing;
      return;
    }
    const doing = what === "other" ? "role" : what;
    send({ type: "knowledge", do: doing, id: line.id, ...(choice !== undefined ? { role: choice } : {}) });
    const room = line.kind === "area" && line.field === "role";
    said =
      what === "other" ? t("ui.knowledge.done.own")
      : what === "back" ? t("ui.knowledge.done.back")
      : what === "yes" ? t("ui.knowledge.done.yes")
      : line.state === "yes" && room ? t("ui.knowledge.done.no_room", { room: line.name })
      : line.state === "yes" ? t("ui.knowledge.done.no_again")
      : t("ui.knowledge.done.no");
    choosing = false;
    picked = line.id;
  }
  function deviceYes(fold: DeviceFold): void {
    send({ type: "knowledge", do: "device_yes", ids: fold.rows.filter((r) => r.state === "open").map((r) => r.id) });
  }

  function day(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(lang, { day: "numeric", month: "short" });
  }
  /** Where it came from, and since when it stands so; ``chip``: the source is shown before it
   * as the violet "van AI" label (E-524), so only the rest. */
  function origin(line: KnowledgeRow, chip = false): string {
    const source = chip ? "" : t(`ui.knowledge.source.${line.source}`);
    if (line.state === "open") return line.certainty !== undefined ? `${source} · ${t("ui.knowledge.certainty", { pct: line.certainty })}` : source;
    if (line.source === "person") return t("ui.knowledge.origin.person", { date: day(line.since) });
    return t(`ui.knowledge.origin.${line.state}`, { source, date: day(line.since) });
  }
  const summary = (fold: DeviceFold): string => fold.summary.map((s) => (s.count > 1 ? `${s.what} (${s.count})` : s.what)).join(", ");
</script>

{#snippet line(item: KnowledgeRow, inFold: boolean)}
  <li>
    <button class="row" class:in-fold={inFold} aria-current={row?.id === item.id ? "true" : undefined} onclick={() => pick(item)}>
      <span class="main">
        <span class="title"><span class="what">{item.name}</span>{#if !inFold && item.device && item.device !== item.name}<span class="room">{item.device}</span>{/if}</span>
        <span class="when">{item.label}: {item.what} · {#if item.source === "ai"}<span class="from-ai">{t("ui.knowledge.source.ai")}</span>{:else}{t(`ui.knowledge.source.${item.source}`)}{/if}</span>
      </span>
      <span class="state {item.state}">{t(`ui.knowledge.state.${item.state}`)}</span>
    </button>
  </li>
{/snippet}

{#snippet detail(item: KnowledgeRow)}
  {@const which = choiceOf(item)}
  <article class="detail" aria-label={item.name}>
    <h3>{item.name}</h3>
    <!-- Its device and room; a room is its own name already. -->
    <p class="sub">{#if item.device && item.device !== item.name}{item.device} · {/if}{#if item.kind !== "area"}{item.room || t("ui.knowledge.no_room")}{/if} <span class="state {item.state}">{t(`ui.knowledge.state.${item.state}`)}</span></p>
    <div class="thinks"><span class="k">{item.label}</span><span class="v">{item.what}</span></div>
    <p class="fact"><span class="k">{t("ui.knowledge.k.origin")}:</span> {#if item.source === "ai"}<span class="from-ai">{t("ui.knowledge.source.ai")}</span>{origin(item, true)}{:else}{origin(item)}{/if}</p>
    {#if item.why && item.source !== "person"}<p class="fact"><span class="k">{t("ui.knowledge.k.why")}:</span> {item.why}</p>{/if}
    {#if item.was}<p class="fact"><span class="k">{t("ui.knowledge.k.was")}:</span> {item.was}</p>{/if}
    {#if item.note}<p class="note">{item.note}</p>{/if}
    {#if acts(item).length}
      <div class="acts">
        {#each acts(item) as what (what)}
          <button class:primary={what === "yes" && item.state === "open"} aria-expanded={what === "other" ? choosing : undefined}
            onclick={() => act(item, what)}>{t(actKey(what, item.state))}</button>
        {/each}
      </div>
    {/if}
    {#if which && view && (choosing || item.unknown)}
      <!-- Iets anders… (E-525): another kind of room, another function, another sensor. -->
      <label class="field">{t(which === "role" ? "ui.knowledge.choose_role" : which === "function" ? "ui.knowledge.choose_function" : which === "switch" ? "ui.knowledge.choose_switch" : "ui.knowledge.choose_sensor")}
        <select value="" onchange={(e) => { const v = e.currentTarget.value; if (v) act(item, "other", v); }}>
          <option value="">…</option>
          {#if which === "role"}
            {#each view.roles as choice (choice.id)}{#if choice.id !== item.role || item.unknown}<option value={choice.id}>{choice.name}</option>{/if}{/each}
            <option value="other">{t("ui.knowledge.none_of_these")}</option>
          {:else if which === "function"}
            {#each view.functions as choice (choice.id)}<option value={choice.id}>{choice.name}</option>{/each}
          {:else if which === "switch"}
            {#each view.switchRoles ?? [] as choice (choice.id)}{#if choice.id !== item.role}<option value={choice.id}>{choice.name}</option>{/if}{/each}
          {:else}
            {#each item.options ?? [] as choice (choice.id)}<option value={choice.id}>{choice.name}</option>{/each}
          {/if}
        </select>
      </label>
    {/if}
    {#if said && picked === item.id}<p class="said" role="status">{said}</p>{/if}
  </article>
{/snippet}

<section class="know" data-section="knowledge" aria-labelledby="know-title">
  <header class="head">
    <div>
      <h2 id="know-title">{t("ui.knowledge.title")}</h2>
      {#if view}<p class="muted">{t("ui.knowledge.recognised", { count: view.recognised }, view.recognised)}</p>{/if}
    </div>
    {#if view?.busy}
      <p class="muted" role="status">{t("ui.knowledge.busy")}</p>
    {:else if view?.ai}
      <button onclick={() => send({ type: "knowledge", do: "look" })}>{t("ui.knowledge.look")}</button>
    {/if}
  </header>
  <details class="about">
    <summary>{t("ui.knowledge.how")}</summary>
    <p>{t("ui.knowledge.note")}</p>
    {#if view && !view.ai}<p>{t("ui.knowledge.ai_off")}</p>{/if}
    {#if view?.described}<p>{t("ui.knowledge.described", { count: view.described }, view.described)}</p>{/if}
  </details>

  {#if !view}
    <p class="muted">…</p>
  {:else}
    <!-- Four tiles (E-524, as E-454): a tap shows only that group, again everything. -->
    <div class="tiles" role="group" aria-label={t("ui.knowledge.tiles")}>
      {#each TILES as one (one)}
        {@const count = one === "scenes" ? view.scenes.length : n[one]}
        <button class="tile {one}" class:waiting={one === "open" && count > 0} aria-pressed={current === one} onclick={() => choose(one)}>
          <b>{count}</b><span>{t(`ui.knowledge.tile.${one}`)}</span>
        </button>
      {/each}
    </div>
    <div class="tools">
      <input type="search" bind:value={query} placeholder={t("ui.knowledge.search")} aria-label={t("ui.knowledge.search")} />
      {#if current !== "scenes"}
        <div class="seg" role="group" aria-label={t("ui.filter.group")}>
          <button aria-pressed={byRoom} onclick={() => (byRoom = true)}>{t("ui.knowledge.by_room")}</button>
          <button aria-pressed={!byRoom} onclick={() => (byRoom = false)}>{t("ui.knowledge.by_none")}</button>
        </div>
      {/if}
    </div>

    {#if current === "scenes"}
      <p class="muted">{t("ui.knowledge.scenes_note")}</p>
      {#if !scenes.length}<p class="muted">{t(view.scenes.length ? "ui.filter.none" : "ui.ha_scenes.none")}</p>{/if}
      <ul class="block">
        {#each scenes as scene (scene.id)}
          <li class="scene">
            <span class="title"><span class="what">{scene.name}</span><span class="room">{t(`ui.filter.kind.${scene.kind}`)}{scene.rooms.length ? ` · ${scene.rooms.join(", ")}` : ""}</span></span>
            {#if scene.does?.text}<span>{t("ui.automations.does", { text: scene.does.text })} <span class="from-ai">{t("ui.knowledge.from_ai")}</span></span>{/if}
            <span class="muted">{scene.text}</span>
          </li>
        {/each}
      </ul>
    {:else}
      {#if !list.length}<p class="muted">{query.trim() ? t("ui.filter.none") : t(`ui.knowledge.empty.${current ?? "all"}`)}</p>{/if}
      <div class="split" class:wide bind:clientWidth={width}>
        <div class="rows">
          {#each groups as group (group.key)}
            {#if byRoom}
              <h3 class="group">{#if roomIcons[group.key]}<DeviceIcon name={roomIcons[group.key]} size={16} />{/if}{group.name} <span class="n">{group.count}</span></h3>
            {/if}
            <ul class="block">
              {#each group.entries as entry (entry.id)}
                {#if isFold(entry)}
                  <!-- One device, folded: its name and what is in it (E-524). -->
                  <li>
                    <details class="fold" open={entry.rows.some((r) => r.id === picked) || query.trim() !== ""}>
                      <summary>
                        <span class="main"><span class="title"><span class="what">{entry.name}</span>{#if entry.model}<span class="room">{entry.model}</span>{/if}</span><span class="when">{t("ui.knowledge.things", { count: entry.rows.length }, entry.rows.length)}: {summary(entry)}</span></span>
                        {#if new Set(entry.rows.map((r) => r.state)).size === 1}<span class="state {entry.rows[0].state}">{t(`ui.knowledge.state.${entry.rows[0].state}`)}</span>{/if}
                      </summary>
                      {#if entry.rows.some((r) => r.state === "open" && !r.unknown)}
                        <div class="fold-yes"><button class="primary" onclick={() => deviceYes(entry)}>{t("ui.knowledge.device_yes")}</button></div>
                      {/if}
                      <ul>{#each entry.rows as item (item.id)}{@render line(item, true)}{/each}</ul>
                    </details>
                  </li>
                {:else}
                  {@render line(entry, false)}
                {/if}
              {/each}
            </ul>
          {/each}
        </div>
        {#if wide && (row || said)}
          <aside class="pane" aria-live="polite">
            {#if row}{@render detail(row)}{:else}<p class="said" role="status">{said}</p>{/if}
          </aside>
        {/if}
      </div>
      {#if wide && !row && !said && list.length}<p class="muted">{t("ui.knowledge.pick")}</p>{/if}
      {#if !wide && sheetOpen && (row || said)}
        <Sheet title={row?.name ?? t("ui.knowledge.title")} closeLabel={t("ui.close")} onclose={() => { sheetOpen = false; said = ""; }}>
          <div class="in-sheet">{#if row}{@render detail(row)}{:else}<p class="said" role="status">{said}</p>{/if}</div>
        </Sheet>
      {/if}
    {/if}
  {/if}
</section>

<style>
  .know { display: flex; flex-direction: column; gap: 10px; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  h3 { margin: 0; font-size: calc(15px * var(--text-scale)); }
  .muted { margin: 0; color: var(--muted); }
  .head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap; }
  .head p { font-size: calc(14px * var(--text-scale)); }
  button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); font: inherit; cursor: pointer; }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  .about summary { min-height: 36px; display: inline-flex; align-items: center; cursor: pointer; color: var(--accent); font-weight: 600; font-size: calc(14px * var(--text-scale)); }
  .about p { margin: 6px 0 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); line-height: 1.45; }
  /* Four tiles, in one row on a computer, two by two on a phone (E-524, E-454). */
  .tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
  @media (max-width: 640px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  .tile { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; min-height: 72px; padding: 10px 12px; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); text-align: left; }
  .tile b { font-family: var(--font-mono); font-weight: 500; font-size: calc(22px * var(--text-scale)); line-height: 1.1; }
  .tile span { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .tile.waiting b { color: var(--learning); }
  .tile[aria-pressed="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); background: var(--raised); }
  .tools { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .tools input { flex: 1; min-width: 180px; min-height: 44px; border-radius: 22px; border: 1px solid var(--edge); background: var(--surface); color: var(--text); padding: 0 16px; font: inherit; }
  .seg { display: inline-flex; border: 1px solid var(--edge); border-radius: 22px; overflow: hidden; }
  .seg button { border: none; border-radius: 0; background: var(--surface); }
  .seg button + button { border-left: 1px solid var(--edge); }
  .seg button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); font-weight: 600; }
  h3.group { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
  .n { color: var(--muted); font-weight: 400; font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); }
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .block { border: 1px solid var(--line); border-radius: 16px; overflow: hidden; background: var(--surface); }
  .block > li { border-top: 1px solid var(--line); }
  .block > li:first-child { border-top: none; }
  .row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: center; width: 100%; min-height: 56px; padding: 10px 14px; border: none; border-radius: 0; background: none; text-align: left; }
  .row:hover { background: var(--raised); }
  .row[aria-current="true"] { background: var(--raised); box-shadow: inset 3px 0 0 var(--accent); }
  .row.in-fold { padding-left: 28px; }
  .main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .title { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline; }
  .what { font-weight: 600; min-width: 0; overflow-wrap: anywhere; }
  .room { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .when { color: var(--muted); font-size: calc(14px * var(--text-scale)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  /* Where a line stands, as a label in words, never only a colour. */
  .state { font-size: calc(12px * var(--text-scale)); padding: 2px 10px; border-radius: 12px; font-weight: 600; white-space: nowrap; color: var(--muted); background: color-mix(in srgb, var(--muted) 18%, transparent); }
  .state.open { color: var(--learning); background: color-mix(in srgb, var(--learning) 18%, transparent); }
  .state.yes { color: var(--accent); background: color-mix(in srgb, var(--accent) 18%, transparent); }
  .fold > summary { list-style: none; display: grid; grid-template-columns: 16px minmax(0, 1fr) auto; gap: 8px; align-items: center; min-height: 56px; padding: 10px 14px; cursor: pointer; }
  .fold > summary::-webkit-details-marker { display: none; }
  .fold > summary::before { content: ""; width: 8px; height: 8px; border-right: 2px solid var(--muted); border-bottom: 2px solid var(--muted); transform: rotate(-45deg); justify-self: center; }
  .fold[open] > summary::before { transform: rotate(45deg); }
  .fold ul > li { border-top: 1px solid var(--line); }
  .fold-yes { padding: 0 14px 10px 28px; }
  .scene { display: flex; flex-direction: column; gap: 2px; padding: 10px 14px; font-size: calc(14px * var(--text-scale)); }
  .split { display: block; }
  .split.wide { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 16px; align-items: start; }
  .rows { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .pane { position: sticky; top: 64px; max-height: calc(100vh - 96px); overflow-y: auto; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); padding: 16px; }
  .detail { display: flex; flex-direction: column; gap: 10px; }
  .detail h3 { font-family: var(--font-display); font-size: calc(19px * var(--text-scale)); }
  .in-sheet .detail h3 { display: none; } /* the sheet's own title says it */
  .detail .sub { margin: 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .thinks { display: flex; flex-direction: column; padding: 12px 14px; border-radius: 14px; background: var(--raised); }
  .thinks .k { color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .thinks .v { font-family: var(--font-display); font-weight: 600; font-size: calc(18px * var(--text-scale)); overflow-wrap: anywhere; }
  .fact { margin: 0; font-size: calc(14px * var(--text-scale)); line-height: 1.45; }
  .fact .k { color: var(--muted); }
  .note { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); line-height: 1.45; }
  .acts { display: flex; flex-wrap: wrap; gap: 8px; }
  .field { display: flex; flex-direction: column; gap: 4px; font-size: calc(14px * var(--text-scale)); }
  .field select { min-height: 44px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); padding: 0 12px; font: inherit; }
  .said { margin: 0; padding: 10px 12px; border-radius: 12px; border-left: 3px solid var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent); font-size: calc(14px * var(--text-scale)); line-height: 1.45; }
  @media (max-width: 640px) { .in-sheet .acts { flex-direction: column; } .in-sheet .acts button { width: 100%; } }
</style>

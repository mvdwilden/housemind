<script lang="ts">
  // Devices and entities: what HouseMind recognised, and "leave this alone" (admins). Grouped
  // by room and then device, by kind or by step, with the search field, the Filter button and
  // "Groeperen" in one bar (E-258, E-276, B-123). The chosen grouping stays on this device.
  // Without groups a flat list in pages; above it how many, the details per row and Exporteren,
  // and an overview per kind with what is not available (E-325).
  import Cells from "../components/Cells.svelte";
  import DeviceIcon from "../components/DeviceIcon.svelte";
  import { tone } from "../lib/deviceicons";
  import { openBeside } from "../lib/openha";
  import LightLevels from "../components/LightLevels.svelte";
  import Sheet from "../components/Sheet.svelte";
  import { STANDS, kelvinCss } from "../lib/stands";
  import Filters from "../components/Filters.svelte";
  import ListTools from "../components/ListTools.svelte";
  import Pager from "../components/Pager.svelte";
  import StepPill from "../components/StepPill.svelte";
  import { listChoice } from "../lib/device.svelte";
  import { active, filtered, grouped, STEPS, type Chosen, type FilterDef } from "../lib/filters";
  import { chosenColumns, pageOf, ROWS, type ListDef } from "../lib/lists";
  import { Opened } from "../lib/opened.svelte";
  import { groupTree, type GroupTree } from "../lib/grouptree";
  import type { DeviceRow } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    devices: DeviceRow[] | undefined;
    isAdmin: boolean;
    lang: string;
    name?: string; // Vesta's name, for the column with what she calls each device (E-411)
    excluded: string[];
    t: T;
    onexclude: (ids: string[]) => void;
    onwhynot: (id: string) => void;
    why: Record<string, string>;
    onlevels?: (message: Record<string, unknown>) => void; // Standen voor lampen (E-490)
  }
  let { devices, isAdmin, lang, name = "Vesta", t, onexclude, onwhynot, why, onlevels = () => {} }: Props = $props();
  // The lamp whose standen are open, and the room where a stand is being tried (E-491).
  let levelsOf = $state<string | null>(null);
  const levelsLamp = $derived(levelsOf ? (devices ?? []).find((d) => d.id === levelsOf) : undefined);
  let roomTry = $state<{ area: string; stand: number | null } | null>(null);
  function tryRoom(area: string, stand: number): void {
    roomTry = { area, stand };
    onlevels({ do: "try_room", area, stand });
  }
  function roomDone(): void {
    if (roomTry) onlevels({ do: "back", area: roomTry.area });
    roomTry = null;
  }
  let asked = $state<string | null>(null);
  let filter = $state("");

  const VIEWS = ["none", "room", "type", "step"]; // none: one flat list in pages (E-325)
  const KEY = "housemind.devices.view";
  function remembered(): string {
    try {
      const saved = localStorage.getItem(KEY) ?? "";
      return VIEWS.includes(saved) ? saved : "room";
    } catch {
      return "room";
    }
  }
  let view = $state(remembered());
  $effect(() => {
    try {
      localStorage.setItem(KEY, view);
    } catch {
      // a private window: it is only not remembered
    }
  });

  let chosen = $state<Chosen>({});
  const defs = $derived<FilterDef<DeviceRow>[]>([
    { key: "room", title: t("ui.filter.title.room"), values: (d) => [d.area], label: (v) => v || t("ui.filter.no_room") },
    { key: "type", title: t("ui.filter.title.type"), values: (d) => [d.domain], label: (v) => v },
    { key: "step", title: t("ui.filter.title.step"), values: (d) => [d.step], label: (v) => t(`ui.filter.step.${v}`), order: STEPS, pill: true },
    { key: "status", title: t("ui.list.status"), values: (d) => [unavailable(d) ? "unavailable" : "available"], label: (v) => t(`ui.list.${v}`), order: ["available", "unavailable"] },
  ]);
  function unavailable(d: DeviceRow): boolean {
    return d.state === "unavailable";
  }
  // Home Assistant's own words for "not there" in the app's language; other states as they are.
  function status(state: string): string {
    return state === "unavailable" ? t("ui.list.unavailable") : state === "unknown" ? t("ui.list.unknown") : state;
  }
  const list = $derived<ListDef<DeviceRow>>({
    key: "devices",
    name: { key: "name", title: t("ui.list.name"), value: (d) => d.name },
    columns: [
      { key: "room", title: t("ui.filter.title.room"), value: (d) => d.area || t("ui.filter.no_room"), on: true },
      { key: "type", title: t("ui.filter.title.type"), value: (d) => d.domain, on: true },
      { key: "step", title: t("ui.filter.title.step"), value: (d) => t(`ui.filter.step.${d.step}`), step: (d) => d.step, on: true },
      // The state now, off by default (QA 3, schermen 21): under Kolommen for whoever wants to
      // see the list follow the house (E-473).
      { key: "status", title: t("ui.list.status"), value: (d) => status(d.state) },
      { key: "device", title: t("ui.list.device"), value: (d) => d.device },
      { key: "kind", title: t("ui.list.kind"), value: (d) => d.kind ?? t("ui.devices.unknown") },
      { key: "labels", title: t("ui.list.labels"), value: (d) => (d.labels ?? []).join(", ") },
      // How Vesta says it ("de lamp in de slaapkamer"), so an administrator can check the names (E-411).
      { key: "vesta", title: t("ui.list.vesta", { name }), value: (d) => d.vesta ?? "", on: true },
    ],
    id: (d) => d.id,
    overview: { filter: "type", problem: unavailable },
  });
  // The grouping already says it: that column is left out of the rows.
  const columns = $derived(chosenColumns(list, listChoice("devices").columns).filter((c) => c.key !== view));
  const compact = $derived(listChoice("devices").compact === true);
  let page = $state(1);
  let more = $state(false);
  const text = (d: DeviceRow): string => `${d.name} ${d.device} ${d.area} ${d.id}`;
  const shown = $derived(filtered(devices ?? [], defs, chosen, filter, text));
  // Without groups: by name, as the overview's other lists (E-325).
  const byName = $derived(view === "none" ? [...shown].sort((a, b) => a.name.localeCompare(b.name, lang)) : []);
  const paged = $derived(view === "none" ? pageOf(byName, page, listChoice("devices").rows ?? ROWS[0], more) : null);
  const groups = $derived(
    grouped(shown, (d) => (view === "room" ? d.area : view === "type" ? d.domain : d.step), view === "step" ? STEPS : []),
  );
  const opened = $derived(shown.length <= 40 || filter !== "" || active(chosen));
  // What someone opened or closed by hand stays so when the list is drawn again; a new search,
  // filter or grouping starts over.
  const byHand = new Opened();
  $effect(() => {
    void [filter, chosen, view];
    byHand.reset();
    page = 1;
    more = false;
  });

  function title(key: string): string {
    if (view === "room") return key || t("ui.filter.no_room");
    if (view === "step") return t(`ui.filter.step.${key}`);
    return key;
  }

  function toggle(device: DeviceRow): void {
    const now = new Set((devices ?? []).filter((d) => d.excluded).map((d) => d.id));
    if (now.has(device.id)) now.delete(device.id);
    else now.add(device.id);
    onexclude([...now]);
  }
</script>

{#snippet row(device: DeviceRow)}
  <li class:excluded={device.excluded} data-id={device.id}>
    <!-- Its icon (E-510): what it is at a glance, warm for a lamp that is on. -->
    <span class="icon {tone(device.icon ?? '', device.state === 'on' || device.state === 'playing')}" aria-hidden="true"><DeviceIcon name={device.icon ?? "device"} size={20} pct={device.pct} level={device.level} closed={device.closed} /></span>
    <div class="main">
      <strong>{device.name}</strong>
      <Cells {columns} item={device} />
      <!-- No codes (QA 3, schermen 18): only what asks something of you – not there, or not
           recognised. The name opens it in Home Assistant, where you change its icon too. -->
      {#if unavailable(device)}<span class="muted">{t("ui.list.unavailable")}</span>{/if}
      {#if !device.kind}<span class="muted">{t("ui.devices.unknown")}</span>{/if}
      {#if isAdmin}<button class="link other-icon" onclick={() => openBeside(`/config/entities?search=${encodeURIComponent(device.id)}`)}>{t("ui.devices.open_in_ha")}</button>{/if}
      {#if device.detects}<span class="muted detects">{device.detects}</span>{/if}
      {#if device.levels}{@render stands(device)}{/if}
      {#if view !== "room" && device.partOf}<span class="muted">{t("ui.devices.group.part_of", { name: device.partOf.name })}</span>{/if}
      {#if view !== "room" && device.group}<span class="tag" class:radio={device.group.radio}>{device.group.sourceName}</span>{/if}
    </div>
    <div class="side">
      {#if isAdmin && device.levels?.edit}<button class="levels" onclick={() => (levelsOf = device.id)}>{device.levels.can === "onoff" ? t("ui.stands.adjust") : t("ui.stands.button")}</button>{/if}
      {#if device.controls}<button aria-expanded={asked === device.id} onclick={() => { asked = asked === device.id ? null : device.id; if (asked) onwhynot(device.id); }}>{t("ui.why_not")}</button>{/if}
      {#if isAdmin && device.controls}
        <label class="toggle"><input type="checkbox" checked={device.excluded} onchange={() => toggle(device)} /> {t("ui.devices.leave_alone")}</label>
      {/if}
    </div>
    {#if asked === device.id}<p class="why" role="status">{why[device.id] ?? "…"}</p>{/if}
  </li>
{/snippet}

<!-- A lamp's four standen: how full, its percentage and its colour (E-490). -->
{#snippet stands(device: DeviceRow)}
  {@const lv = device.levels!}
  {#if lv.can === "onoff"}
    <span class="muted">{lv.dim === "off" ? t("ui.stands.plain.line_off") : t("ui.stands.plain.line_keep")}</span>
  {:else if !lv.edit}
    <span class="muted">{t("ui.stands.group_follows", { name: lv.sourceName || device.partOf?.name || "" })}</span>
  {:else}
    <span class="chips">
      {#each STANDS as stand, i (stand)}
        {@const c = lv.colour[i]}
        <span class="chip"><span class="fill" style="--fill: {25 * (i + 1)}%" aria-hidden="true"></span>{t(`stands.stand.${stand}`)} <b>{lv.pct[i]}%</b>{#if lv.can !== "dim"}<span class="swatch" class:rhythm={c.rhythm} style={c.kelvin !== undefined ? `background: ${kelvinCss(c.kelvin)}` : c.name ? `background: ${c.name}` : ""}
              title={c.rhythm ? t("ui.stands.rhythm") : c.kelvin !== undefined ? `${c.kelvin} K` : t(`stands.colour.${c.name}`)}></span>{/if}</span>
      {/each}
    </span>
    <span><span class="tag" class:own={lv.source === "own"} class:start={lv.source === "start"}>{t(`ui.stands.source.${lv.source}`, { name: lv.sourceName })}</span></span>
  {/if}
{/snippet}

<!-- A group with what is inside it (E-312): smaller groups, then its own devices. -->
{#snippet groupNode(g: DeviceRow, tree: GroupTree)}
  {@const info = g.group!}
  <details class="grp" open={byHand.is(`g:${g.id}`, false)}>
    <summary onclick={(e) => byHand.flip(`g:${g.id}`, e)}>
      <span class="gname">{g.name}</span>
      <span class="tag" class:radio={info.radio}>{info.sourceName}</span>
      <span class="muted">{t("ui.devices.group.members", { n: info.members.length })} · {info.steers ? t("ui.devices.group.steers") : t("ui.devices.group.gathers")}{!info.steers && info.why ? ` – ${info.why}` : ""}</span>
    </summary>
    <div class="inside">
      <ul>{@render row(g)}</ul>
      <!-- What is in it, indented under the group itself: its children. -->
      {#if tree.children.get(g.id)?.length || tree.members.get(g.id)?.length}
        <div class="kids" role="group" aria-label={t("ui.devices.group.part_of", { name: g.name })}>
          {#each tree.children.get(g.id) ?? [] as child (child.id)}{@render groupNode(child, tree)}{/each}
          {#if tree.members.get(g.id)?.length}<ul>{#each tree.members.get(g.id) ?? [] as item (item.id)}{@render row(item)}{/each}</ul>{/if}
        </div>
      {/if}
      {#if tree.elsewhere.get(g.id)}<p class="muted">{t("ui.devices.group.also", { names: (tree.elsewhere.get(g.id) ?? []).join(", ") })}</p>{/if}
      {#if !info.members.length}<p class="muted">{t("ui.devices.group.unknown")}</p>{/if}
    </div>
  </details>
{/snippet}

<section class="devices" class:compact>
  {#if devices?.length}
    <!-- Only what you must do something about (QA 3, schermen 10): how many are not there. -->
    {@const gone = devices.filter(unavailable).length}
    {#if gone}<button class="link gone" onclick={() => (chosen = { status: ["unavailable"] })}>{t("ui.devices.unavailable_n", { count: gone }, gone)}</button>{/if}
  {/if}
  <Filters items={devices ?? []} {defs} {text} bind:chosen bind:search={filter} searchLabel={t("ui.devices.search")} {t}
    bind:group={view} groups={VIEWS.map((value) => ({ value, label: t(`ui.filter.title.${value}`) }))} />
  {#if devices === undefined}
    <p class="muted">…</p>
  {:else}
    <ListTools def={list} items={shown} total={devices.length} page={paged} {lang} {t} />
    {#if !shown.length}<p class="muted">{t("ui.filter.none")}</p>{/if}
    {#if paged}
      <ul>{#each paged.items as item (item.id)}{@render row(item)}{/each}</ul>
      <Pager list="devices" page={paged.page} pages={paged.pages} total={shown.length} {more} {lang} {t}
        ongo={(n) => (page = n)} onmore={() => (more = true)} />
    {/if}
    {#each paged ? [] : groups as [key, list] (`${view}:${key}`)}
      <details class="group" open={byHand.is(`${view}:${key}`, opened)}>
        <summary onclick={(e) => byHand.flip(`${view}:${key}`, e)}>{#if view === "step"}<StepPill step={key} name={title(key)} />{:else}<span>{title(key)}</span>{/if} <span class="count">{list.length}</span></summary>
        {#if view === "room"}
          {@const tree = groupTree(list, devices ?? [])}
          {#each tree.top as g (g.id)}{@render groupNode(g, tree)}{/each}
          {#each grouped(tree.loose, (d) => d.device) as [device, entities] (device)}
            <!-- One entity with the device's own name needs no heading of its own. -->
            {#if !(entities.length === 1 && entities[0].name === device)}<h4>{device || t("ui.filter.nothing")}</h4>{/if}
            <ul>{#each entities as item (item.id)}{@render row(item)}{/each}</ul>
          {/each}
        {:else}
          <ul>{#each list as item (item.id)}{@render row(item)}{/each}</ul>
        {/if}
      </details>
    {/each}
  {/if}
</section>

{#if levelsLamp?.levels}
  <Sheet title={t("ui.stands.title", { name: levelsLamp.name })} closeLabel={t("ui.close")} onclose={() => { roomDone(); levelsOf = null; }}>
    {#key levelsLamp.id}<LightLevels lamp={levelsLamp} {t} onsend={onlevels} />{/key}
    <!-- Proef een stand for every lamp of the room, here where you set the standen (QA 3). -->
    {@const area = levelsLamp.areaId}
    {#if area && levelsLamp.levels.can !== "onoff"}
      <div class="roomtry" role="group" aria-label={t("ui.stands.room_try_label", { room: levelsLamp.area })}>
        <span class="muted">{t("ui.stands.room_try")}:</span>
        {#each STANDS as stand, i (stand)}
          <button aria-pressed={roomTry?.area === area && roomTry.stand === i} onclick={() => tryRoom(area, i)}>{t(`stands.stand.${stand}`)}</button>
        {/each}
        {#if roomTry?.area === area}<button class="done" onclick={roomDone}>{t("ui.stands.done")}</button>{/if}
      </div>
    {/if}
  </Sheet>
{/if}

<style>
  .devices { display: flex; flex-direction: column; gap: 10px; }
  .group { border-top: 1px solid var(--line); padding: 4px 0; }
  .group summary { min-height: 44px; display: flex; align-items: center; gap: 8px; cursor: pointer; font-weight: 600; }
  .count { color: var(--muted); font-weight: 400; font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); }
  h4 { margin: 10px 0 6px; font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; }
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  li { display: flex; gap: 10px; justify-content: space-between; align-items: center; padding: 10px 12px; border-radius: 14px; background: var(--surface); border: 1px solid var(--line); flex-wrap: wrap; }
  li.excluded { opacity: 0.7; }
  .compact li { padding: 4px 10px; gap: 6px; } /* the same details, less room (B-198) */
  .compact ul { gap: 3px; }
  .why { flex-basis: 100%; margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); line-height: 1.5; }
  .main { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1 1 220px; }
  .icon { width: 34px; height: 34px; border-radius: 10px; background: var(--raised); border: 1px solid var(--line); display: inline-flex; align-items: center; justify-content: center; flex: none; align-self: flex-start; color: var(--muted); }
  .icon.lamp { color: var(--learning); }
  .icon.on { color: var(--accent); }
  .other-icon { min-height: 32px; font-size: calc(13px * var(--text-scale)); font-weight: 500; }
  .muted { color: var(--muted); font-size: calc(14px * var(--text-scale)); margin: 0; }
  .mono { font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); color: var(--muted); overflow-wrap: anywhere; }
  .side { display: flex; gap: 10px; align-items: center; }
  .side button { min-height: 40px; padding: 0 12px; border-radius: 20px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; }
  .toggle { display: flex; gap: 6px; align-items: center; font-size: calc(14px * var(--text-scale)); min-height: 40px; }
  .toggle input { width: 20px; height: 20px; accent-color: var(--accent); }
  .grp { border: 1px solid var(--line); border-radius: 14px; padding: 2px 10px; margin: 6px 0; background: var(--raised); }
  .grp summary { min-height: 44px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; cursor: pointer; }
  .gname { font-weight: 600; }
  .inside { display: flex; flex-direction: column; gap: 6px; padding: 0 0 10px; }
  /* A group's children sit indented under it, along a line (the administrator's wish). */
  .kids { display: flex; flex-direction: column; gap: 6px; margin-left: 12px; padding-left: 16px; border-left: 2px solid var(--line); }
  .compact .kids { margin-left: 8px; padding-left: 10px; }
  .tag { font-size: calc(12px * var(--text-scale)); padding: 2px 8px; border-radius: 10px; background: var(--surface); border: 1px solid var(--line); white-space: nowrap; }
  .tag.radio { color: var(--accent); border-color: var(--accent); }
  .tag.own { color: var(--accent); border-color: var(--accent); }
  .tag.start { color: var(--learning); border-color: var(--learning); }
  /* Standen voor lampen (B-413) */
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 2px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; font-size: calc(13px * var(--text-scale)); padding: 2px 8px 2px 6px; border-radius: 14px; background: var(--raised); border: 1px solid var(--line); white-space: nowrap; }
  .chip b { font-family: var(--font-mono); font-weight: 500; font-size: calc(12px * var(--text-scale)); }
  .fill { width: 12px; height: 12px; border-radius: 50%; border: 1.5px solid var(--learning); background: color-mix(in srgb, var(--learning) var(--fill), transparent); }
  .swatch { width: 11px; height: 11px; border-radius: 3px; border: 1px solid var(--edge); }
  .swatch.rhythm { border-radius: 50%; background: conic-gradient(from 200deg, #ffa652, #ffe4ce, #f8f8ff, #ffa652); }
  .side button.levels { border-color: var(--accent); color: var(--accent); }
  .roomtry { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 0 0 8px; }
  .roomtry button { min-height: 36px; padding: 0 12px; border-radius: 18px; border: 1px solid var(--edge); background: var(--raised); cursor: pointer; font-size: calc(14px * var(--text-scale)); }
  .roomtry button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
  .roomtry button.done { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 600; }
</style>

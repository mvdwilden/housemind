<script lang="ts" module>
  import type { TidyItem } from "../lib/types";
  // The entity id is shown for one entity; for a cause the list below says enough.
  function oneEntity(item: TidyItem): boolean {
    return item.kind === "unavailable" || item.kind === "not_provided" || item.kind === "duplicate" || item.kind === "meter_still";
  }
  // A bundle of one integration (B-258): its entities are the list below the card.
  function bundle(item: TidyItem): boolean {
    return item.kind.endsWith("_group") || item.kind === "integration_silent";
  }
  // The kinds in the overview and the filter, a bundle counted with its kind.
  const KINDS = [
    "integration_down", "integration_silent", "device_down", "not_provided", "unavailable", "meter_still", "energy", "duplicate", "missing_ref",
  ];
  const kindOf = (item: TidyItem) => (item.kind.startsWith("energy_") ? "energy" : item.kind.replace(/_group$/, ""));
  // What the search field looks in: the names and ids, also of a bundle's entities.
  const searchText = (item: TidyItem) =>
    [item.name, item.target, item.entity?.entityId ?? "", ...item.follows.map((e) => `${e.name} ${e.entityId}`)].join(" ");
  // A name from Home Assistant may start small ("tuinsensor"); a title never does.
  function capital(text: string): string {
    return text.charAt(0).toLocaleUpperCase() + text.slice(1);
  }
</script>

<script lang="ts">
  // Home Assistant netjes (E-351 – E-354, B-245, deel A): what is broken in Home Assistant, one
  // card per cause, heavy ones first. HouseMind only looks here; every card opens the right place
  // in Home Assistant, where the administrator decides (E-03).
  import DeviceIcon from "./DeviceIcon.svelte";
  import AskFirst from "./AskFirst.svelte";
  import Filters from "./Filters.svelte";
  import Overview from "./Overview.svelte";
  import Pager from "./Pager.svelte";
  import SystemRing from "./SystemRing.svelte";
  import { listChoice } from "../lib/device.svelte";
  import { filtered, type Chosen, type FilterDef } from "../lib/filters";
  import { pageOf, ROWS } from "../lib/lists";
  import { openBeside } from "../lib/openha";
  import { checkedAfter, every, keepLine, size, tidyLines, tidyTitle, unusedLine } from "../lib/tidy";
  import type { TidyView } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    tidy: TidyView | undefined;
    lang: string;
    t: T;
    send: (message: Record<string, unknown>) => void;
  }
  let { tidy, lang, t, send }: Props = $props();

  let checking = $state(false);
  $effect(() => {
    if (tidy) checking = false; // a new answer came in
  });
  const when = (iso: string) => new Date(iso).toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" });

  function check(): void {
    checking = true;
    send({ type: "tidy", do: "check" });
  }

  // From six cards on: the overview per kind, the filter bar and pages (B-245, B-258, E-325).
  const items = $derived(tidy?.broken ?? []);
  const bar = $derived(items.length > 5);
  let chosen = $state<Chosen>({});
  let search = $state("");
  let page = $state(1);
  let more = $state(false);
  const kindDef = $derived<FilterDef<TidyItem>>({
    key: "kind", title: t("ui.filter.title.kind"), values: (i) => [kindOf(i)], label: (v) => t(`ui.tidy.short.${v}`), order: KINDS,
  });
  const defs = $derived<FilterDef<TidyItem>[]>([
    kindDef,
    { key: "source", title: t("ui.tidy.source"), values: (i) => (i.source ? [i.source.name ?? i.source.id] : []), label: (v) => v, skipNone: true },
    { key: "room", title: t("ui.filter.title.room"), values: (i) => i.rooms, label: (v) => v || t("ui.filter.no_room") },
  ]);
  const shown = $derived(filtered(items, defs, chosen, search, searchText));
  const paged = $derived(pageOf(shown, page, listChoice("tidy").rows ?? ROWS[0], more));
  $effect(() => {
    void [search, chosen];
    page = 1;
    more = false;
  });

  // 'Bewust uit' (B-239): one card at a time, with the hours from a pattern ready.
  let marking = $state<string | null>(null);
  let how = $state("always");
  let start = $state("22:00");
  let end = $state("07:00");
  let until = $state("");
  function openMark(item: TidyItem): void {
    marking = item.key;
    how = item.suggestMark?.how ?? "always";
    start = item.suggestMark?.start ?? "22:00";
    end = item.suggestMark?.end ?? "07:00";
    until = "";
  }
  function saveMark(item: TidyItem): void {
    send({ type: "tidy", do: "mark", target: item.markTarget, how, start, end, until });
    marking = null;
  }
  const act = (message: Record<string, unknown>) => send({ type: "tidy", ...message });
  // A choice about the backups is about the system, not about a thing with a name (E-397).
  function decidedName(d: NonNullable<TidyView["decided"]>[number]): string {
    if (d.choice === "own_backups") return t("ui.tidy.system.backup");
    if (d.key.startsWith("backup_keep_all:")) return t("ui.tidy.system.keep_all_title");
    if (d.key.startsWith("backup_loose:")) return t("ui.tidy.system.loose_kept", { count: d.count ?? 0 }, d.count ?? 0);
    return d.name;
  }

  // Automations that have not run for a long time (B-240): pages from six on, like the rest.
  const unused = $derived(tidy?.unused ?? []);
  let unusedPage = $state(1);
  let unusedMore = $state(false);
  const unusedPaged = $derived(pageOf(unused, unusedPage, listChoice("tidy").rows ?? ROWS[0], unusedMore));
  const seasonal = $derived(unused.some((u) => u.season));

  // The system (B-241) and the last update (B-242).
  const percent = (share: number) => share.toLocaleString(lang, { style: "percent", maximumFractionDigits: 0 });
  // Home Assistant's own words for a size ("40.7 GB"), with the number in the reader's way.
  const local = (text: string) => text.replace(/^[0-9]+(\.[0-9]+)?/, (n) => Number(n).toLocaleString(lang, { maximumFractionDigits: 1 }));
  const PARTS = ["core", "os", "supervisor"];
  function busiest(list: NonNullable<NonNullable<TidyView["system"]>["database"]["busiest"]>): string {
    const names = list.map((b) => t("ui.tidy.system.db_every", { name: b.name, time: every(b.every, t, lang) }));
    return t("ui.tidy.system.db_busiest", { list: new Intl.ListFormat(lang, { type: "conjunction" }).format(names) });
  }
  const date = (iso: string) => new Date(iso).toLocaleDateString(lang, { dateStyle: "medium" });
  function markLine(m: TidyView["marks"][number]): string {
    if (m.how === "hours") return t("ui.tidy.mark.hours_line", { start: m.start ?? "", end: m.end ?? "" });
    if (m.how === "until") return t("ui.tidy.mark.until_line", { date: m.until ? date(m.until) : "" });
    return t(`ui.tidy.mark.${m.how}`);
  }
</script>

<section data-section="tidy">
  <p class="muted">{t("ui.tidy.intro")}</p>
  {#if tidy}
    <div class="status">
      <span>
        {#if tidy.checkedAt}
          <b>{t("ui.tidy.count", {}, tidy.count)}</b>
          <span class="muted inline"> · {t("ui.tidy.checked", { when: when(tidy.checkedAt) })}</span>
        {:else}
          <span class="muted inline">{t("ui.tidy.not_yet")}</span>
        {/if}
      </span>
      <button type="button" onclick={check} disabled={checking}>{checking ? t("ui.tidy.checking") : t("ui.tidy.check_now")}</button>
    </div>

    {#if tidy.system}
      <!-- The system (B-241) first (E-451): the ring, then three tiles; HouseMind changes nothing here. -->
      {@const sys = tidy.system}
      <!-- Only what is wrong (QA 3, schermen 26): Home Assistant shows backups and storage
           itself; a tile here is a reason to act. -->
      {@const wrong = {
        backup: sys.backup.state === "old" || sys.backup.state === "none" || Boolean(sys.backup.heavy),
        database: sys.database.state === "grows",
        disk: Boolean(sys.disk.heavy) || (sys.disk.state !== "ok" && sys.disk.state !== "unknown"),
      }}
      <h3>{t("ui.tidy.system.title")}</h3>
      {#if !wrong.backup && !wrong.database && !wrong.disk}
        <p class="muted">{t("ui.tidy.system.all_well")}</p>
      {:else}
      <SystemRing {sys} {lang} {t} />
      <div class="tiles">
        {#if wrong.backup}
        <article class="tile" class:heavy={sys.backup.heavy}>
          <p class="label">{t("ui.tidy.system.backup")}</p>
          <p class="value">
            {#if sys.backup.days === undefined}{t("ui.tidy.system.unknown")}
            {:else if sys.backup.days === 0}{t("ui.tidy.system.backup_today")}
            {:else}{t("ui.tidy.system.backup_age", { count: sys.backup.days }, sys.backup.days)}{/if}
          </p>
          <p class="line">
            {#if sys.backup.state === "old"}{t("ui.tidy.system.backup_old", { count: sys.backup.days ?? 0 })}
            {:else}{t(`ui.tidy.system.backup_${sys.backup.state}`)}{/if}
          </p>
          {#if sys.backup.keep}<p class="muted">{keepLine(sys.backup.keep, t, lang)}</p>{/if}
          <div class="actions">
            <button type="button" onclick={() => openBeside("/config/backup")}>{t("ui.tidy.system.open")}</button>
            {#if sys.backup.state === "old" || sys.backup.state === "none"}
              <button type="button" class="link" onclick={() => act({ do: "own_backups", key: "backup:home_assistant" })}>{t("ui.tidy.system.own_backups")}</button>
            {/if}
          </div>
        </article>
        {/if}
        {#if wrong.database}
        <article class="tile">
          <p class="label">{t("ui.tidy.system.database")}</p>
          <p class="value">{sys.database.bytes !== undefined ? size(sys.database.bytes, lang) : t("ui.tidy.system.unknown")}</p>
          {#if sys.database.state === "unknown"}
            <p class="line">{t("ui.tidy.system.db_unknown")}</p>
          {:else}
            <p class="line">
              {#if sys.database.growth === null || sys.database.growth === undefined}{t("ui.tidy.system.db_growth_later")}
              {:else}{t(sys.database.state === "grows" ? "ui.tidy.system.db_grows" : "ui.tidy.system.db_growth", { size: size(Math.max(0, sys.database.growth), lang) })}{/if}
            </p>
            {#if sys.database.busiest?.length}<p class="muted">{busiest(sys.database.busiest)}</p>{/if}
          {/if}
          <div class="actions"><button type="button" onclick={() => openBeside("/config/storage")}>{t("ui.tidy.system.open")}</button></div>
        </article>
        {/if}
        {#if wrong.disk}
        <article class="tile" class:heavy={sys.disk.heavy}>
          <p class="label">{t("ui.tidy.system.disk")}</p>
          <p class="value">{sys.disk.share !== undefined ? t("ui.tidy.system.disk_value", { pct: percent(sys.disk.share) }) : t("ui.tidy.system.unknown")}</p>
          {#if sys.disk.usedText && sys.disk.totalText}
            <p class="muted">{t("ui.tidy.system.disk_of", { used: local(sys.disk.usedText), total: local(sys.disk.totalText) })}</p>
          {/if}
          {#if sys.disk.room !== undefined}
            <!-- The margin in backups (E-397): how many more fit, rounded down. -->
            <p class="line">{sys.disk.room > 0 ? t("ui.tidy.system.disk_room", { count: sys.disk.room }, sys.disk.room) : t("ui.tidy.system.disk_room_none")}</p>
          {/if}
          <p class="line">{t(sys.disk.state === "ok" && sys.disk.room !== undefined ? "ui.tidy.system.disk_ok_room" : `ui.tidy.system.disk_${sys.disk.state}`)}</p>
          {#if sys.disk.looseBytes}<p class="muted">{t("ui.tidy.system.disk_loose", { size: size(sys.disk.looseBytes, lang) })}</p>{/if}
          <div class="actions"><button type="button" onclick={() => openBeside("/config/storage")}>{t("ui.tidy.system.open")}</button></div>
        </article>
        {/if}
      </div>
      {/if}
      {#if sys.keepAll}
        <!-- Home Assistant keeps every backup (E-397): its own setting, so it opens there. -->
        <article class="card">
          <p class="title">{t("ui.tidy.system.keep_all_title")}</p>
          <p class="line">{t("ui.tidy.system.keep_all_text")}</p>
          <div class="actions">
            <button type="button" onclick={() => openBeside("/config/backup/settings")}>{t("ui.tidy.open")}</button>
            <button type="button" class="link" onclick={() => act({ do: "leave", key: "backup_keep_all:home_assistant" })}>{t("ui.tidy.leave")}</button>
          </div>
        </article>
      {/if}
      {#if sys.loose.length}
        <!-- Old loose backups Home Assistant does not clear itself (E-397); HouseMind never deletes. -->
        <article class="card">
          <p class="title">{t("ui.tidy.system.loose_title", { count: sys.loose.length, size: size(sys.loose.reduce((n, b) => n + b.size, 0), lang) }, sys.loose.length)}</p>
          <p class="line">{t("ui.tidy.system.loose_text")}</p>
          <details>
            <summary>{t("ui.tidy.system.loose_which")}</summary>
            <ul>
              {#each sys.loose as b (b.id)}
                <li>{b.name} · {new Date(b.date).toLocaleDateString(lang, { dateStyle: "medium" })} · {size(b.size, lang)}</li>
              {/each}
            </ul>
          </details>
          <div class="actions">
            <button type="button" onclick={() => openBeside("/config/backup/backups")}>{t("ui.tidy.open")}</button>
            <button type="button" class="link" onclick={() => act({ do: "keep_backups", key: "backup_loose:local" })}>{t("ui.tidy.system.keep_backups")}</button>
          </div>
        </article>
      {/if}
      <p class="muted">{t("ui.tidy.system.note")}</p>
    {/if}

    {#if tidy.checkedAt}
      <h3>{t("ui.tidy.broken")} <span class="count">{tidy.broken.length}</span></h3>
      {#if tidy.broken.length === 0}
        <p class="muted">{t("ui.tidy.none")}</p>
      {/if}
      {#if bar}
        <Overview {items} def={kindDef} bind:chosen {lang} {t} />
        <Filters {items} {defs} text={searchText} bind:chosen bind:search searchLabel={t("ui.tidy.search")} {t} />
        {#if !shown.length}<p class="muted">{t("ui.filter.none")}</p>{/if}
      {/if}
      {#each paged.items as item (item.key)}
        <article class="card" class:heavy={item.heavy}>
          {#if item.heavy}<span class="badge">{t("ui.tidy.heavy")}</span>{/if}
          <!-- What the card is about, as its icon (E-512). -->
          <p class="title">{#if item.entity?.icon}<span class="ic" aria-hidden="true"><DeviceIcon name={item.entity.icon} size={20} /></span>{/if}{capital(tidyTitle(item, t))}</p>
          <p class="when">{t("ui.tidy.since", { when: when(item.since) })}</p>
          {#each tidyLines(item, t) as line (line)}<p class="line">{line}</p>{/each}
          {#if item.entity && oneEntity(item)}<p class="mono">{item.entity.entityId}</p>{/if}
          {#if item.suggest && item.kind === "missing_ref"}<p class="hint">{t("ui.tidy.like", { name: `${item.suggest.name} (${item.suggest.entityId})` })}</p>{/if}
          {#if item.followsCount > 0}
            <details>
              <summary>{bundle(item) ? t("ui.tidy.members", { count: item.followsCount }) : t("ui.tidy.follows", { count: item.followsCount + item.dependsCount })}</summary>
              <ul>
                {#each item.follows as e (e.entityId)}<li>{e.name}{#if e.room} · {e.room}{/if}</li>{/each}
                {#if item.followsCount > item.follows.length}<li>…</li>{/if}
                {#each item.depends as name (name)}<li>{name}</li>{/each}
              </ul>
            </details>
          {/if}
          {#if item.suggestMark && item.can.includes("mark")}
            <p class="chip">{item.suggestMark.how === "dark" ? t("ui.tidy.pattern_dark") : t("ui.tidy.pattern", { start: item.suggestMark.start ?? "", end: item.suggestMark.end ?? "" })}</p>
          {/if}
          {#if item.review}<p class="line strong">{t("ui.tidy.review")}</p>{/if}
          {#if marking === item.key}
            <fieldset class="mark">
              <legend>{t("ui.tidy.mark.title")}</legend>
              {#each ["always", "dark", "hours", "until"] as choice (choice)}
                <label class="check"><input type="radio" name="how-{item.key}" value={choice} bind:group={how} /> {t(`ui.tidy.mark.${choice}`)}</label>
                {#if choice === "hours" && how === "hours"}
                  <span class="times"><input type="time" bind:value={start} aria-label={t("ui.tidy.mark.from")} /> – <input type="time" bind:value={end} aria-label={t("ui.tidy.mark.to")} /></span>
                {/if}
                {#if choice === "until" && how === "until"}
                  <input type="date" bind:value={until} aria-label={t("ui.tidy.mark.until")} />
                {/if}
              {/each}
              <p class="muted">{t("ui.tidy.mark.review_note")}</p>
              <div class="actions">
                <button type="button" class="primary" disabled={how === "until" && !until} onclick={() => saveMark(item)}>{t("ui.tidy.mark.save")}</button>
                <button type="button" onclick={() => (marking = null)}>{t("ui.tidy.mark.cancel")}</button>
              </div>
            </fieldset>
          {/if}
          <div class="actions">
            {#if item.can.includes("review") && item.review}
              <button type="button" class="primary" onclick={() => act({ do: "review_ok", target: item.review })}>{t("ui.tidy.review_yes")}</button>
              <button type="button" onclick={() => act({ do: "unmark", target: item.review })}>{t("ui.tidy.review_no")}</button>
            {/if}
            {#if item.can.includes("mark") && marking !== item.key}
              <button type="button" class:primary={!!item.suggestMark} onclick={() => openMark(item)}>{t("ui.tidy.mark.button")}</button>
            {/if}
            {#if item.can.includes("off")}
              <AskFirst text={t("ui.tidy.off")} label={t("ui.tidy.off")} question={t("ui.tidy.off_ask")} onyes={() => act({ do: "off", key: item.key })} {t} />
            {/if}
            {#if item.can.includes("off_all")}
              <AskFirst text={t("ui.tidy.off_all", { count: item.followsCount })} label={t("ui.tidy.off_all", { count: item.followsCount })} question={t("ui.tidy.off_all_ask", { count: item.followsCount })} onyes={() => act({ do: "off_all", key: item.key })} {t} />
            {/if}
            {#if item.can.includes("not_duplicate")}
              <button type="button" onclick={() => act({ do: "not_duplicate", key: item.key })}>{t("ui.tidy.not_duplicate")}</button>
            {/if}
            {#if item.can.includes("leave")}
              <button type="button" class="link" onclick={() => act({ do: "leave", key: item.key })}>{t("ui.tidy.leave")}</button>
            {/if}
            <button type="button" onclick={() => openBeside(item.open)}>{t("ui.tidy.open")}</button>
          </div>
        </article>
      {/each}
      {#if bar}
        <Pager list="tidy" page={paged.page} pages={paged.pages} total={shown.length} {more} {lang} {t}
          ongo={(n) => (page = n)} onmore={() => (more = true)} />
      {/if}

      <!-- Automations that have not run for a long time (B-240): never deleted by HouseMind. -->
      <h3>{t("ui.tidy.unused.title")} <span class="count">{unused.length}</span></h3>
      {#if !unused.length}<p class="muted">{t("ui.tidy.unused.none")}</p>{/if}
      {#each unusedPaged.items as item (item.key)}
        <article class="card">
          <p class="title">{capital(item.name)}</p>
          <p class="line">{unusedLine(item, t)}</p>
          {#if item.season}<p class="muted">{t("ui.tidy.unused.season_line")}</p>{/if}
          <div class="actions">
            {#if item.can.includes("off")}
              <AskFirst text={t("ui.tidy.off")} label={t("ui.tidy.off")} question={t("ui.tidy.off_ask")} onyes={() => act({ do: "off", key: item.key })} {t} />
            {/if}
            {#if item.can.includes("keep_on")}
              <button type="button" class="link" onclick={() => act({ do: "keep_on", key: item.key })}>{t("ui.tidy.keep_on")}</button>
            {/if}
            {#if item.kind === "unused_off"}
              <button type="button" onclick={() => openBeside(item.open)}>{t("ui.tidy.delete_in_ha")}</button>
            {:else}
              <button type="button" onclick={() => openBeside(item.open)}>{t("ui.tidy.open")}</button>
            {/if}
            {#if item.can.includes("keep")}
              <button type="button" class="link" onclick={() => act({ do: "keep", key: item.key })}>{t("ui.tidy.keep")}</button>
            {/if}
          </div>
        </article>
      {/each}
      {#if unused.length > 5}
        <Pager list="tidy" page={unusedPaged.page} pages={unusedPaged.pages} total={unused.length} more={unusedMore} {lang} {t}
          ongo={(n) => (unusedPage = n)} onmore={() => (unusedMore = true)} />
      {/if}
      {#if unused.length || seasonal}<p class="muted">{t("ui.tidy.unused.season")}</p>{/if}
    {/if}


    {#if tidy.checkedAt}
      <!-- After the last update (B-242): what no longer works, and what came back. -->
      <h3>{t("ui.tidy.update.title")}</h3>
      {#if !tidy.update}
        <p class="muted">{t("ui.tidy.update.none")}</p>
      {:else}
        {@const up = tidy.update}
        <article class="card">
          {#each PARTS.filter((p) => up.to[p]) as part (part)}
            <p class="title">{t("ui.tidy.update.line", { part: t(`ui.tidy.update.part.${part}`), from: up.from[part] ?? "", to: up.to[part] })}</p>
          {/each}
          <p class="when">{when(up.at)}</p>
          <p class="line">{up.checks.length ? checkedAfter(up.checks.length, t, lang) : t("ui.tidy.update.first")}</p>
          {#if up.next}<p class="muted">{t("ui.tidy.update.next", { when: when(up.next) })}</p>{/if}
          {#if !up.known}
            <p class="line">{t("ui.tidy.update.no_before")}</p>
          {:else if up.checks.length && !up.lost.length && !up.backCount}
            <p class="line">{t("ui.tidy.update.all_well")}</p>
          {/if}
        </article>
        {#if up.lost.length}
          <p class="sub">{t("ui.tidy.update.lost", { count: up.lost.reduce((n, g) => n + (g.down ? 1 : g.count), 0) })}</p>
          {#each up.lost as group (group.source)}
            <article class="card heavy">
              <p class="title">
                {#if group.down}{t("ui.tidy.update.lost_down", { name: group.name })}
                {:else if group.count === 1 && group.entities.length}{t("ui.tidy.update.lost_one", { name: group.entities[0].name })}
                {:else}{t("ui.tidy.update.lost_some", { name: group.name, count: group.count }, group.count)}{/if}
              </p>
              {#if group.entities.length && !(group.count === 1 && !group.down)}
                <details>
                  <summary>{t("ui.tidy.members", { count: group.count })}</summary>
                  <ul>{#each group.entities as e (e.entityId)}<li>{e.name}{#if e.room} · {e.room}{/if}</li>{/each}</ul>
                </details>
              {/if}
              <div class="actions"><button type="button" onclick={() => openBeside(group.open)}>{t("ui.tidy.open")}</button></div>
            </article>
          {/each}
        {/if}
        {#if up.backCount}
          <p class="sub">{t("ui.tidy.update.back", { count: up.backCount })}</p>
          <ul class="rows">{#each up.back as e (e.entityId)}<li><span class="what"><b>{e.name}</b>{#if e.room}<span class="muted">{e.room}</span>{/if}</span></li>{/each}</ul>
        {/if}
      {/if}
    {/if}

    {#if tidy.off?.length}
      <!-- What HouseMind switched off after a yes: always back (B-243). -->
      <h3>{t("ui.tidy.off_title")} <span class="count">{tidy.off.length}</span></h3>
      <ul class="rows">
        {#each tidy.off as row (row.target)}
          <li>
            <span class="what"><b>{row.name}</b><span class="muted">{t(`ui.tidy.off_reason.${row.reason}`)} · {t("ui.tidy.off_at", { when: when(row.at) })}</span></span>
            <button type="button" onclick={() => act({ do: "on", target: row.target })}>{t("ui.tidy.on")}</button>
          </li>
        {/each}
      </ul>
      <p class="muted">{t("ui.tidy.on_note")}</p>
      {#if tidy.off.length > 1}
        <button type="button" class="link" onclick={() => act({ do: "all_on" })}>{t("ui.tidy.all_on")}</button>
      {/if}
    {/if}

    {#if tidy.decided?.length || tidy.marks?.length}
      <!-- What the administrator decided, folded: each can be undone (B-239, B-244). -->
      <details class="decided">
        <summary>{t("ui.tidy.decided", { count: (tidy.decided?.length ?? 0) + (tidy.marks?.length ?? 0) })}</summary>
        <ul class="rows">
          {#each tidy.marks ?? [] as m (m.target)}
            <li>
              <span class="what"><b>{m.name}</b><span class="muted">{t("ui.tidy.mark.title")}: {markLine(m)} · {t("ui.tidy.decided_at", { when: date(m.at) })}</span></span>
              <button type="button" class="link" onclick={() => act({ do: "unmark", target: m.target })}>{t("ui.tidy.undo")}</button>
            </li>
          {/each}
          {#each tidy.decided ?? [] as d (d.key)}
            <li>
              <span class="what"><b>{decidedName(d)}</b><span class="muted">{t(`ui.tidy.choice.${d.choice}`)} · {t("ui.tidy.decided_at", { when: date(d.at) })}</span></span>
              <button type="button" class="link" onclick={() => act({ do: "undo_choice", key: d.key })}>{t("ui.tidy.undo")}</button>
            </li>
          {/each}
        </ul>
      </details>
    {/if}
  {/if}
</section>

<style>
  .ic { display: inline-flex; vertical-align: -4px; margin-right: 8px; color: var(--muted); }
  section { display: flex; flex-direction: column; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
  h3 { margin: 12px 0 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; display: flex; gap: 8px; align-items: center; }
  .count { color: var(--text); border: 1px solid var(--line); border-radius: 999px; padding: 0 8px; font-size: calc(12px * var(--text-scale)); letter-spacing: 0; }
  .muted { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .muted.inline { display: inline; }
  .status { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; background: var(--raised); border: 1px solid var(--line); border-radius: 14px; padding: 10px 14px; }
  .card { position: relative; display: flex; flex-direction: column; gap: 4px; background: var(--raised); border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px 12px 18px; overflow: hidden; }
  .card::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: var(--edge); }
  .card.heavy::before { background: var(--important); }
  .badge { align-self: flex-start; font-size: calc(12px * var(--text-scale)); font-weight: 600; color: var(--important); }
  .title { margin: 0; font-weight: 600; }
  .when { margin: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .line { margin: 0; }
  .hint { margin: 0; color: var(--learning); font-size: calc(14px * var(--text-scale)); }
  .mono { margin: 0; font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); color: var(--muted); overflow-wrap: anywhere; }
  details > summary { cursor: pointer; min-height: 40px; display: flex; align-items: center; color: var(--accent); font-weight: 600; }
  details ul { margin: 0 0 4px; padding-left: 20px; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
  button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); cursor: pointer; }
  button:disabled { opacity: 0.6; cursor: default; }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  button.link { border: none; background: none; color: var(--accent); font-weight: 600; padding: 0 4px; }
  .chip { align-self: flex-start; margin: 2px 0; font-size: calc(13px * var(--text-scale)); border: 1px solid var(--learning); color: var(--learning); border-radius: 999px; padding: 2px 10px; }
  .strong { font-weight: 600; }
  .mark { border: 1px solid var(--line); border-radius: 12px; padding: 8px 12px; display: flex; flex-direction: column; gap: 4px; }
  .mark legend { font-weight: 600; padding: 0 4px; }
  .check { display: flex; gap: 10px; align-items: center; min-height: 40px; }
  .check input { width: 20px; height: 20px; accent-color: var(--accent); }
  .times { display: flex; gap: 8px; align-items: center; padding-left: 30px; }
  .mark input[type="time"], .mark input[type="date"] { min-height: 40px; border-radius: 10px; border: 1px solid var(--edge); background: var(--surface); color: var(--text); padding: 0 8px; font: inherit; }
  .rows { list-style: none; margin: 0; padding: 0; background: var(--raised); border: 1px solid var(--line); border-radius: 14px; }
  .rows li { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 8px 14px; border-top: 1px solid var(--line); flex-wrap: wrap; }
  .rows li:first-child { border-top: 0; }
  .what { display: flex; flex-direction: column; min-width: 0; }
  .decided { margin-top: 8px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
  .tile { position: relative; display: flex; flex-direction: column; gap: 4px; background: var(--raised); border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; }
  .tile.heavy { border-color: var(--important); }
  .label { margin: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .value { margin: 0; font-size: calc(20px * var(--text-scale)); font-weight: 600; }
  .sub { margin: 8px 0 0; font-weight: 600; }
</style>

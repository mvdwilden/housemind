<script lang="ts">
  // "Klaar voor autonomie?" (E-256, B-100), like the card of the first version: per part how far
  // HouseMind is and what is still needed – the server computes it all with the thresholds of
  // learning, so this only shows. Compact (levels 1 and 2): one sentence per part. From level 3
  // an overview per part on top (a tap shows only that part) and under it one list of every
  // skill × room, or of the existing automations, with search, Filter and Groeperen, the same
  // as Apparaten en entiteiten (E-314; before, each part had its own fold-out list). From level 3
  // the parts are tiles – name, score, steps, rooms – and only the part you tap has its text and
  // buttons, in one card under the tiles, so the list stays in sight (E-454). Per row
  // the step, the sentence when HouseMind does it (E-385; on Expert the details under it, E-387),
  // the score against its target and the counters; Meer has the plain sentence, the
  // numbers and the buttons. An administrator gets a button to the limit when a part holds
  // something back, can set a step themselves (20.10: a part from level 3, a skill in a room on
  // Expert) and can make HouseMind forget one thing (E-94) – each after a yes.
  // Both lists can also be one flat list in pages (Groeperen: Geen); above them how many, the
  // details per row (Kolommen, Compact) and Exporteren, and an overview per step – per part for
  // the automations (E-325). Compact (Thuis) stays as small as it was: none of that.
  import DeviceIcon from "./DeviceIcon.svelte";
  import { PART_ICONS } from "../lib/deviceicons";
  import type { ReadyAutomation, ReadyPart } from "../lib/types";
  import { partScore } from "../lib/dayview";
  import { listChoice } from "../lib/device.svelte";
  import { filtered, grouped, type Chosen } from "../lib/filters";
  import { pageOf, ROWS } from "../lib/lists";
  import { Opened } from "../lib/opened.svelte";
  import {
    allAutomations,
    automationFilters,
    closestFirst,
    detailFacts,
    flagOf,
    onlyPart,
    READY_STEPS,
    readyRows,
    ringOf,
    rowFilters,
    waits,
    type ReadyAutoRow,
    type ReadyRow,
  } from "../lib/readiness";
  import Filters from "./Filters.svelte";
  import ForgetPair from "./ForgetPair.svelte";
  import ListTools from "./ListTools.svelte";
  import OwnStep from "./OwnStep.svelte";
  import Pager from "./Pager.svelte";
  import Sheet from "./Sheet.svelte";
  import StepPill from "./StepPill.svelte";
  import type { T } from "../lib/ui";

  interface Props {
    parts: ReadyPart[];
    lang: string;
    t: T;
    compact?: boolean;
    onlimit?: () => void;
    send?: (message: Record<string, unknown>) => void;
    onmore?: () => void; // Lees meer: the manual on how a step works (QA 3)
    roomIcons?: Record<string, string>; // a room's icon by its id (E-511)
    others?: ReadyAutomation[]; // the automations no part lists: the one list (E-523)
  }
  let { parts, lang, t, compact = false, onlimit, send, onmore, roomIcons = {}, others = [] }: Props = $props();
  const VIEWS = ["none", "part", "room", "step"]; // none: one flat list in pages (E-325)
  const AUTO_VIEWS = ["none", "part"];
  // Where an automation stands in a word, the same groups as at Huisinstellingen.
  const AUTO_GROUP: Record<string, string> = { ready: "ready", asked: "ready", taken_over: "taken", still_off: "taken", learning: "learning" };
  const byHand = new Opened(); // "Meer" opened by hand stays open while the list redraws

  const rows = $derived(compact ? [] : readyRows(parts));
  const autos = $derived(compact ? [] : allAutomations(parts, others, t("ui.filter.part.other")));
  const kinds = $derived([...(rows.length ? ["skills"] : []), ...(autos.length ? ["automations"] : [])]);
  let kind = $state("skills");
  const showing = $derived(kinds.includes(kind) ? kind : kinds[0]);

  let chosen = $state<Chosen>({});
  let search = $state("");
  let view = $state("part");
  let chosenAutos = $state<Chosen>({});
  let searchAutos = $state("");
  let autoView = $state("part");
  let list = $state<HTMLElement | null>(null);

  const defs = $derived(rowFilters(parts, t));
  const autoDefs = $derived(automationFilters(parts, t));
  const rowText = (r: ReadyRow): string => `${r.what} ${r.room} ${r.partName} ${r.when ?? ""}`;
  const autoText = (a: ReadyAutoRow): string => `${a.name} ${a.partName} ${(a.rooms ?? []).join(" ")}`;
  const rowId = (r: ReadyRow): string => `${r.part}:${r.skill}:${r.area}`;

  // One quiet row per rule, and everything about it in one place (E-501, E-502, B-421 – B-424):
  // beside the list from 980 px wide, else in a sheet from below. The first row is chosen on a
  // computer; on a phone nothing is, until a tap.
  const WIDE = 980;
  let width = $state(0); // the list's own width: Brein beside a side menu is narrower than the window
  const wide = $derived(width >= WIDE);
  let picked = $state<string | null>(null);
  let sheetOpen = $state(false);
  function choose(row: ReadyRow): void {
    picked = rowId(row);
    if (!wide) sheetOpen = true;
  }

  // Kolommen and Exporteren are gone here (E-501): a row and its detail say it all. The
  // number of rows per page is still kept per list.
  const rowChoice = $derived(listChoice("readiness"));
  const autoChoice = $derived(listChoice("readiness_automations"));

  // Closest to the next step first, also in a flat list.
  const shown = $derived(filtered(rows, defs, chosen, search, rowText).sort(closestFirst));
  const shownAutos = $derived(filtered(autos, autoDefs, chosenAutos, searchAutos, autoText));

  // The two quick filters above the list (E-501, B-426): waits for something, and ready.
  const waiting = $derived(rows.filter(waits).length);
  const readyCount = $derived(rows.filter((r) => r.ready || r.heldBack).length);
  function quick(key: "waits" | "ready"): void {
    const on = (chosen[key] ?? []).length > 0;
    chosen = { ...chosen, [key]: on ? [] : ["yes"] };
  }
  let page = $state(1);
  let more = $state(false);
  let autoPage = $state(1);
  let autoMore = $state(false);
  const paged = $derived(view === "none" ? pageOf(shown, page, rowChoice.rows ?? ROWS[0], more) : null);
  const autoPaged = $derived(autoView === "none" ? pageOf(shownAutos, autoPage, autoChoice.rows ?? ROWS[0], autoMore) : null);
  const order = $derived(view === "part" ? parts.map((p) => p.domain) : view === "step" ? READY_STEPS : []);
  const groups = $derived(paged ? [] : grouped(shown, (r) => (view === "part" ? r.part : view === "room" ? r.room : r.step), order));
  // The row on top of the list as it is drawn: chosen by itself on a computer (B-424).
  const topRow = $derived(paged ? paged.items[0] : groups[0]?.[1][0]);
  const pickedRow = $derived(shown.find((r) => rowId(r) === picked) ?? (wide ? topRow : undefined));
  const autoGroups = $derived(autoPaged ? [] : grouped(shownAutos, (a) => a.part, [...parts.map((p) => p.domain), "other"]));
  // The automations the same way (B-427): one row each, everything about it beside or in a sheet.
  const autoId = (a: ReadyAutoRow): string => `${a.part}:${a.id}`;
  let pickedAuto = $state<string | null>(null);
  let autoSheet = $state(false);
  const topAuto = $derived(autoPaged ? autoPaged.items[0] : autoGroups[0]?.[1][0]);
  const pickedAutoRow = $derived(shownAutos.find((a) => autoId(a) === pickedAuto) ?? (wide ? topAuto : undefined));
  function chooseAuto(a: ReadyAutoRow): void {
    pickedAuto = autoId(a);
    if (!wide) autoSheet = true;
  }
  function autoGroup(a: ReadyAutoRow): string {
    return AUTO_GROUP[a.state] ?? "rest";
  }
  // A new search, filter or grouping starts on the first page again.
  $effect(() => {
    void [search, chosen, view];
    page = 1;
    more = false;
  });
  $effect(() => {
    void [searchAutos, chosenAutos, autoView];
    autoPage = 1;
    autoMore = false;
  });

  function share(value: number, target: number): number {
    return target > 0 ? Math.min(100, Math.round((value / target) * 100)) : 0;
  }

  function partName(domain: string): string {
    return parts.find((p) => p.domain === domain)?.name ?? (domain === "other" ? t("ui.filter.part.other") : domain);
  }

  // Uitzetten after a yes, Weer aanzetten at once (E-42), now in this one list (E-523).
  let confirmOff = $state<string | null>(null);
  function turn(automation: ReadyAutoRow, how: "off" | "on"): void {
    send?.({ type: "automation", entity: automation.id, turn: how });
    confirmOff = null;
  }
  const takeOver = (a: ReadyAutoRow): string =>
    a.does ? t(`ui.automations.take_over.${a.does.takeOver}`, { part: t(`ui.filter.part.${a.does.part}`) }) : "";
  function only(domain: string): boolean {
    const now = (showing === "automations" ? chosenAutos : chosen).part ?? [];
    return now.length === 1 && now[0] === domain;
  }

  // A part without a list of its own is chosen for its card only (E-454).
  let lone = $state<string | null>(null);
  const current = $derived(parts.find((p) => only(p.domain)) ?? parts.find((p) => p.domain === lone) ?? null);

  function offDate(part: ReadyPart): string {
    return part.offSince ? new Date(part.offSince).toLocaleDateString(lang, { day: "numeric", month: "short" }) : "";
  }

  function listed(part: ReadyPart): boolean {
    return (part.skills?.length ?? 0) > 0 || (part.automations?.length ?? 0) > 0;
  }

  // A tap on a part: its card under the tiles, and only that part in the list (both lists);
  // again: all.
  function pick(domain: string): void {
    const off = current?.domain === domain;
    const part = parts.find((p) => p.domain === domain);
    lone = !off && part && !listed(part) ? domain : null;
    if (part && !listed(part)) {
      chosen = { ...chosen, part: [] };
      chosenAutos = { ...chosenAutos, part: [] };
      return;
    }
    chosen = off ? { ...chosen, part: [] } : onlyPart(chosen, domain);
    chosenAutos = off ? { ...chosenAutos, part: [] } : onlyPart(chosenAutos, domain);
    if (!off && list && list.getBoundingClientRect().top > innerHeight - 160) {
      const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
      list.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
    }
  }

</script>

{#snippet skillRing(skill: ReadyRow, big: boolean)}
  {@const ring = ringOf(skill)}
  {@const c = 94.25}
  <!-- How often it went well, the target as a dot, what came from the history dotted (B-421). -->
  <svg class="ring {skill.step}" class:big viewBox="0 0 36 36" aria-hidden="true">
    <circle cx="18" cy="18" r="15" class="ring-track" />
    {#if ring.value}
      <circle cx="18" cy="18" r="15" class="ring-fill" stroke-dasharray="{(c * ring.value) / 100} {c}" transform="rotate(-90 18 18)" />
      {#if ring.history}<circle cx="18" cy="18" r="15" class="ring-past" stroke-dasharray="{(c * ring.history) / 100} {c}" transform="rotate(-90 18 18)" />{/if}
    {/if}
    {#if ring.target !== null}
      {@const a = ((ring.target / 100) * 360 - 90) * (Math.PI / 180)}
      <circle cx={18 + 15 * Math.cos(a)} cy={18 + 15 * Math.sin(a)} r="2.4" class="ring-goal" />
    {/if}
    <text x="18" y="21.5" text-anchor="middle" class="ring-text">{ring.value ?? "–"}</text>
  </svg>
{/snippet}

{#snippet skillRow(skill: ReadyRow)}
  {@const id = rowId(skill)}
  {@const flag = flagOf(skill)}
  <li class="skill" class:held={skill.heldBack}>
    <button class="row" aria-current={wide && pickedRow && rowId(pickedRow) === id ? "true" : undefined}
      aria-label="{skill.what}{flag ? ` – ${t(`ui.ready.flag.${flag}`)}` : ''}" onclick={() => choose(skill)}>
      {@render skillRing(skill, false)}
      <span class="main">
        <!-- What it moves and in which room, as icons too (E-511). -->
        <span class="title"><span class="part-icon" aria-hidden="true"><DeviceIcon name={PART_ICONS[skill.part] ?? "device"} size={18} /></span><span class="what">{skill.skillName}</span>{#if skill.room}<span class="room">{#if roomIcons[skill.area]}<DeviceIcon name={roomIcons[skill.area]} size={14} />{/if}{skill.room}</span>{/if}</span>
        <!-- When HouseMind does it, in its own words per step (E-385, E-386); the whole sentence in the detail. -->
        {#if skill.when}<span class="when">{skill.when}</span>{/if}
        {#if flag}<span class="flag under {flag}">{t(`ui.ready.flag.${flag}`)}</span>{/if}
      </span>
      <span class="side"><StepPill step={skill.step} name={skill.stepName} />{#if flag}<span class="flag {flag}">{t(`ui.ready.flag.${flag}`)}</span>{/if}</span>
    </button>
  </li>
{/snippet}

{#snippet skillDetail(skill: ReadyRow)}
  {@const ring = ringOf(skill)}
  <article class="detail" aria-label={skill.what}>
    <h3>{skill.skillName}</h3>
    <p class="sub">{skill.room}{skill.room ? " · " : ""}{skill.partName} <StepPill step={skill.step} name={skill.stepName} /></p>
    {#if skill.when}<p class="sentence">{skill.when}</p>{/if}
    {#if ring.value !== null}
      <div class="meter">
        {@render skillRing(skill, true)}
        <p><b>{ring.value}%</b> {t(ring.yes ? "ui.ready.said_yes" : "ui.ready.went_well")}{#if ring.target !== null}<br /><span class="muted-s">{t("ui.ready.goal", { target: ring.target })}{ring.history ? ` · ${t("ui.history.hatched")}` : ""}</span>{/if}</p>
      </div>
    {/if}
    {#if skill.counters.length}
      <ul class="checks">
        {#each skill.counters as counter (counter.key)}
          <li class:against={counter.key === "corrections"}><span class={counter.met ? "ok" : "todo"} aria-hidden="true">{counter.met ? "✓" : "○"}</span>{counter.text}{#if counter.met}<span class="sr"> {t("ui.ready.met")}</span>{/if}</li>
        {/each}
      </ul>
    {/if}
    {#if skill.byDoor}<p class="by-door">{skill.byDoor}</p>{/if}
    {#if skill.text || skill.steer || skill.question}
      <div class="callout" class:news={skill.ready || skill.heldBack}>
        {#if skill.text}<p>{skill.text}</p>{/if}
        <!-- The automation in Home Assistant that steers it, and the question (E-317). -->
        {#if skill.steer}<p class="steer">{skill.steer.text}</p>{/if}
        {#if skill.question && send}
          {@const q = skill.question}
          {#if q.declined}
            <button class="link" onclick={() => send({ type: "automation", entity: q.id, turn: q.kind, row: true })}>{q.button}</button>
          {:else}
            <div class="steer-ask" role="group" aria-label={q.text}>
              <p>{q.text}</p>
              <div class="buttons">
                <button class="primary" onclick={() => send({ type: "automation", entity: q.id, turn: q.kind, row: true })}>{t("ui.yes")}</button>
                <button onclick={() => send({ type: "automation", entity: q.id, turn: q.kind === "off" ? "leave" : "keep", row: true })}>{t("ui.no")}</button>
              </div>
            </div>
          {/if}
        {/if}
      </div>
    {/if}
    {#if skill.whenDetail || skill.numbers}
      <details class="facts">
        <summary>{t("ui.ready.details")}</summary>
        <!-- On Expert why (E-387), as terms and values instead of one long line (B-423). -->
        {#if skill.whenDetail}
          <dl class="when-detail">
            {#each detailFacts(skill.whenDetail) as [term, value], i (i)}
              {#if term}<dt>{term}</dt><dd>{value}</dd>{:else}<dd class="wide">{value}</dd>{/if}
            {/each}
          </dl>
        {/if}
        {#if skill.numbers}
          <!-- What happened, not a second number for "how sure" beside the ring (QA 3, schermen 20). -->
          <p>{t("ui.ready.numbers", { ...skill.numbers, since: skill.numbers.since ?? "–" })}</p>
        {/if}
      </details>
    {/if}
    {#if (skill.own || skill.forget) && send}
      <div class="foot">
        {#if skill.own}<OwnStep own={skill.own} what={skill.what} {t} {send} />{/if}
        {#if skill.forget}<ForgetPair skill={skill.skill} area={skill.area} what={skill.what} {t} {send} />{/if}
      </div>
    {/if}
  </article>
{/snippet}

{#snippet autoRing(automation: ReadyAutoRow, big: boolean)}
  {@const c = 94.25}
  {@const share = automation.total ? automation.ready / automation.total : 0}
  <!-- How many of its rooms HouseMind can do already (B-427). -->
  <svg class="ring autonomous" class:big viewBox="0 0 36 36" aria-hidden="true">
    <circle cx="18" cy="18" r="15" class="ring-track" />
    {#if automation.ready}<circle cx="18" cy="18" r="15" class="ring-fill" stroke-dasharray="{c * share} {c}" transform="rotate(-90 18 18)" />{/if}
    <text x="18" y="21" text-anchor="middle" class="ring-text small">{automation.total ? `${automation.ready}/${automation.total}` : "–"}</text>
  </svg>
{/snippet}

{#snippet autoRow(automation: ReadyAutoRow)}
  {@const group = autoGroup(automation)}
  <li class="skill">
    <button class="row" aria-current={wide && pickedAutoRow && autoId(pickedAutoRow) === autoId(automation) ? "true" : undefined}
      aria-label="{automation.name} – {t(`ui.automations.group.${group}`)}" onclick={() => chooseAuto(automation)}>
      {@render autoRing(automation, false)}
      <span class="main">
        <span class="title"><span class="part-icon" aria-hidden="true"><DeviceIcon name={PART_ICONS[automation.part] ?? "device"} size={18} /></span><span class="what">{automation.name}</span>{#if automation.rooms?.length}<span class="room">{automation.rooms.join(", ")}</span>{/if}</span>
        <span class="when">{automation.text}</span>
      </span>
      <span class="side"><span class="state {group}">{t(`ui.automations.group.${group}`)}</span></span>
    </button>
  </li>
{/snippet}

{#snippet autoDetail(automation: ReadyAutoRow)}
  {@const group = autoGroup(automation)}
  <article class="detail" aria-label={automation.name}>
    <h3>{automation.name}</h3>
    <p class="sub">{automation.partName}{automation.rooms?.length ? ` · ${automation.rooms.join(", ")}` : ""} <span class="state {group}">{t(`ui.automations.group.${group}`)}</span></p>
    <p class="sentence">{automation.text}</p>
    {#if automation.total}
      <div class="meter">
        {@render autoRing(automation, true)}
        <p><b>{automation.ready}/{automation.total}</b> {t("ui.ready.column.rooms_ready").toLowerCase()}</p>
      </div>
    {/if}
    {#if automation.history}<p class="muted-s">{automation.history}</p>{/if}
    {#if automation.teaches?.length}<p class="muted-s">{t("ui.automations.teaches", { what: automation.teaches.join(", ") })}</p>{/if}
    {#if automation.does?.text}
      <!-- What it does, from AI (B-346): only to read – AI never changes an automation (E-03). -->
      <p class="sentence">{t("ui.automations.does", { text: automation.does.text })} <span class="from-ai">{t("ui.knowledge.from_ai")}</span></p>
      <p class="muted-s">{takeOver(automation)}</p>
    {/if}
    {#if send && confirmOff === automation.id}
      <div class="callout">
        <p>{t("ui.automations.confirm_off", { name: automation.name })}</p>
        <div class="foot">
          <button class="primary" onclick={() => turn(automation, "off")}>{t("ui.yes")}</button>
          <button onclick={() => (confirmOff = null)}>{t("ui.no")}</button>
        </div>
      </div>
    {:else if send && automation.canTurnOff}
      <div class="foot"><button onclick={() => (confirmOff = automation.id)}>{t("ui.automations.turn_off")}</button></div>
    {:else if send && automation.canTurnOn}
      <div class="foot"><button onclick={() => turn(automation, "on")}>{t("ui.automations.turn_on")}</button></div>
    {/if}
  </article>
{/snippet}

<section class="ready" data-section="readiness" aria-labelledby="ready-title">
  <!-- The tab and the window already say the name (QA 3, schermen 14): the heading is for a
       screen reader only, then one sentence and Lees meer – the manual says how a step works. -->
  <h2 id="ready-title" class="sr">{t("ui.ready.title")}</h2>
  {#if !compact}
    <p class="muted">{t("ui.ready.intro")}{#if onmore}{" "}<button class="read-more" onclick={onmore}>{t("ui.vesta.more")}</button>{/if}</p>
  {/if}
  {#snippet ring(part: ReadyPart)}
    {#if partScore(part) !== null}
      {@const score = partScore(part) ?? 0}
      <!-- How often it went well, as a ring (E-437, design 52). -->
      <svg class="score" width="36" height="36" viewBox="0 0 44 44" role="img" aria-label={t("ui.flow.score", { pct: score })}>
        <circle cx="22" cy="22" r="18" class="score-track" />
        <circle cx="22" cy="22" r="18" class="score-bar" stroke-dasharray="{(113.1 * score) / 100} 113.1" transform="rotate(-90 22 22)" />
        <text x="22" y="26" text-anchor="middle" class="score-text">{score}</text>
      </svg>
    {/if}
  {/snippet}
  {#snippet counts(part: ReadyPart)}
    <span class="counts">
      {#each READY_STEPS.filter((s) => part.counts[s]) as step (step)}
        <StepPill {step} name={t(`steps.${step}`)} count={part.counts[step]} />
      {/each}
    </span>
  {/snippet}
  {#snippet about(part: ReadyPart)}
    {#if part.off}
      <!-- Off (E-400): grey, since when, and what it learned is kept. -->
      <p class="since">{t("ui.parts.off_since", { date: offDate(part) })}</p>
    {/if}
    <p class="text">{part.text}</p>
    {#if part.off && onlimit && send}
      <button class="link" onclick={() => send?.({ type: "house_setting", key: "parts", value: { [part.domain]: "on" } })}>{t("ui.parts.turn_on")}</button>
    {/if}
    {#if part.corrections}<p class="corrections">{part.corrections.text}</p>{/if}
    {#if part.limit && onlimit && !part.off}
      <button class="link" onclick={onlimit}>{t("ui.settings.max_step")}</button>
    {/if}
  {/snippet}
  {#if compact}
    <!-- Thuis and Ontzorgd: one sentence per part, as it was. -->
    <div class="overview">
      {#each parts as part (part.domain)}
        <article class="part" class:off={part.off} data-section="ready-{part.domain}">
          <header>
            <h3>{part.name}</h3>
            {@render counts(part)}
            {#if part.capName}<span class="cap">{t("ui.ready.may", { cap: part.capName })}</span>{/if}
          </header>
          {@render about(part)}
        </article>
      {/each}
    </div>
  {:else}
    <p class="hint">{t("ui.ready.overview_hint")}</p>
    <!-- The parts as tiles (E-454): a tap shows its card below them and filters the list. -->
    <div class="tiles" role="group" aria-label={t("ui.ready.parts")}>
      {#each parts as part (part.domain)}
        {@const rooms = part.skills?.length ?? 0}
        <button class="tile" class:off={part.off} aria-pressed={current?.domain === part.domain} data-section="ready-{part.domain}"
          onclick={() => pick(part.domain)}>
          <!-- The name the whole width, then the steps, then the rooms (E-454); no ring with a
               number on the tile (QA 3): the steps say how far it is. -->
          <span class="head"><span class="part-icon" aria-hidden="true"><DeviceIcon name={PART_ICONS[part.domain] ?? "device"} size={20} /></span><span class="name">{part.name}</span></span>
          {@render counts(part)}
          <span class="foot"><span class="sub">
            {#if part.off}{t("ui.parts.off_since", { date: offDate(part) })}{:else if part.capName}{t("ui.ready.may", { cap: part.capName })}{:else if rooms}{t(part.domain === "energy" ? "ui.ready.n_skills" : "ui.ready.n_rooms", { count: rooms }, rooms)}{:else}{t("ui.ready.none_yet")}{/if}
          </span></span>
        </button>
      {/each}
    </div>
    {#if current}
      {@const part = current}
      <article class="part chosen" aria-label={part.name}>
        <h3>{part.name}</h3>
        {@render about(part)}
        {#if part.own && send && !part.off}<OwnStep own={part.own} what={part.name} {t} {send} />{/if}
      </article>
    {/if}
  {/if}

  {#if showing}
    <section class="list" bind:this={list} aria-label={showing === "skills" ? t("ui.ready.tab.skills") : t("ui.ready.tab.automations")}>
      {#if kinds.length > 1}
        <div class="kinds" role="group" aria-label={t("ui.ready.title")}>
          <button aria-pressed={showing === "skills"} onclick={() => (kind = "skills")}>{t("ui.ready.tab.skills")} <span class="n">{rows.length}</span></button>
          <button aria-pressed={showing === "automations"} onclick={() => (kind = "automations")}>{t("ui.ready.tab.automations")} <span class="n">{autos.length}</span></button>
        </div>
      {/if}
      {#if showing === "skills"}
        <Filters items={rows} {defs} text={rowText} bind:chosen bind:search searchLabel={t("ui.ready.search")} {t}
          bind:group={view} groups={VIEWS.map((value) => ({ value, label: t(`ui.filter.title.${value}`) }))} />
        <!-- Two quick filters (E-501, B-426): what waits for something, and what is ready. -->
        <div class="quick" role="group" aria-label={t("ui.ready.quick")}>
          <button aria-pressed={(chosen.waits ?? []).length > 0} onclick={() => quick("waits")}>{t("ui.ready.filter_waits")} <span class="n">{waiting}</span></button>
          <button aria-pressed={(chosen.ready ?? []).length > 0} onclick={() => quick("ready")}>{t("ui.ready.quick_ready")} <span class="n">{readyCount}</span></button>
        </div>
        <ListTools items={shown} total={rows.length} page={paged} {lang} {t} />
        {#if !shown.length}<p class="muted">{t("ui.filter.none")}</p>{/if}
        <div class="split" class:wide bind:clientWidth={width}>
          <div class="rows">
            {#if paged}
              <ul class="block">{#each paged.items as skill (rowId(skill))}{@render skillRow(skill)}{/each}</ul>
              <Pager list="readiness" page={paged.page} pages={paged.pages} total={shown.length} {more} {lang} {t}
                ongo={(n) => (page = n)} onmore={() => (more = true)} />
            {/if}
            {#each groups as [key, items] (`${view}:${key}`)}
              <h3 class="group">
                {#if view === "step"}<StepPill step={key} name={t(`steps.${key}`)} />{:else}{view === "part" ? partName(key) : key || t("ui.filter.no_room")}{/if}
                <span class="n">{items.length}</span>
              </h3>
              <ul class="block">{#each items as skill (rowId(skill))}{@render skillRow(skill)}{/each}</ul>
            {/each}
          </div>
          {#if wide && pickedRow}<aside class="pane" aria-live="polite">{@render skillDetail(pickedRow)}</aside>{/if}
        </div>
        {#if !wide && sheetOpen && pickedRow}
          <Sheet title={pickedRow.skillName} closeLabel={t("ui.close")} onclose={() => (sheetOpen = false)}>
            <div class="in-sheet">{@render skillDetail(pickedRow)}</div>
          </Sheet>
        {/if}
      {:else}
        <Filters items={autos} defs={autoDefs} text={autoText} bind:chosen={chosenAutos} bind:search={searchAutos} searchLabel={t("ui.ready.search_automation")} {t}
          bind:group={autoView} groups={AUTO_VIEWS.map((value) => ({ value, label: t(`ui.filter.title.${value}`) }))} />
        <ListTools items={shownAutos} total={autos.length} page={autoPaged} {lang} {t} />
        {#if !shownAutos.length}<p class="muted">{t("ui.filter.none")}</p>{/if}
        <div class="split" class:wide bind:clientWidth={width}>
          <div class="rows">
            {#if autoPaged}
              <ul class="block">{#each autoPaged.items as automation (autoId(automation))}{@render autoRow(automation)}{/each}</ul>
              <Pager list="readiness_automations" page={autoPaged.page} pages={autoPaged.pages} total={shownAutos.length} more={autoMore} {lang} {t}
                ongo={(n) => (autoPage = n)} onmore={() => (autoMore = true)} />
            {/if}
            {#each autoGroups as [key, items] (key)}
              <h3 class="group">{partName(key)} <span class="n">{items.length}</span></h3>
              <ul class="block">{#each items as automation (autoId(automation))}{@render autoRow(automation)}{/each}</ul>
            {/each}
          </div>
          {#if wide && pickedAutoRow}<aside class="pane" aria-live="polite">{@render autoDetail(pickedAutoRow)}</aside>{/if}
        </div>
        {#if !wide && autoSheet && pickedAutoRow}
          <Sheet title={pickedAutoRow.name} closeLabel={t("ui.close")} onclose={() => (autoSheet = false)}>
            <div class="in-sheet">{@render autoDetail(pickedAutoRow)}</div>
          </Sheet>
        {/if}
      {/if}
    </section>
  {/if}
</section>

<style>
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  .read-more { border: none; background: none; padding: 0; color: var(--accent); font: inherit; font-weight: 600; cursor: pointer; min-height: 24px; }
  .ready { display: flex; flex-direction: column; gap: 10px; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  h3 { margin: 0; font-size: calc(15px * var(--text-scale)); }
  .muted { margin: 0; color: var(--muted); }
  .hint { margin: 0; font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .overview { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; overflow: hidden; }
  /* The parts as tiles (E-454): in one row on a computer, filling it, two by two on a phone. */
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; }
  .tile { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-height: 44px; padding: 10px 12px; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); color: var(--text); font: inherit; text-align: left; cursor: pointer; }
  .tile[aria-pressed="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); background: var(--raised); }
  .tile .name { font-weight: 600; font-size: calc(15px * var(--text-scale)); max-width: 100%; overflow-wrap: break-word; }
  .tile .head { display: flex; align-items: center; gap: 8px; max-width: 100%; }
  .part-icon { display: inline-flex; flex: none; color: var(--muted); }
  .tile[aria-pressed="true"] .part-icon { color: var(--accent); }
  .title .part-icon { align-self: center; }
  .room :global(svg) { display: inline-block; vertical-align: -2px; margin-right: 3px; }
  .tile .foot { display: flex; align-items: center; justify-content: space-between; gap: 6px; width: 100%; margin-top: auto; }
  .tile .sub { font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .tile .sub { min-width: 0; }
  .tile.off { color: var(--muted); }
  .tile.off .counts { filter: grayscale(1); opacity: 0.7; }
  .chosen { border: 1px solid var(--line); border-radius: 16px; background: var(--surface); }
  .part { padding: 10px 14px; display: flex; flex-direction: column; gap: 4px; border-bottom: 1px solid var(--line); }
  .part:last-child { border-bottom: none; }
  header { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }
  .corrections { margin: 0; font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .counts { display: flex; flex-wrap: wrap; gap: 6px; font-size: calc(13px * var(--text-scale)); }
  .cap { color: var(--muted); font-style: italic; font-size: calc(13px * var(--text-scale)); }
  .text { margin: 0; line-height: 1.45; }
  .link { align-self: flex-start; border: none; background: none; color: var(--accent); font-weight: 600; cursor: pointer; min-height: 44px; padding: 0; }
  .list { display: flex; flex-direction: column; gap: 8px; scroll-margin-top: 12px; }
  .kinds { display: inline-flex; align-self: flex-start; border: 1px solid var(--edge); border-radius: 22px; overflow: hidden; }
  .kinds button { min-height: 44px; padding: 0 16px; border: none; background: var(--surface); color: var(--text); font: inherit; cursor: pointer; }
  .kinds button + button { border-left: 1px solid var(--edge); }
  .kinds button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); font-weight: 600; }
  .kinds button[aria-pressed="true"] .n { color: inherit; }
  h3.group { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: calc(15px * var(--text-scale)); }
  .n, .of { color: var(--muted); font-weight: 400; font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); }
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .skill { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border-radius: 14px; background: var(--surface); border: 1px solid var(--line); }
  /* One quiet row per rule (E-501, B-421), in one block per group with a line between. */
  .block { gap: 0; border: 1px solid var(--line); border-radius: 16px; overflow: hidden; background: var(--surface); }
  .block .skill { padding: 0; border: none; border-radius: 0; border-top: 1px solid var(--line); }
  .block .skill:first-child { border-top: none; }
  .row { display: grid; grid-template-columns: 36px minmax(0, 1fr) auto; gap: 12px; align-items: center; width: 100%; min-height: 56px; padding: 10px 14px; border: none; border-radius: 0; background: none; color: var(--text); font: inherit; text-align: left; cursor: pointer; }
  .row:hover { background: var(--raised); }
  .row[aria-current="true"] { background: var(--raised); box-shadow: inset 3px 0 0 var(--accent); }
  .row .main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .row .title { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline; }
  .row .room { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .row .when { color: var(--muted); font-size: calc(14px * var(--text-scale)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .row .side { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
  .flag { font-size: calc(12px * var(--text-scale)); color: var(--learning); white-space: nowrap; }
  .flag.ready { color: var(--accent); }
  .flag.held { color: var(--important); }
  .flag.unseen { color: var(--muted); }
  /* Where an automation stands, as a label (B-427). */
  .state { font-size: calc(12px * var(--text-scale)); padding: 2px 10px; border-radius: 12px; font-weight: 600; white-space: nowrap; color: var(--muted); background: color-mix(in srgb, var(--muted) 18%, transparent); }
  .state.ready { color: var(--learning); background: color-mix(in srgb, var(--learning) 18%, transparent); }
  .state.taken { color: var(--accent); background: color-mix(in srgb, var(--accent) 18%, transparent); }
  .ring-text.small { font-size: 8px; }
  .flag.under { display: none; }
  /* On a phone the flag goes under the sentence: beside it, it left the sentence no room. */
  @media (max-width: 640px) {
    .row .side .flag { display: none; }
    .flag.under { display: block; white-space: normal; }
  }
  .ring { width: 36px; height: 36px; flex: none; }
  .ring.big { width: 64px; height: 64px; }
  .ring-track { fill: none; stroke: var(--line); stroke-width: 4; }
  .ring-fill { fill: none; stroke: var(--step-observe); stroke-width: 4; stroke-linecap: round; }
  .ring.propose .ring-fill { stroke: var(--step-propose); }
  .ring.autonomous .ring-fill { stroke: var(--step-autonomous); }
  .ring-past { fill: none; stroke: var(--surface); stroke-width: 4; stroke-dasharray: 1.5 2; opacity: 0.7; }
  .ring-goal { fill: var(--text); }
  .ring-text { fill: var(--text); font-family: var(--font-mono); font-size: 9.5px; }
  /* Beside the list on a computer, in a sheet on a phone (B-424). */
  .split { display: block; }
  .split.wide { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 16px; align-items: start; }
  .rows { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .pane { position: sticky; top: 64px; max-height: calc(100vh - 96px); overflow-y: auto; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); padding: 16px; }
  .detail { display: flex; flex-direction: column; gap: 12px; }
  .detail h3 { font-family: var(--font-display); font-size: calc(19px * var(--text-scale)); }
  .in-sheet .detail h3 { display: none; } /* the sheet's own title says it */
  .detail .sub { margin: 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .detail .sentence { margin: 0; line-height: 1.5; }
  .detail .meter { display: flex; gap: 14px; align-items: center; padding: 12px; border-radius: 14px; background: var(--raised); }
  .detail .meter p { margin: 0; font-size: calc(14px * var(--text-scale)); }
  .detail .meter b { font-family: var(--font-display); font-size: calc(18px * var(--text-scale)); }
  .muted-s { color: var(--muted); }
  .checks { gap: 6px; font-size: calc(14px * var(--text-scale)); }
  .checks li { display: flex; gap: 8px; align-items: baseline; }
  .checks .ok { color: var(--accent); font-weight: 700; width: 16px; text-align: center; flex: none; }
  .checks .todo { color: var(--muted); width: 16px; text-align: center; flex: none; }
  .checks .against .todo { color: var(--important); }
  .callout { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border-radius: 12px; border-left: 3px solid var(--edge); background: var(--raised); }
  .callout.news { border-left-color: var(--learning); }
  .callout p { margin: 0; line-height: 1.45; }
  .by-door { margin: 0; color: var(--muted); font-size: 0.92em; line-height: 1.45; }
  .facts { border-top: 1px solid var(--line); padding-top: 8px; display: flex; flex-direction: column; gap: 8px; }
  .facts summary { min-height: 36px; display: inline-flex; align-items: center; cursor: pointer; color: var(--accent); font-weight: 600; font-size: calc(14px * var(--text-scale)); }
  .when-detail { display: grid; grid-template-columns: minmax(80px, auto) minmax(0, 1fr); gap: 6px 12px; margin: 0; font-size: calc(14px * var(--text-scale)); }
  .when-detail dt { color: var(--muted); }
  .when-detail dd { margin: 0; overflow-wrap: anywhere; }
  .when-detail dd.wide { grid-column: 1 / -1; }
  .foot { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .foot button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); font: inherit; cursor: pointer; }
  .foot button.primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  .quick { display: flex; flex-wrap: wrap; gap: 8px; }
  .quick button { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--line); background: var(--surface); color: var(--muted); font: inherit; font-size: calc(14px * var(--text-scale)); cursor: pointer; }
  .quick button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
  .head { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
  .what { font-weight: 600; min-width: 0; }
  .when { margin: 0; line-height: 1.45; }
  .bar { position: relative; height: 10px; border-radius: 5px; background: var(--line); margin-right: 118px; }
  .fill { position: absolute; inset: 0 auto 0 0; border-radius: 5px; background: var(--step-observe); }
  .fill.autonomous { background: var(--step-autonomous); }
  .fill.propose { background: var(--step-propose); }
  .tick { position: absolute; top: -4px; bottom: -4px; width: 2px; margin-left: -1px; background: var(--text); border-radius: 1px; }
  .pct { position: absolute; left: calc(100% + 8px); top: 50%; transform: translateY(-50%); white-space: nowrap; font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); color: var(--muted); }
  .counters { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .counter { display: inline-flex; align-items: center; gap: 6px; }
  .met { color: var(--accent); font-weight: 700; }
  .mini { position: relative; width: 34px; height: 6px; border-radius: 3px; background: var(--line); overflow: hidden; }
  .more { display: flex; flex-direction: column; gap: 6px; }
  .steer { color: var(--muted); }
  .steer-ask { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 12px; }
  .steer-ask p { margin: 0; }
  .steer-ask .buttons { display: flex; flex-wrap: wrap; gap: 8px; }
  .steer-ask button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--surface); color: var(--text); cursor: pointer; }
  .steer-ask .primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  /* From the history (E-257): hatched over the start of the bar. */
  .swatch { background: repeating-linear-gradient(135deg, var(--text) 0 2px, transparent 2px 5px); opacity: 0.45; }
  .legend { margin: 0; display: flex; gap: 6px; align-items: center; font-size: calc(12px * var(--text-scale)); color: var(--muted); }
  .swatch { width: 18px; height: 8px; border-radius: 2px; display: inline-block; }
  .past-text { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .mono { margin: 0; font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); color: var(--muted); }
  .part.off { color: var(--muted); }
  .part.off .counts { filter: grayscale(1); opacity: 0.7; }
  .since { margin: 0; font-size: calc(13px * var(--text-scale)); font-weight: 600; color: var(--muted); }
  .score { flex: none; }
  .score-track { fill: none; stroke: var(--line); stroke-width: 5; }
  .score-bar { fill: none; stroke: var(--accent); stroke-width: 5; stroke-linecap: round; }
  .score-text { fill: var(--text); font-family: var(--font-mono); font-size: 12px; }
</style>

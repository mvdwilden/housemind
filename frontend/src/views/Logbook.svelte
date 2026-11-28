<script lang="ts">
  // Logboek (E-263, B-117): one timeline, the newest on top – what HouseMind did or left (with
  // Waarom? and Ongedaan maken), notifications, your conversations with Vesta, the morning
  // briefing and the house report. Above it the same bar as in Brein (E-323): search, Filter
  // (Soort, Ruimte, Onderdeel, Uitkomst), Periode – the last 2 days, today, yesterday, the last
  // 7 or 30 days or one day, as far back as the house keeps it – and Groeperen (day, room,
  // kind). How much a line shows follows the level: one sentence (2), saw · thought · did (3),
  // the trace (4). On top: today, and whether today is different (moved here from Huis).
  // Without groups one list in pages over the lines of the chosen period; above it how many,
  // the details a line shows and Exporteren, and an overview per kind (E-325). An
  // administrator also sees each question to an AI, with what was sent and what came back
  // (E-348); Soort › AI shows only those. Above it all "Wacht op jou" (E-452): the messages the
  // number at Logboek counts, with their answers; once handled, a message is in the timeline.
  import DeviceIcon from "../components/DeviceIcon.svelte";
  import { tone } from "../lib/deviceicons";
  import { untrack } from "svelte";
  import Cells from "../components/Cells.svelte";
  import Filters from "../components/Filters.svelte";
  import Flow from "../components/Flow.svelte";
  import { flowKind, storySteps } from "../lib/flow";
  import ListTools from "../components/ListTools.svelte";
  import NoticeCard from "../components/NoticeCard.svelte";
  import Pager from "../components/Pager.svelte";
  import { readable } from "../lib/aitext";
  import { listChoice } from "../lib/device.svelte";
  import { filtered, grouped, type Chosen } from "../lib/filters";
  import { chosenColumns, pageOf, ROWS } from "../lib/lists";
  import { byDay, dayName, groupKey, KINDS, lineKeys, logFilters, logList, logText, PERIODS, VIEWS, type LogDone, type LogItem, type Logbook } from "../lib/logbook";
  import type { Notice, Snapshot, Why } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    snap: Snapshot;
    log: Logbook | undefined;
    t: T;
    send: (message: Record<string, unknown>) => void;
    onnotice: (notice: Pick<Notice, "id" | "proposal">, action: string, extra?: Record<string, unknown>) => void;
    onchanged: () => void; // after Why?, Undo or an answer: the lines again
    period: string; // the period the app asks the lines of (E-323)
    day: string;
    onperiod: (period: string, day: string) => void;
    focus?: Why | null; // open at this notice or question: why Vesta has her colour (E-389)
    onfocused?: () => void;
  }
  let { snap, log, t, send, onnotice, onchanged, period, day, onperiod, focus = null, onfocused }: Props = $props();
  const level = $derived(snap.level);
  const lang = $derived(snap.language);

  let chosen = $state<Chosen>({});
  let search = $state("");
  let view = $state("day");
  let when = $state(untrack(() => period));
  let pickDay = $state(untrack(() => day));
  // Another period: the app asks the server for its lines; "Kies een dag" starts at today.
  $effect(() => {
    const w = when;
    const d = pickDay;
    untrack(() => {
      if (w === "day" && !d) {
        pickDay = log?.today ?? "";
        return;
      }
      if (w !== period || (w === "day" && d !== day)) onperiod(w, w === "day" ? d : "");
    });
  });

  // What waits for you (E-452): the same messages as the number at Logboek, on top; the
  // timeline has them only once they are handled.
  const waiting = $derived(snap.notifications ?? []);
  const waits = $derived(new Set(waiting.map((n) => n.id)));
  const items = $derived((log?.items ?? []).filter((i) => !(i.type === "notice" && i.open && waits.has(i.id))));
  const defs = $derived(logFilters(items, level, t));
  const shown = $derived(filtered(items, defs, chosen, search, logText));
  const groups = $derived(
    view === "day"
      ? byDay(shown).map((g) => [g.day, g.items] as [string, LogItem[]])
      : grouped(shown, (i) => groupKey(view, i), []),
  );

  // The lines as a long list (E-325). Pages are over the lines the server sent for the chosen
  // period; they start again at 1 with other search, filters, grouping or period – not when
  // the lines come again after Undo or an answer.
  const listDef = $derived(logList(level, t));
  const choice = $derived(listChoice("logbook"));
  const picked = $derived(chosenColumns(listDef, choice.columns));
  // The day stands above its lines – as the heading, or where a day starts – so a row never
  // repeats it; the grouping says its own column too.
  const columns = $derived(picked.filter((c) => c.key !== "day" && c.key !== view));
  const marks = $derived(view !== "day" && picked.some((c) => c.key === "day"));
  const compact = $derived(choice.compact === true);
  let page = $state(1);
  let more = $state(false);
  const paged = $derived(view === "none" ? pageOf(shown, page, choice.rows ?? ROWS[0], more) : null);
  $effect(() => {
    void [search, chosen, view, when, pickDay];
    page = 1;
    more = false;
  });
  /** The details of one line: what it has (a notification has no room), and not the time of a
   * notification – its card shows that itself. */
  function cells(item: LogItem) {
    return columns.filter((c) => c.value(item) !== "" && !(item.type === "notice" && c.key === "time"));
  }

  // A day that differs (E-89): only what people really have; HouseMind sees "home day" itself.
  const DAY_KINDS = ["home_work", "sick", "day_off", "school_holiday"];
  let choosing = $state(false);
  function note(kind: string | null): void {
    send({ type: "day_note", kind, offset: 0 });
    choosing = false;
  }

  function title(d: string): string {
    const name = dayName(d, log?.today ?? d, lang);
    return name === "today" ? t("ui.today") : name === "yesterday" ? t("ui.logbook.yesterday") : name;
  }
  function heading(key: string): string {
    if (view === "day") return title(key);
    return key || t("ui.filter.no_room");
  }
  // From the reason under Vesta's word (E-389, B-298): that notice in view, lit up for a
  // moment – on its page when the list has pages; Logboek from the top for "+2".
  $effect(() => {
    const want = focus;
    if (!want || !log) return;
    untrack(() => {
      const index = shown.findIndex(
        (i) => i.type === "notice" && (i.id === want.notification || (want.proposal != null && i.proposal === want.proposal)),
      );
      if (view === "none" && index >= 0) page = Math.floor(index / (choice.rows ?? ROWS[0])) + 1;
      setTimeout(() => {
        const at = want.notification ?? want.proposal;
        const card = at == null ? null : document.querySelector<HTMLElement>(
          want.notification != null ? `li[data-notice="${want.notification}"]` : `li[data-proposal="${want.proposal}"]`,
        );
        if (card) {
          const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
          card.scrollIntoView({ block: "center", behavior: still ? "auto" : "smooth" });
          card.classList.add("spot");
          setTimeout(() => card.classList.remove("spot"), 2500);
        } else {
          document.scrollingElement?.scrollTo({ top: 0 });
        }
        onfocused?.();
      }, 150);
    });
  });

  function undo(item: LogDone): void {
    send({ type: "undo", id: item.id });
    setTimeout(onchanged, 400);
  }
</script>

{#snippet line(item: LogItem)}
  {@const own = cells(item)}
  {#if item.type === "done"}
    <div class="line {item.outcome}">
      <!-- What it was about (E-511): its icon beside the cells. -->
      <div class="with-icon">{#if item.icon}<span class="dev-icon {tone(item.icon, item.pct != null)}" aria-hidden="true"><DeviceIcon name={item.icon} size={20} pct={item.pct} level={item.level} closed={item.closed} /></span>{/if}<Cells columns={own} {item} /></div>
      {#if level >= 3 && item.thought}
        <Flow kind={flowKind(item.outcome)} steps={storySteps(item, t)} />
      {:else}
        <p class="text">{item.text}</p>
      {/if}
      {#if item.answered}<p class="answered">{item.answered}</p>{/if}
      {#if level >= 4 && item.details?.length}
        <div class="details">{#each item.details as detail, j (j)}<p>{detail}</p>{/each}</div>
      {/if}
      {#if level >= 4 && item.checks?.length}
        <!-- The gate's checks in words (QA 3): what the decision trail showed, at the line itself. -->
        <details class="checks">
          <summary>{t("ui.logbook.checks")}</summary>
          <ol>
            {#each item.checks as check, j (j)}
              <li class={check.state}><span aria-hidden="true">{check.state === "ok" ? "✓" : check.state === "stop" ? "✕" : "–"}</span> {check.name}<span class="sr"> – {t(`ui.logbook.check_${check.state}`)}</span></li>
            {/each}
          </ol>
        </details>
      {/if}
      {#if item.answers?.length}
        <!-- The thumbs given on it, and what Vesta did with them (E-298). -->
        <ul class="answers">
          {#each item.answers as answer, j (j)}<li class:bad={!answer.good}>{answer.text}</li>{/each}
        </ul>
      {/if}
      {#if item.id !== null && (level < 3 || item.undo)}
        <div class="row">
          <!-- Waarom? on Geïnformeerd only: from Betrokken the line itself says saw → thought → did (QA 3). -->
          {#if level < 3}<button class="link" onclick={() => send({ type: "why", id: item.id })}>{t("ui.why")}</button>{/if}
          {#if item.undo}<button class="link" onclick={() => undo(item)}>{t("ui.undo")}</button>{/if}
        </div>
      {/if}
    </div>
  {:else if item.type === "notice"}
    <Cells columns={own} {item} />
    <!-- Answered elsewhere, by anyone (E-585): it is no longer waiting, so no buttons here. -->
    <NoticeCard notice={item} {t} handled={!item.open || !waits.has(item.id)} onaction={(action, extra) => onnotice(item, action, extra)} />
  {:else if item.type === "talk"}
    <div class="talk">
      {#if own.length}<div class="when"><Cells columns={own} {item} /></div>{/if}
      <p class="me"><span class="sr">{t("ui.logbook.you")}: </span>{item.question}</p>
      <p class="hm"><span class="sr">{snap.vesta?.name ?? "Vesta"}: </span>{item.answer}</p>
    </div>
  {:else if item.type === "part"}
    <!-- A part turned off or on again (E-400): who, and when. -->
    <div class="card">
      <Cells columns={own} {item} />
      <p>{item.text}</p>
    </div>
  {:else if item.type === "alarm"}
    <!-- The alarm (E-533, E-534): one line per moment, red when it went off. -->
    <div class="card alarm {item.state}">
      <div class="with-icon"><span class="dev-icon" aria-hidden="true"><DeviceIcon name="alarm" size={20} /></span><Cells columns={own} {item} /></div>
      <p>{item.text}</p>
    </div>
  {:else if item.type === "briefing"}
    <div class="card">
      <h3>{t("ui.briefing")}</h3>
      <Cells columns={own} {item} />
      <p>{item.text}</p>
    </div>
  {:else if item.type === "report"}
    <div class="card">
      <h3>{t("report.title")}</h3>
      <Cells columns={own} {item} />
      <ul class="plain">{#each item.lines as text (text)}<li>{text}</li>{/each}</ul>
      {#if item.ai}<p class="muted">{t("report.ai")}</p>{/if}
    </div>
  {:else if item.type === "ai"}
    <div class="line ai" class:failed={!item.ok}>
      <Cells columns={own} {item} />
      <p class="text">
        <span class="badge">{t("ui.logbook.ai")}</span> {t(`ui.logbook.ai_purpose.${item.purpose}`)} · <span class="mono">{item.model}</span>{item.cost > 0 ? ` · € ${item.cost.toLocaleString(lang, { minimumFractionDigits: 2, maximumFractionDigits: 5 })}` : ""}
      </p>
      <details class="io">
        <summary>{t("ui.logbook.ai_show")}</summary>
        {#if item.instruction}
          <p class="muted">{t("ui.settings.ai_instruction")}</p>
          <pre>{item.instruction}</pre>
        {/if}
        <p class="muted">{t("ui.settings.ai_sent")}</p>
        <pre>{readable(item.sent)}</pre>
        <p class="muted">{item.ok ? t("ui.settings.ai_received") : t("ui.logbook.ai_failed")}</p>
        <pre>{readable(item.received)}</pre>
      </details>
    </div>
  {/if}
{/snippet}

{#snippet lines(list: LogItem[])}
  {@const keys = lineKeys(list)}
  <ol class:compact>
    {#each list as item, i (keys[i])}
      <!-- Not per day: the day above its first line (and on top of each page). -->
      {#if marks && (i === 0 || list[i - 1].day !== item.day)}<li class="subday">{title(item.day)}</li>{/if}
      <li class={item.type} data-notice={item.type === "notice" ? item.id : undefined} data-proposal={item.type === "notice" ? (item.proposal ?? undefined) : undefined}>{@render line(item)}</li>
    {/each}
  </ol>
{/snippet}

{#if waiting.length}
  <section class="card waiting" data-tour="waiting" aria-labelledby="waiting-title">
    <h2 id="waiting-title">{t("ui.logbook.waiting")} <span class="n">{waiting.length}</span></h2>
    <ol>
      {#each waiting as notice (notice.id)}
        <li class="notice" data-notice={notice.id} data-proposal={notice.proposal ?? undefined}>
          <NoticeCard {notice} {t} onaction={(action, extra) => onnotice(notice, action, extra)} />
        </li>
      {/each}
    </ol>
  </section>
{/if}

{#if snap.day}
  <!-- Vandaag anders? as a small control, no card with a sentence (QA 3, schermen 20): the
       Logboek itself is today. -->
  <div class="day-actions" data-tour="today">
    {#if snap.dayNote}
      <span class="note">{snap.dayNote.text}</span>
      <button class="link" onclick={() => note(null)}>{t("ui.days.normal")}</button>
    {:else if choosing}
      {#each DAY_KINDS as kind (kind)}
        <button class="chip" onclick={() => note(kind)}>{t(`days.kind.${kind}`)}</button>
      {/each}
    {:else}
      <button class="link" onclick={() => (choosing = true)}>{t("ui.days.change")}</button>
    {/if}
  </div>
{/if}

<!-- Geïnformeerd: search and Periode only (QA 3, schermen 24); Filter and Groeperen from Betrokken. -->
<Filters items={items} defs={level >= 3 ? defs : []} text={logText} bind:chosen bind:search searchLabel={t("ui.logbook.search")} {t}
  bind:group={view} groups={level >= 3 ? VIEWS.map((value) => ({ value, label: t(value === "none" ? "ui.filter.title.none" : `ui.logbook.group.${value}`) })) : undefined}
  bind:period={when} periods={PERIODS.map((value) => ({ value, label: t(`ui.logbook.periods.${value}`) }))}
  periodLabel={t("ui.logbook.period")} bind:day={pickDay} dayMin={log?.oldest} dayMax={log?.today} />

{#if !log}
  <p class="muted" aria-busy="true">…</p>
{:else if items.length === 0}
  {#if !waiting.length}<p class="empty">{t(period === "recent" ? "ui.logbook.none_all" : "ui.logbook.none_period")}</p>{/if}
{:else}
  <!-- Kolommen and Exporteren on Expert only (QA 3, schermen 23); below it only the count. -->
  <ListTools def={level >= 4 ? listDef : undefined} items={shown} total={items.length} page={paged} {lang} {t} />
  {#if shown.length === 0}<p class="empty">{t("ui.filter.none")}</p>{/if}
{/if}

{#if paged}
  {@render lines(paged.items)}
  <Pager list="logbook" page={paged.page} pages={paged.pages} total={shown.length} {more} {lang} {t}
    ongo={(n) => (page = n)} onmore={() => (more = true)} />
{/if}
{#each paged ? [] : groups as [key, list] (`${view}:${key}`)}
  <section class="day" aria-label={heading(key)}>
    <h2>{heading(key)} {#if view !== "day"}<span class="n">{list.length}</span>{/if}</h2>
    {@render lines(list)}
  </section>
{/each}

<style>
  .answered { margin: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .checks { font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .checks summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; }
  .checks ol { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 12px; }
  .checks li.ok span:first-child { color: var(--accent); }
  .checks li.stop { color: var(--important); font-weight: 600; }
  .checks .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  .with-icon { display: flex; gap: 10px; align-items: flex-start; }
  .dev-icon { display: inline-flex; flex: none; color: var(--muted); margin-top: 2px; }
  .dev-icon.lamp { color: var(--learning); }
  .answers { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .answers li { font-size: calc(14px * var(--text-scale)); padding: 6px 10px; border-radius: 10px; background: var(--raised); }
  .answers li.bad { border-left: 3px solid var(--warn, #c77a2b); }
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 18px; padding: 14px 16px; }
  .alarm .dev-icon { color: var(--accent); }
  .alarm.triggered { border-color: var(--emergency); }
  .alarm.triggered .dev-icon { color: var(--emergency); }
  h2 { margin: 0 0 8px; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); display: flex; gap: 8px; align-items: baseline; }
  h3 { margin: 0 0 4px; font-size: calc(15px * var(--text-scale)); display: flex; gap: 8px; align-items: baseline; }
  p { margin: 0; }
  .n { color: var(--muted); font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); font-weight: 400; }
  .note { font-weight: 600; }
  .waiting { border-color: var(--accent); }
  .day-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
  .chip { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--edge); background: var(--surface); color: var(--text); cursor: pointer; }
  .muted { color: var(--muted); }
  .empty { color: var(--muted); text-align: center; padding: 32px 0; }
  .day { display: flex; flex-direction: column; }
  ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
  .subday { margin-bottom: -6px; color: var(--muted); font-size: calc(13px * var(--text-scale)); font-weight: 600; }
  li.notice { display: flex; flex-direction: column; gap: 4px; }
  li .card { display: flex; flex-direction: column; gap: 4px; }
  .line { padding: 2px 0 2px 12px; border-left: 3px solid var(--line); display: flex; flex-direction: column; gap: 2px; }
  .line.executed { border-left-color: var(--accent); }
  .line.proposed { border-left-color: var(--learning); }
  .line.denied, .line.failed { border-left-color: var(--important); }
  /* A question to an AI (E-348): its own edge, and what went out and came back on a tap. */
  .line.ai { border-left-color: var(--edge); }
  .line.ai.failed { border-left-color: var(--important); }
  .line.ai .badge { display: inline-block; padding: 0 7px; border-radius: 9px; border: 1px solid currentColor; color: var(--ai); font-size: calc(12px * var(--text-scale)); font-weight: 700; }
  .io summary { min-height: 40px; display: inline-flex; align-items: center; cursor: pointer; color: var(--accent); font-weight: 600; }
  .io pre { margin: 2px 0 8px; padding: 8px 10px; border-radius: 10px; background: var(--raised); font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; }
  .mono { font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); }
  /* The same lines and details, less room (E-325, B-198). */
  ol.compact { gap: 6px; }
  .compact .subday { margin-bottom: -2px; }
  .compact .line { padding: 0 0 0 10px; }
  .compact .card { padding: 8px 12px; }
  .compact .me, .compact .hm { padding: 6px 10px; }
  .text { font-size: calc(15px * var(--text-scale)); line-height: 1.45; }
  .details { margin-top: 4px; display: flex; flex-direction: column; gap: 2px; color: var(--muted); font-size: calc(13px * var(--text-scale)); overflow-wrap: anywhere; }
  .row { display: flex; gap: 18px; }
  .link { align-self: flex-start; border: none; background: none; color: var(--accent); font-weight: 600; cursor: pointer; min-height: 40px; padding: 0; }
  .talk { display: flex; flex-direction: column; gap: 4px; }
  .talk .when { align-self: flex-end; } /* above your own question */
  .me, .hm { padding: 10px 14px; border-radius: 16px; max-width: 85%; white-space: pre-line; }
  .me { align-self: flex-end; background: var(--accent); color: var(--on-accent); border-bottom-right-radius: 4px; }
  .hm { align-self: flex-start; background: var(--surface); border: 1px solid var(--line); border-bottom-left-radius: 4px; }
  .plain { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  /* The notice Vesta's reason pointed to (E-389), lit up for a moment. */
  :global(li.spot) { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 12px; }
</style>

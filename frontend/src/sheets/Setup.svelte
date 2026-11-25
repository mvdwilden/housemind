<script lang="ts">
  // Setting HouseMind up (E-277 – E-282, B-125). Until it is set up there is only this screen,
  // on every level: an administrator chooses "Inrichten", others wait. Five steps, each made of
  // the parts the app already has, so every setting stays in one place: the introduction, the
  // link with Home Assistant, Vesta (name, voice and her own Assist pipeline, in the language
  // HA shows this administrator), the AI, and done – which waits for the history.
  import Orb from "../lib/Orb.svelte";
  import HistoryProgress from "../components/HistoryProgress.svelte";
  import Onboarding from "./Onboarding.svelte";
  import IntegrationSettings from "./IntegrationSettings.svelte";
  import VestaVoice from "./VestaVoice.svelte";
  import AISettings from "./AISettings.svelte";
  import type { HouseSettings, IntegrationView, Snapshot } from "../lib/types";
  import type { T } from "../lib/ui";
  import { untrack } from "svelte";
  import { pipelineStillOpen, wizardPage, wizardTotal } from "../lib/wizard";

  interface Props {
    snap: Snapshot;
    settings: HouseSettings | undefined;
    integration: IntegrationView | undefined;
    preview: string | undefined;
    t: T;
    send: (message: Record<string, unknown>) => void;
    get: (what: string) => void;
    onhelp: () => void;
    onread?: (key: string) => void;
    onspeak?: (step: string) => void;
    heard?: { step: string; value: number | boolean | null; heard: string | null } | null;
    listening?: boolean;
  }
  let { snap, settings, integration, preview, t, send, get, onhelp, onread, onspeak, heard = null, listening = false }: Props = $props();

  const STEPS = ["intro", "link", "vesta", "ai", "finish"];
  const at = $derived(snap.setup?.state ?? "pending");
  const index = $derived(STEPS.indexOf(at));
  const name = $derived(snap.vesta?.name ?? "Vesta");
  const admin = $derived(snap.user.isAdmin);
  const reading = $derived(snap.history?.state === "reading");
  const language = $derived(snap.languageNames[snap.setup?.language ?? ""] ?? snap.setup?.language ?? "");
  const go = (step: string) => send({ type: "setup_step", step });
  const setting = (key: string, value: unknown) => { send({ type: "house_setting", key, value }); setTimeout(() => get("settings"), 300); };
  // The link counts only when HA really reaches HouseMind (20.23); without it "Verder" asks.
  const linked = $derived(integration?.contact === "ok");
  let confirmUnlinked = $state(false);
  // Klaar waits for the reading until it is done, or stands still for three minutes (E-396):
  // the server says how long still, and counts again whenever the reading moves on.
  let waitLeft = $state(0);
  $effect(() => {
    const until = Date.now() + (snap.setup?.readWait ?? 0) * 1000;
    waitLeft = Math.ceil(Math.max(0, until - Date.now()) / 1000);
    const timer = setInterval(() => (waitLeft = Math.ceil(Math.max(0, until - Date.now()) / 1000)), 1000);
    return () => clearInterval(timer);
  });
  const speech = $derived(integration?.speech);

  // One row of pages, one count (E-395): the introduction's questions, the house questions,
  // then the link, Vesta, the AI and Klaar. The introduction stays while the house questions
  // show, so Terug comes back to the answers given.
  let introIndex = $state(0);
  let introCount = $state(0);
  let introOpen = $state(untrack(() => Boolean(snap.onboarding.person)));
  const total = $derived(wizardTotal(introCount));
  const page = $derived(wizardPage(at, introOpen, introIndex, introCount, index));
  function backToIntro(): void {
    introIndex = Math.max(0, introCount - 1);
    introOpen = true;
  }

  // What each step needs from the server.
  $effect(() => {
    if (!admin || index < 0) return;
    get("settings");
    if (at === "link" || at === "vesta" || at === "finish") get("integration");
  });
  // While the link waits for Home Assistant (a restart, a confirmation), look again now and then.
  $effect(() => {
    if (at !== "link" && at !== "vesta" && at !== "finish") return;
    const timer = setInterval(() => get("integration"), 5000);
    return () => clearInterval(timer);
  });
</script>

<main class="setup">
  <div class="top">
    <span class="brand">{t("app.name")}</span>
    <button class="round" aria-label={t("ui.help")} title={t("ui.help")} onclick={onhelp}>?</button>
  </div>

  {#if at === "pending" || !admin}
    <div class="start">
      <Orb state="inactive" level={2} size={120} label={t("orb.inactive")} />
      <h1>{t("ui.setup.title")}</h1>
      {#if !admin}
        <p>{t("ui.setup.others")}</p>
      {:else}
        <p>{t("ui.setup.text", { name })}</p>
        <p class="muted">{t("ui.setup.emergency")}</p>
        <button class="primary" onclick={() => send({ type: "setup_start" })}>{t("ui.setup.start")}</button>
      {/if}
    </div>
  {:else}
    <div class="steps">
      <p class="count">{t("ui.setup.step", { n: page, total })}</p>
      <h1>{t(`ui.setup.steps.${at}`)}</h1>

      {#if at === "intro"}
        <div hidden={!introOpen}>
          <Onboarding {snap} {settings} {t} {send} {get} {onread} {onspeak} {heard} {listening}
            bind:index={introIndex} bind:count={introCount} active={introOpen} ondone={() => (introOpen = false)} />
        </div>
        <!-- After it, this step shows the questions about the house first (20.23). -->
        {#if !introOpen}
          <p>{t("ui.setup.intro_done")}</p>
          {#if settings}
            <!-- A few questions about the house (20.23): who starts where, phones, the calendar. -->
            <section class="house" data-section="house_questions">
              <h2>{t("ui.setup.house.title")}</h2>
              <label class="field">{t("ui.settings.new_level")}
                <select value={settings.newPersonLevel ?? 1} onchange={(e) => setting("new_person_level", Number(e.currentTarget.value))}>
                  {#each [1, 2, 3, 4] as n (n)}<option value={n}>{t(`levels.${["carefree", "informed", "involved", "expert"][n - 1]}`)}</option>{/each}
                </select>
              </label>
              <p class="muted">{t("ui.settings.new_level_note")}</p>
              <p>{t("ui.setup.house.phoneless")}</p>
              <div class="choice" role="group" aria-label={t("ui.setup.house.phoneless")}>
                <button aria-pressed={settings.phonelessResidents === true} onclick={() => setting("phoneless_residents", true)}>{t("ui.yes")}</button>
                <button aria-pressed={!settings.phonelessResidents} onclick={() => setting("phoneless_residents", false)}>{t("ui.no")}</button>
              </div>
              {#if settings.phonelessResidents}
                <!-- Their first names, only so that they never reach an AI (QA 2, B-174). -->
                <label class="field">{t("ui.settings.private_names")}
                  <input type="text" value={(settings.privateNames ?? []).join(", ")} autocomplete="off"
                    onchange={(e) => { setting("private_names", e.currentTarget.value.split(",").map((n) => n.trim()).filter(Boolean)); }} />
                </label>
                <p class="muted">{t("ui.settings.private_names_note")}{#if settings.namesInRooms?.length} {t("ui.settings.private_names_found", { names: settings.namesInRooms.join(", ") })}{/if}</p>
              {/if}
              {#if settings.hasCalendar}
                <p>{t("ui.setup.house.calendar", { name })}</p>
                <div class="choice" role="group" aria-label={t("ui.setup.house.calendar", { name })}>
                  <button aria-pressed={settings.calendarAsked && settings.calendar === true} onclick={() => setting("calendar", true)}>{t("ui.yes")}</button>
                  <button aria-pressed={settings.calendarAsked && settings.calendar !== true} onclick={() => setting("calendar", false)}>{t("ui.no")}</button>
                </div>
                <p class="muted">{t("ui.settings.calendar.note")}</p>
              {/if}
            </section>
          {/if}
        {/if}
      {:else if at === "link"}
        <p>{t("ui.setup.link_text", { name })}</p>
        <IntegrationSettings info={integration} part="link" {name} {t} {send} />
        {#if !linked}<p class="muted">{t("ui.setup.link_later")}</p>{/if}
        {#if confirmUnlinked && !linked}
          <div class="warn" role="alert">
            <p>{t("ui.setup.unlinked", { name })}</p>
            <div class="row">
              <button class="primary" onclick={() => { confirmUnlinked = false; go("vesta"); }}>{t("ui.setup.unlinked_yes")}</button>
              <button onclick={() => (confirmUnlinked = false)}>{t("ui.no")}</button>
            </div>
          </div>
        {/if}
      {:else if at === "vesta"}
        <p>{t("ui.setup.vesta_text", { name })}</p>
        {#if settings}<VestaVoice {settings} {t} {send} {get} />{/if}
        <p class="muted">{t("ui.setup.language", { language, name })}</p>
        {#if speech && speech.cloud.length}
          <p class="note">{t("ui.setup.speech_cloud", { name, engines: speech.cloud.join(", ") })}</p>
        {:else if speech?.local}
          <p class="muted">{t("ui.setup.speech_local", { name })}</p>
        {/if}
        <IntegrationSettings info={integration} part="pipeline" {name} {t} {send} />
      {:else if at === "ai"}
        <p>{t("ui.setup.ai_text", { name })}</p>
        {#if settings}<AISettings {settings} {preview} level={snap.level} {t} {send} {get} />{/if}
      {:else if at === "finish"}
        <p>{t("ui.setup.finish_text", { name })}</p>
        {#if snap.setup?.labels}<p class="muted">{t("ui.setup.labels_used", {}, snap.setup.labels)}</p>{/if}
        <HistoryProgress view={snap.history} {t} />
        {#if reading && waitLeft > 0}
          <p class="muted" role="status">{t("ui.setup.wait")}</p>
        {:else if reading}
          <p class="muted" role="status">{t("ui.setup.read_stalled")}</p>
        {/if}
        {#if settings}
          <section data-section="knowledge">
            <p>{t("ui.setup.knowledge")}</p>
            <label class="check"><input type="checkbox" checked={settings.knowledgeUpdates}
              onchange={(e) => setting("knowledge_updates", e.currentTarget.checked)} />
              {t("ui.settings.upkeep.knowledge")}</label>
          </section>
        {/if}
        {#if integration && !linked}<p class="muted">{t("ui.setup.finish_unlinked")}</p>{/if}
        <!-- Her own Assist pipeline, once more, if it was passed at Naam en stem (E-280). -->
        {#if pipelineStillOpen(integration)}<IntegrationSettings info={integration} part="pipeline" {name} {t} {send} />{/if}
      {/if}

      {#if at !== "intro" || !introOpen}
        <!-- While it reads, on every page how far it is (E-395); Klaar shows it above. -->
        {#if at !== "finish"}<HistoryProgress view={snap.history} {t} />{/if}
        <!-- The same row on every page (E-395): Terug, Volgende, and Klaar on the last. -->
        <div class="nav">
          {#if at === "intro"}<button onclick={backToIntro}>{t("ui.setup.back")}</button>
          {:else}<button onclick={() => go(STEPS[index - 1])}>{t("ui.setup.back")}</button>{/if}
          {#if at === "finish"}
            <button class="primary" disabled={reading && waitLeft > 0} onclick={() => send({ type: "setup_done" })}>{t("ui.setup.done")}</button>
          {:else if at === "link" && !linked}
            <button class="primary" onclick={() => (confirmUnlinked = true)}>{t("ui.next")}</button>
          {:else}
            <button class="primary" onclick={() => go(STEPS[index + 1])}>{t("ui.next")}</button>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</main>

<style>
  .setup { max-width: 640px; margin: 0 auto; padding: 16px 16px 48px; display: flex; flex-direction: column; gap: 16px; min-height: 100vh; box-sizing: border-box; }
  .top { display: flex; justify-content: space-between; align-items: center; }
  .brand { font-family: var(--font-display); font-weight: 600; }
  .round { width: 44px; height: 44px; border-radius: 22px; border: 1px solid var(--line); background: var(--raised); color: inherit; font-size: calc(18px * var(--text-scale)); cursor: pointer; }
  .start { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 14px; margin-top: 8vh; }
  .steps { display: flex; flex-direction: column; gap: 12px; }
  h1 { margin: 0; font-family: var(--font-display); font-size: calc(24px * var(--text-scale)); }
  p { margin: 0; line-height: 1.5; }
  .muted { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .count { color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .nav { display: flex; justify-content: space-between; gap: 10px; margin-top: 8px; }
  .nav .primary { margin-left: auto; }
  button { min-height: 48px; padding: 0 20px; border-radius: 24px; border: 1px solid var(--line); background: var(--raised); color: inherit; cursor: pointer; font-weight: 500; }
  .primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 600; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  h2 { margin: 8px 0 0; font-size: calc(18px * var(--text-scale)); }
  .house, [data-section="knowledge"] { display: flex; flex-direction: column; gap: 8px; }
  .field { display: flex; flex-direction: column; gap: 6px; }
  select { min-height: 44px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: inherit; padding: 0 12px; }
  .choice, .row { display: flex; gap: 10px; flex-wrap: wrap; }
  .choice button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
  .check { display: flex; gap: 10px; align-items: center; min-height: 44px; }
  .warn { padding: 12px 14px; border-radius: 16px; border: 1px solid var(--line); background: var(--raised); display: flex; flex-direction: column; gap: 10px; }
  .note { font-size: calc(15px * var(--text-scale)); }
</style>

<script lang="ts">
  // The introduction (E-40, E-117): at most four steps, about five minutes.
  import { untrack } from "svelte";
  import Orb from "../lib/Orb.svelte";
  import HistoryProgress from "../components/HistoryProgress.svelte";
  import PartsList from "../components/PartsList.svelte";
  import { answerNow } from "../lib/introanswer";
  import { introSteps, introAnswered } from "../lib/wizard";
  import type { HouseSettings, Snapshot } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    snap: Snapshot;
    settings: HouseSettings | undefined;
    t: T;
    send: (message: Record<string, unknown>) => void;
    get: (what: string) => void;
    // Vesta tells each step (E-253): her voice, and an answer said aloud.
    onread?: (key: string) => void;
    onspeak?: (step: string) => void;
    heard?: { step: string; value: number | boolean | null; heard: string | null } | null;
    listening?: boolean;
    // Setting up (E-278): after the introduction the next step comes; there is no "Later".
    ondone?: () => void;
    // Setting up (E-395): the wizard counts the pages and keeps this one while it shows the
    // house questions, so Terug comes back to the answers given.
    index?: number;
    count?: number;
    active?: boolean;
  }
  let { snap, settings, t, send, get, onread, onspeak, heard = null, listening = false, ondone, index = $bindable(0), count = $bindable(0), active = true }: Props = $props();
  // Klaar waits while the history is read (E-396) – unless it stands still – not when setting
  // up, where more pages come.
  const reading = $derived(!ondone && snap.history?.state === "reading" && !snap.history?.stalled);
  const name = $derived(snap.vesta?.name ?? "Vesta");
  // After the yes to learning she reads the history (E-257): her sentence and a bar.
  const history = $derived(snap.history);

  // Decided once: answering the house questions must not make the steps jump.
  // Setting up always asks all four (E-278, E-395) – also after a reload once they were
  // answered, so the count stays the same and Terug shows the answers given.
  const answeredBefore = untrack(() => Boolean(ondone) && !snap.onboarding.person);
  const house = untrack(() => Boolean(ondone) || snap.onboarding.house);
  const steps = $derived(introSteps(snap.level, house, Boolean(ondone)));
  $effect(() => {
    count = steps.length;
  });
  // What was chosen on each page (E-395): Ja and Nee choose, Volgende goes on.
  let chosen = $state<Record<string, boolean>>({});
  let pets = $state<string[]>([]);
  let hasPets = $state<boolean | null>(null);
  // The introduction once more (E-272): the answers of now are already chosen.
  const again = $derived(Boolean(snap.onboarding.again) || answeredBefore);
  const pressed = (step: string, now: boolean | null | undefined, button: boolean) =>
    step in chosen ? chosen[step] === button : again && now != null ? now === button : false;
  function choose(step: string, value: boolean, message: Record<string, unknown> | null): void {
    chosen = { ...chosen, [step]: value };
    if (message) send(message);
  }
  let filled = false;
  $effect(() => {
    if (!again || !settings || filled) return;
    filled = true;
    pets = [...settings.petAreas];
    hasPets = pets.length > 0; // the answer of before: Ja with its rooms, or Nee
  });
  const step = $derived(steps[Math.min(index, steps.length - 1)]);
  const answered = $derived(
    introAnswered(step, chosen, {
      again,
      emergencyPush: snap.prefs.emergencyPush,
      consentLearning: settings?.consentLearning,
      hasPets,
    }),
  );

  $effect(() => {
    if (house && !settings) get("settings");
  });

  function next(): void {
    // Nee clears the rooms chosen before; it kept them, so pets came back after a reload.
    if (step === "pets") send({ type: "house_setting", key: "pet_areas", value: hasPets ? pets : [] });
    // The yes to learning goes with Volgende (E-395): a tap on Ja and then Nee starts no reading.
    if (step === "learning" && "learning" in chosen) {
      send({ type: "house_setting", key: "consent_learning", value: chosen.learning });
    }
    if (index >= steps.length - 1) {
      send({ type: "set_pref", key: "onboarded", value: true });
      ondone?.();
    } else {
      index += 1;
    }
  }

  // Her words for each step, aloud when her voice is on for this device.
  $effect(() => {
    if (active) onread?.(`ui.intro.vesta.${step}`);
  });

  // An answer said aloud: the same as the button for it – never one from an earlier
  // introduction (found in the real house: it skipped the first step).
  let used: unknown = null;
  const atStart = untrack(() => heard);
  $effect(() => {
    const answer = answerNow(heard, used, atStart, step);
    if (!answer || !active) return;
    used = answer;
    const value = answer.value;
    if (step === "level" && typeof value === "number") {
      send({ type: "set_level", level: value });
      next();
    } else if (step === "emergency" && typeof value === "boolean") {
      choose(step, value, { type: "set_pref", key: "emergency_push", value });
      next();
    } else if (step === "learning" && typeof value === "boolean") {
      choose(step, value, null);
      next();
    } else if (step === "pets" && typeof value === "boolean") {
      hasPets = value;
      if (!value) next();
    }
  });
</script>

<div class="intro">
  <div class="vesta">
    <Orb state="calm" level={2} size={56} label={name} />
    <p aria-live="polite">{t(`ui.intro.vesta.${step}`, { name })}</p>
  </div>
  {#if onspeak}
    <button class="speak" disabled={listening} onclick={() => onspeak(step)}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
      {listening ? t("ui.vesta.listening") : t("ui.vesta.speak")}
    </button>
    {#if heard && heard.step === step && heard.value === null}
      <p class="muted">{t("ui.intro.not_heard")}</p>
    {/if}
  {/if}
  <HistoryProgress view={history} {t} />
  {#if !ondone}<p class="count">{t("ui.intro.step", { n: index + 1, total: steps.length })}</p>{/if}
  {#if step === "level"}
    <h3>{t("ui.intro.welcome")}</h3>
    <p>{t("ui.intro.welcome_text")}</p>
    <p class="disclaimer">{t("ui.intro.disclaimer")}</p>
    <div class="levels">
      {#each snap.levels as option (option.level)}
        <button class="level" aria-pressed={option.level === snap.level} onclick={() => send({ type: "set_level", level: option.level })}>
          <strong>{t(`levels.${option.key}`)}</strong>
          <span>{t(`levels.${option.key}_hint`)}</span>
        </button>
      {/each}
    </div>
  {:else if step === "emergency"}
    <h3>{t("ui.intro.emergency")}</h3>
    <p>{t("ui.intro.emergency_text")}</p>
    <div class="row">
      <button aria-pressed={pressed(step, snap.prefs.emergencyPush, true)} onclick={() => choose(step, true, { type: "set_pref", key: "emergency_push", value: true })}>{t("ui.yes")}</button>
      <button aria-pressed={pressed(step, snap.prefs.emergencyPush, false)} onclick={() => choose(step, false, { type: "set_pref", key: "emergency_push", value: false })}>{t("ui.no")}</button>
    </div>
  {:else if step === "learning"}
    <h3>{t("ui.intro.learning")}</h3>
    <p>{t("ui.intro.learning_text")}</p>
    <div class="row">
      <button aria-pressed={pressed(step, settings?.consentLearning, true)} onclick={() => choose(step, true, null)}>{t("ui.yes")}</button>
      <button aria-pressed={pressed(step, settings?.consentLearning, false)} onclick={() => choose(step, false, null)}>{t("ui.no")}</button>
    </div>
  {:else if step === "pets"}
    <h3>{t("ui.intro.pets")}</h3>
    <p>{t("ui.intro.pets_text")}</p>
    <div class="row">
      <button aria-pressed={hasPets === true} onclick={() => (hasPets = true)}>{t("ui.yes")}</button>
      <button aria-pressed={hasPets === false} onclick={() => (hasPets = false)}>{t("ui.no")}</button>
    </div>
    {#if hasPets && settings}
      <div class="areas">
        {#each settings.areas as area (area.id)}
          <label class="check"><input type="checkbox" checked={pets.includes(area.id)}
            onchange={(e) => (pets = e.currentTarget.checked ? [...pets, area.id] : pets.filter((p) => p !== area.id))} /> {area.name}</label>
        {/each}
      </div>
    {/if}
    {#if hasPets !== null}
      <p class="muted">{t("ui.intro.automations")}</p>
      {#if reading}<p class="muted" role="status">{t("ui.setup.wait")}</p>{/if}
    {/if}
  {:else if step === "parts"}
    <!-- Setting up on Expert (E-401): the same list as in Huisinstellingen. -->
    <h3>{t("ui.parts.title")}</h3>
    <p>{t("ui.parts.wizard")}</p>
    {#if settings?.parts}
      <PartsList parts={settings.parts} expert lang={snap.language} {t}
        onchange={(part, state) => { send({ type: "house_setting", key: "parts", value: { [part]: state } }); setTimeout(() => get("settings"), 300); }} />
    {/if}
  {/if}
  <!-- The same row on every page (E-395): Terug, and Volgende once there is an answer. -->
  <div class="nav">
    {#if index > 0}<button onclick={() => (index -= 1)}>{t("ui.setup.back")}</button>{:else}<span></span>{/if}
    {#if index >= steps.length - 1 && !ondone}
      <button class="primary" disabled={!answered || reading} onclick={next}>{t("ui.intro.done")}</button>
    {:else}
      <button class="primary" disabled={!answered} onclick={next}>{t("ui.next")}</button>
    {/if}
  </div>
  {#if !ondone && (step !== "pets" || hasPets === null)}
    <button class="skip" onclick={() => send({ type: "set_pref", key: "onboarded", value: true })}>{t("ui.intro.later")}</button>
  {/if}
</div>

<style>
  .vesta { display: flex; gap: 12px; align-items: center; }
  .vesta p { margin: 0; font-size: calc(16px * var(--text-scale)); line-height: 1.45; }
  .speak { align-self: flex-start; display: inline-flex; gap: 8px; align-items: center; min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--accent); background: transparent; color: var(--accent); font-size: calc(15px * var(--text-scale)); cursor: pointer; }
  .speak:disabled { opacity: 0.7; cursor: default; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .disclaimer { color: var(--muted); font-size: calc(14px * var(--text-scale)); margin: 0; }
  .intro { display: flex; flex-direction: column; gap: 12px; }
  .count { margin: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  h3 { margin: 0; font-family: var(--font-display); font-size: calc(22px * var(--text-scale)); }
  p { margin: 0; line-height: 1.5; }
  .muted { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .levels { display: flex; flex-direction: column; gap: 6px; }
  .level { text-align: left; padding: 10px 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; display: flex; flex-direction: column; gap: 2px; min-height: 48px; }
  .level[aria-pressed="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .row button[aria-pressed="true"] { outline: 2px solid var(--accent); outline-offset: 2px; }
  .level span { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .row { display: flex; gap: 8px; }
  .nav { display: flex; justify-content: space-between; gap: 8px; margin-top: 8px; }
  .row button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); border-color: var(--accent); outline: none; }
  .row button { flex: 1; }
  button { min-height: 48px; padding: 0 18px; border-radius: 24px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; font-weight: 500; }
  .primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 600; }
  .skip { background: none; border: none; color: var(--muted); }
  .areas { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
  .check { display: flex; gap: 10px; align-items: center; min-height: 40px; }
  .check input { width: 22px; height: 22px; accent-color: var(--accent); }
</style>

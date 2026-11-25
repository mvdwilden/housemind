<script lang="ts">
  // The wall tablet (E-134): the room it hangs in first, big Orb, clock and weather, Ask and
  // Pause. Awake when someone is in the room or just touched it; nearly dark at night.
  import { trap } from "../lib/focus";
  import Orb from "../lib/Orb.svelte";
  import Glyph from "../components/Glyph.svelte";
  import ProposalCard from "../components/ProposalCard.svelte";
  import { cover, fullscreen, keepAwake, kioskWanted, uncover, type KioskWindow } from "../lib/kiosk";
  import { doorbellToShow } from "../lib/doorbell";
  import { openMoreInfo } from "../lib/openha";
  import { orbLook } from "../lib/orb";
  import type { Snapshot } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    snap: Snapshot;
    orb?: string; // what the Orb shows now (thinking while a question is answered)
    t: T;
    said: string | null;
    send: (message: Record<string, unknown>) => void;
    onpause: () => void;
    onanswer: (proposal: number, answer: string) => void;
    onexit: () => void;
    speaking?: boolean;
    listening?: boolean;
  }
  let { snap, orb, t, said, send, onpause, onanswer, onexit, speaking = false, listening = false }: Props = $props();
  const tablet = $derived(snap.tablet!);
  const orbState = $derived(orb ?? snap.orb);
  const look = $derived(orbLook(orbState, Math.max(1, snap.level)));
  let now = $state(new Date());
  let touched = $state(0);
  $effect(() => {
    const timer = setInterval(() => (now = new Date()), 15000);
    return () => clearInterval(timer);
  });
  // Kiosk mode (E-134): only HouseMind on the screen, and the screen kept on.
  let storage: Storage | undefined;
  try {
    storage = window.localStorage;
  } catch {
    storage = undefined;
  }
  const kiosk = kioskWanted(storage);
  $effect(() => {
    if (!kiosk) return;
    const win = window as unknown as KioskWindow;
    cover(win);
    let release: (() => void) | null = null;
    const awake = async () => {
      release?.();
      release = await keepAwake(navigator as unknown as Parameters<typeof keepAwake>[0]);
    };
    void awake();
    const again = () => { if (document.visibilityState === "visible") void awake(); };
    document.addEventListener("visibilitychange", again);
    return () => {
      document.removeEventListener("visibilitychange", again);
      release?.();
      uncover(win);
    };
  });
  // The doorbell (E-187): its camera for 30 seconds, only in kiosk mode; ringing again starts
  // the 30 seconds over. The live image comes straight from Home Assistant, never through
  // HouseMind, and nothing is kept. Only looking: no door opens, no camera controls.
  let closedAt = $state<string | null>(null);
  let doorbellLeft = $state(0);
  const ring = $derived(doorbellToShow(kiosk, tablet.doorbell, closedAt));
  $effect(() => {
    const bell = ring;
    if (!bell) {
      doorbellLeft = 0;
      return;
    }
    const until = Date.now() + bell.left * 1000;
    doorbellLeft = bell.left;
    touched = Date.now(); // the screen wakes up
    const timer = setInterval(() => (doorbellLeft = Math.max(0, (until - Date.now()) / 1000)), 250);
    return () => clearInterval(timer);
  });
  // The alarm or a fire (E-544): red over everything while it goes on, the screen awake.
  const alarm = $derived(tablet.alarm ?? null);
  $effect(() => {
    if (alarm) touched = Date.now();
  });
  const recently = $derived(now.getTime() - touched < 60000);
  const awake = $derived(tablet.awake || recently);
  const clock = $derived(now.toLocaleTimeString(snap.language, { hour: "2-digit", minute: "2-digit" }));
  function touch() {
    touched = Date.now();
    now = new Date();
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="tablet" class:asleep={!awake} class:night={tablet.night} onpointerdown={touch} onkeydown={touch}>
  <header>
    <span class="clock">{clock}</span>
    {#if tablet.weather}<span class="weather">{tablet.weather}</span>{/if}
  </header>

  <main>
    <Orb state={orbState} level={Math.max(1, snap.level)} size={awake ? 220 : 120} {speaking} {listening} label="{t(look.textKey)}. {t('orb.tap_hint')}"
      ontap={() => send({ type: "orb_tap" })} onhold={onpause} />
    {#if awake}
      <h1><Glyph shape={look.shape} color={look.color} deep={look.deep} size={20} /> {t(look.textKey)}</h1>
      <p class="room"><strong>{tablet.name}</strong> · {tablet.summary}</p>
      {#if said}<p class="said" role="status">{said}</p>{/if}
      {#if tablet.scenes.length}
        <div class="chips" aria-label={t("ui.scenes.title")}>
          {#each tablet.scenes as scene (scene.id)}
            <button class="chip" onclick={() => send({ type: "scene", area: tablet.area, id: scene.id })}>{scene.name}</button>
          {/each}
        </div>
      {/if}
      {#if snap.proposals?.length && !tablet.night}
        <ProposalCard proposal={snap.proposals[0]} {t} onanswer={(a) => onanswer(snap.proposals![0].id, a)} />
      {/if}
      <div class="actions">
        <button class="pill" onclick={() => send({ type: "orb_tap" })}>{t("ui.ask")}</button>
        <button class="pill" onclick={onpause}>{t("ui.pause")}</button>
      </div>
    {/if}
  </main>

  {#if alarm}
    <div class="alarm" class:fire={alarm.kind !== "burglary"} role="alertdialog" aria-modal="true" aria-label={alarm.title} tabindex="-1" use:trap data-alarm>
      <h2>{alarm.title}</h2>
      <p class="where">{alarm.where ? `${alarm.where} · ` : ""}{alarm.at}</p>
      {#if alarm.url}<img src={alarm.url} alt={alarm.title} />{/if}
      <p class="advice">{alarm.advice}</p>
      {#if alarm.alarm}<button class="pill" onclick={() => openMoreInfo(alarm.alarm!)}>{t("ui.tablet.alarm.open")}</button>{/if}
    </div>
  {:else if ring && doorbellLeft > 0}
    <div class="doorbell" role="dialog" aria-modal="true" aria-label={t("ui.tablet.doorbell")} tabindex="-1" use:trap data-doorbell>
      <h2>{t("ui.tablet.doorbell")}{ring.where ? ` · ${ring.where}` : ""}</h2>
      {#if ring.url}<img src={ring.url} alt={t("ui.tablet.doorbell")} />{:else}<p class="none">{t("ui.tablet.doorbell_no_image")}</p>{/if}
      <div class="bar" style="--left: {doorbellLeft / ring.seconds}"></div>
      <button class="pill" onclick={() => (closedAt = ring.at)}>{t("ui.tablet.doorbell_close")}</button>
    </div>
  {/if}

  {#if awake}
    <div class="corner">
      <button class="exit" onclick={() => fullscreen(window as unknown as KioskWindow)}>{t("ui.tablet.fullscreen")}</button>
      <button class="exit" onclick={() => { uncover(window as unknown as KioskWindow); onexit(); }}>{t("ui.tablet.exit")}</button>
    </div>
  {/if}
</div>

<style>
  .tablet { min-height: 100dvh; display: flex; flex-direction: column; padding: 24px 28px; gap: 16px; background: var(--bg); color: var(--text); transition: background 1.2s, opacity 1.2s; }
  header { display: flex; justify-content: space-between; align-items: baseline; }
  .clock { font-size: calc(56px * var(--text-scale)); font-weight: 300; letter-spacing: 1px; }
  .weather { font-size: calc(20px * var(--text-scale)); color: var(--muted); }
  main { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; text-align: center; }
  h1 { margin: 6px 0 0; font-size: calc(28px * var(--text-scale)); display: inline-flex; align-items: center; gap: 8px; }
  .room { margin: 0; font-size: calc(18px * var(--text-scale)); color: var(--muted); }
  .said { margin: 0; font-size: calc(18px * var(--text-scale)); max-width: 40ch; }
  .chips { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }
  .chip { min-height: 48px; padding: 0 20px; border-radius: 24px; border: 1px solid var(--line); background: var(--surface); color: inherit; font-size: calc(17px * var(--text-scale)); cursor: pointer; }
  .actions { display: flex; gap: 12px; }
  .pill { min-height: 52px; padding: 0 28px; border-radius: 26px; border: 1px solid var(--line); background: var(--surface); color: inherit; font-size: calc(18px * var(--text-scale)); cursor: pointer; }
  .corner { align-self: flex-end; display: flex; gap: 8px; }
  .exit { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--line); background: transparent; color: var(--muted); font-size: calc(13px * var(--text-scale)); cursor: pointer; }
  .asleep { background: #000; color: #555; }
  .asleep header { opacity: 0.35; }
  .night.asleep main { opacity: 0.2; }
  .night.asleep header { opacity: 0; }
  /* At night less light, but text that stays readable (M-25, WCAG 1.4.3): black, softer text
     that still reaches 4.5:1, and only the Orb itself dimmed. */
  .night:not(.asleep) { background: #000; --text: #b4bccf; --muted: #9099b0; --surface: #0b0e16; --line: #2a3145; --edge: #5d6884; }
  .night:not(.asleep) :global(.orb) { filter: brightness(0.6); }
  .alarm { position: fixed; inset: 0; z-index: 30; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; padding: 24px; background: #B3261E; color: #fff; text-align: center; }
  .alarm h2 { margin: 0; font-size: calc(34px * var(--text-scale)); }
  .alarm .where { margin: 0; font-size: calc(22px * var(--text-scale)); }
  .alarm .advice { margin: 0; max-width: 34ch; font-size: calc(20px * var(--text-scale)); }
  .alarm img { max-width: 100%; max-height: 50dvh; border-radius: 16px; background: #000; }
  .alarm .pill { background: #fff; color: #B3261E; border: 0; font-weight: 600; }
  .doorbell { position: fixed; inset: 0; z-index: 20; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; padding: 24px; background: var(--bg); color: var(--text); }
  .doorbell h2 { margin: 0; font-size: calc(26px * var(--text-scale)); }
  .doorbell img { max-width: 100%; max-height: 70dvh; border-radius: 16px; background: #000; }
  .doorbell .none { color: var(--muted); font-size: calc(18px * var(--text-scale)); }
  .bar { width: min(560px, 100%); height: 6px; border-radius: 3px; background: var(--accent); transform-origin: left; transform: scaleX(var(--left)); transition: transform 0.25s linear; }
</style>

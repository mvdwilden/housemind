<script lang="ts">
  // The Vesta window (E-236, E-237, B-85): a tap on the Orb opens it. A big Orb, her words as
  // a subtitle, the conversation; while she listens: the beating ring, "Ik luister…", a meter
  // that moves with your voice, six seconds running down, and Klaar and Stop. The same window
  // on every level, for talking and for typing; her yes or no questions are answered here too
  // (E-241).
  import Orb from "../lib/Orb.svelte";
  import { trap } from "../lib/focus";
  import { meter } from "../lib/listen";
  import { voiceLevel } from "../lib/voice";
  import { doing, type VestaChoice, type VestaWindow } from "../lib/vesta";
  import type { T } from "../lib/ui";

  interface Props {
    vesta: VestaWindow;
    orb: string;
    level: number;
    name: string;
    speaking: boolean;
    t: T;
    onclose: () => void;
    onfinish: () => void;
    oncancel: () => void;
    onretry: () => void;
    ontype: (text: string) => void;
    ontyping: (at: boolean) => void; // in the typing field: she stops listening, the window stays
    onanswer: (yes: boolean, word: string) => void;
    onthumbs?: (decision: number, good: boolean) => void;
    onask: (action: string, word: string) => void;
    onopen: () => void;
    ondecline: (word: string) => void;
    onmore: (more: import("../lib/vesta").VestaMore) => void;
    aloud: boolean; // her voice on this device
    onaloud: (on: boolean) => void;
    houseMode?: string; // the one-tap buttons Vacation and Guests, or Home (E-286)
    onmode?: (mode: string) => void;
    listenMs?: number; // the person's time to talk (E-299)
    onmoretime?: () => void; // "Nog even": the time starts again
    onchoice?: (choice: VestaChoice) => void; // one of her choices (E-349)
    ai?: boolean; // an AI is on: you may also answer her question in your own words (E-349)
    ontalk?: () => void; // a tap on her in the window: she listens (E-464)
    canlisten?: boolean; // this device can listen
  }
  let { vesta, orb, level, name, speaking, t, onclose, onfinish, oncancel, onretry, ontype, ontyping, onanswer, onask, onopen, ondecline, onmore, aloud, onaloud, houseMode = "home", onmode, listenMs = 6000, onmoretime, onthumbs, onchoice, ai = false, ontalk, canlisten = false }: Props = $props();
  const away = $derived(houseMode === "vacation" || houseMode === "guests");

  // The words on the buttons under her question: the same as in the notification list.
  function word(action: string): string {
    if (action === "why") return t("ui.vesta.why_ask"); // Waarom? under a proposal (E-349)
    if (action.startsWith("later_")) return t(`ui.notice.later.${action.slice(6)}`); // how long (E-381)
    const kind = vesta.ask?.kind;
    if (kind === "proposal" || kind === "thumbs") return t(`notify.answer.${action}`);
    if (action === "later") return t("notify.answer.later"); // "Nog niet" (E-460)
    if (kind === "automation" || kind === "restore" || kind === "vacation" || kind === "rule_suggest") {
      return t(`ui.${action}`);
    }
    if (kind === "unusual") return t(action === "yes" ? "notify.answer.turn_off" : "notify.answer.keep_on");
    if (kind === "knowledge") return t(`ui.knowledge.${action}`); // Klopt, Klopt niet (B-360)
    return t(`ui.notifications.${action}`);
  }

  // Typing to her: the field under the conversation. After a typed question the focus moves
  // here, so the next words go to her and not to the bar behind the window.
  let typed = $state("");
  let field = $state<HTMLInputElement | null>(null);
  $effect(() => {
    if (vesta.typed && vesta.phase !== "listening" && field && document.activeElement !== field) field.focus();
  });
  // A tap on her (E-464): she listens; a device that cannot listen goes to the typing field.
  function tapped(): void {
    if (canlisten) ontalk?.();
    else field?.focus();
  }
  function type(event: SubmitEvent): void {
    event.preventDefault();
    const text = typed.trim();
    if (!text) return;
    ontype(text);
    typed = "";
  }

  // A clock for the meter and the bar while she listens, and for her movement while she
  // speaks (a frame at a time).
  let now = $state(0);
  $effect(() => {
    if (vesta.phase !== "listening" && !speaking) return;
    let frame = requestAnimationFrame(function tick(at) {
      now = at;
      frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  });
  const left = $derived(
    vesta.phase === "listening" ? Math.max(0, 1 - (now - vesta.listenStart) / listenMs) : 0,
  );
  const bars = $derived(meter(vesta.level, now));
  const voice = $derived(speaking ? voiceLevel(now) : 0);
  const last = $derived([...vesta.turns].reverse().find((turn) => turn.who === "vesta"));
  // An emergency shows also while she thinks.
  const shown = $derived(
    (vesta.phase === "thinking" || vesta.phase === "hearing") && orb !== "emergency" ? "thinking" : orb,
  );
  // What she does now, in words next to her name (E-269): the same as her Orb shows.
  const doingWords = $derived(t(`ui.vesta.now.${doing(shown, vesta.phase, speaking)}`));
  // Her own buttons are always there, also while she listens (E-464).
  const hers = $derived(Boolean(vesta.ask || vesta.offer || vesta.confirm || (vesta.choices && onchoice)));
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="backdrop" role="presentation" onclick={onclose}>
  <div
    class="window"
    role="dialog"
    aria-modal="true"
    aria-label={name}
    tabindex="-1"
    use:trap
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.key === "Escape" && onclose()}
  >
    <!-- Close and her voice: big enough to see and to hit at a glance (B-379). -->
    <button class="close" aria-label={t("ui.close")} onclick={onclose}>
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
    </button>
    <!-- Her voice off or on for this device, in one tap (E-253). -->
    <button class="speaker" aria-pressed={!aloud} aria-label={aloud ? t("ui.vesta.mute") : t("ui.vesta.unmute")} title={aloud ? t("ui.vesta.mute") : t("ui.vesta.unmute")} onclick={() => onaloud(!aloud)}>
      {#if aloud}
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4z" /><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11" /></svg>
      {:else}
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4z" /><path d="M17 9l5 6M22 9l-5 6" /></svg>
      {/if}
    </button>
    <div class="orb">
      <Orb state={shown} level={Math.max(level, 2)} size={150} {speaking} {voice} listening={vesta.phase === "listening"} label="{name} · {doingWords}. {t('orb.tap_hint')}" ontap={tapped} />
    </div>
    <p class="name">{name} <span class="doing">· {doingWords}</span></p>
    {#if last}
      <!-- An AI's answer as it is written grows here; her answer replaces it (E-159). -->
      <p class="subtitle" class:partial={last.partial} aria-live={last.partial ? "off" : "polite"}>{last.text}</p>
      {#if last.ai}<p class="via">{t("ui.vesta.via_ai")}</p>{/if}
      {#if last.more}
        <button class="read" onclick={() => last.more && onmore(last.more)}>{t("ui.vesta.more")}</button>
      {/if}
      {#if last.thumbs && onthumbs}
        {@const decision = last.thumbs}
        <!-- Was it good what she did (E-298): one tap, from level 2. -->
        <div class="buttons thumbs" role="group" aria-label={t("ui.thumbs.label")}>
          <button onclick={() => onthumbs?.(decision, true)} aria-label={t("ui.thumbs.up")}>{t("notify.answer.good")}</button>
          <button onclick={() => onthumbs?.(decision, false)} aria-label={t("ui.thumbs.down")}>{t("notify.answer.bad")}</button>
        </div>
      {/if}
    {/if}

    {#if vesta.ask}
      <div class="buttons" class:four={vesta.ask.actions.length > 2}>
        {#each vesta.ask.actions as action, i (action)}
          <button class:primary={i === 0} onclick={() => onask(action, word(action))}>{word(action)}</button>
        {/each}
      </div>
    {/if}
    {#if vesta.offer}
      <div class="buttons">
        <button class="primary" onclick={onopen}>{t("ui.vesta.open")}</button>
        <button onclick={() => ondecline(t("ui.no"))}>{t("ui.no")}</button>
      </div>
    {/if}
    {#if vesta.confirm}
      <div class="buttons">
        <button class="primary" onclick={() => onanswer(true, t("ui.yes"))}>{t("ui.yes")}</button>
        <button onclick={() => onanswer(false, t("ui.no"))}>{t("ui.no")}</button>
      </div>
    {/if}
    {#if vesta.choices && onchoice}
      <!-- Her choices (E-349): after a tap without an AI, or the AI asking back. -->
      <div class="modes" role="group" aria-label={t("ui.vesta.choices")}>
        {#each vesta.choices as choice (choice.id)}
          <button class="mode" onclick={() => onchoice?.(choice)}>{choice.text}</button>
        {/each}
      </div>
    {/if}

    {#if vesta.phase === "listening"}
      <p class="status" role="status">{t("ui.vesta.listening")}</p>
      <div class="meter" aria-hidden="true">
        {#each bars as bar, i (i)}
          <span style:transform="scaleY({bar})"></span>
        {/each}
      </div>
      <div class="time" role="progressbar" aria-valuemin="0" aria-valuemax={listenMs / 1000} aria-valuenow={Math.ceil(left * listenMs / 1000)} aria-label={t("ui.vesta.seconds", { n: Math.ceil(left * listenMs / 1000) })}>
        <span style:transform="scaleX({left})"></span>
      </div>
      <p class="seconds">{t("ui.vesta.seconds", { n: Math.ceil(left * listenMs / 1000) })}</p>
      <div class="buttons">
        <!-- Under her own buttons only Stop: the rest would be more buttons than answers. -->
        {#if !hers}
          <button class="primary" onclick={onfinish}>{t("ui.vesta.done")}</button>
          {#if onmoretime}<button onclick={onmoretime}>{t("ui.vesta.more_time")}</button>{/if}
        {/if}
        <button onclick={oncancel}>{t("ui.vesta.stop")}</button>
      </div>
    {:else if vesta.phase === "hearing"}
      <p class="status" role="status">{t("ui.vesta.hearing")}</p>
    {:else if vesta.phase === "thinking" && !last}
      <p class="status" role="status">{t("orb.thinking")}</p>
    {/if}
    {#if !speaking && vesta.phase === "idle" && canlisten && !vesta.noMic}
      <!-- She listens after a tap (E-464): this tells how. -->
      <p class="hint again">{t("orb.tap_hint")}</p>
    {/if}
    {#if onmode && vesta.phase !== "listening" && !vesta.ask && !vesta.offer && !vesta.confirm}
      <div class="modes" role="group" aria-label={t("ui.vesta.modes")}>
        {#if away}
          <button class="mode" onclick={() => onmode?.("home")}>{t("ui.vesta.mode.home")}</button>
        {:else}
          <button class="mode" onclick={() => onmode?.("vacation")}>{t("ui.vesta.mode.vacation")}</button>
          <button class="mode" onclick={() => onmode?.("guests")}>{t("ui.vesta.mode.guests")}</button>
        {/if}
      </div>
    {/if}
    {#if vesta.turns.length > 1}
      <ol class="turns">
        {#each vesta.turns.slice(0, -1) as turn, i (i)}
          <li class:you={turn.who === "you"}>
            <strong>{turn.who === "you" ? t("ui.vesta.you") : name}</strong>
            {turn.who === "you" ? `“${turn.text}”` : turn.text}
          </li>
        {/each}
      </ol>
    {/if}
    {#if vesta.noMic && vesta.micHelp}
      {@const help = vesta.micHelp}
      <div class="mic" role="status">
        <p class="micTitle">{t("ui.mic.title")}</p>
        <p>{t(`ui.mic.${help.problem}`)}</p>
        {#if help.problem === "denied"}
          <p class="steps">{t(`ui.mic.steps.${help.browser}`)}</p>
        {:else if help.problem === "insecure"}
          <!-- At home the HA app often uses the plain http address: say how to switch to https. -->
          <p class="steps">{t(help.browser === "ha_ios" || help.browser === "ha_android" ? `ui.mic.insecure_${help.browser}` : "ui.mic.insecure_browser")}</p>
        {/if}
        <p class="hint">{t("ui.mic.assist")}</p>
        {#if help.problem !== "insecure" && help.problem !== "no_device"}
          <button class="retry" onclick={onretry}>{t("ui.mic.retry")}</button>
        {/if}
      </div>
    {:else if vesta.noMic}
      <p class="hint">{t("ui.mic.short")}</p>
    {/if}
    <!-- Typing is always there, also while she listens: a tap in the field stops her listening,
         and the window stays open while you are at it (E-264: no separate talk bar any more). -->
    <!-- While she asks, her buttons are enough without an AI; with an AI you may also answer
         in your own words (E-349). -->
    {#if (!vesta.confirm && !vesta.ask && !vesta.offer) || ai}
      <form class="type" onsubmit={type}>
        <label class="sr" for="vesta-type">{t("ui.vesta.type")}</label>
        <input id="vesta-type" bind:this={field} bind:value={typed} placeholder={t("ui.vesta.type")} maxlength="500" autocomplete="off" enterkeyhint="send"
          onfocus={() => ontyping(true)} oninput={() => ontyping(true)} onblur={() => { if (!typed.trim()) ontyping(false); }} />
        <button type="submit" aria-label={t("ui.send")} disabled={!typed.trim()}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6" /></svg>
        </button>
      </form>
    {/if}
  </div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; z-index: 40; display: flex; align-items: center; justify-content: center; padding: 16px; background: rgba(5, 8, 15, 0.55); backdrop-filter: blur(6px); }
  .window { position: relative; width: min(460px, 100%); max-height: calc(100vh - 32px); overflow: auto; display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 24px 20px 20px; border-radius: 24px; background: var(--surface); border: 1px solid var(--line); box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35); text-align: center; outline: none; }
  .close, .speaker { position: absolute; top: 12px; width: 52px; height: 52px; border-radius: 26px; border: none; background: var(--raised); color: var(--text); display: grid; place-items: center; cursor: pointer; }
  .close { right: 12px; }
  .speaker { left: 12px; }
  .speaker[aria-pressed="true"] { color: var(--accent); }
  .name { margin: 0; color: var(--accent); font-size: calc(13px * var(--text-scale)); letter-spacing: 0.04em; text-transform: uppercase; }
  .doing { color: var(--muted); font-size: calc(14px * var(--text-scale)); letter-spacing: normal; text-transform: none; }
  .status { margin: 0; font-size: calc(20px * var(--text-scale)); font-weight: 600; }
  .subtitle { margin: 4px 0 0; font-size: calc(20px * var(--text-scale)); line-height: 1.45; }
  .subtitle.partial { color: var(--muted); }
  .meter { display: flex; gap: 6px; align-items: center; height: 44px; }
  .meter span { display: block; width: 8px; height: 44px; border-radius: 4px; background: var(--accent); transform-origin: center; transition: transform 80ms linear; }
  .time { width: 70%; height: 6px; border-radius: 3px; background: var(--line); overflow: hidden; }
  .time span { display: block; height: 100%; background: var(--accent); transform-origin: left; }
  .buttons { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; margin-top: 4px; max-width: 100%; }
  .buttons button { min-width: min(110px, 100%); min-height: 48px; padding: 0 16px; border-radius: 24px; border: 1px solid var(--edge); background: var(--raised); color: inherit; font-size: calc(16px * var(--text-scale)); cursor: pointer; }
  .buttons.four { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }
  .buttons.thumbs button { min-width: 0; padding: 0 18px; }
  .buttons .primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  .turns { list-style: none; margin: 8px 0 0; padding: 0; width: 100%; display: flex; flex-direction: column; gap: 6px; text-align: left; }
  .turns li { padding: 8px 12px; border-radius: 12px; background: var(--raised); font-size: calc(15px * var(--text-scale)); }
  .turns li.you { align-self: flex-end; background: transparent; border: 1px solid var(--line); font-style: italic; }
  .turns strong { display: block; font-size: calc(12px * var(--text-scale)); color: var(--muted); font-style: normal; }
  .hint { margin: 6px 0 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .hint.again { margin: 0; }
  .mic { width: 100%; margin-top: 6px; padding: 14px 16px; border-radius: 16px; background: var(--raised); text-align: left; display: flex; flex-direction: column; gap: 6px; }
  .mic p { margin: 0; font-size: calc(15px * var(--text-scale)); line-height: 1.45; }
  .micTitle { font-weight: 600; font-size: calc(16px * var(--text-scale)); }
  .steps { padding: 8px 10px; border-radius: 10px; border: 1px solid var(--line); }
  .retry { align-self: flex-start; min-height: 44px; padding: 0 18px; border-radius: 22px; border: 1px solid var(--accent); background: transparent; color: var(--accent); font-size: calc(15px * var(--text-scale)); cursor: pointer; margin-top: 4px; }
  .via { margin: -4px 0 0; color: var(--ai); font-size: calc(12px * var(--text-scale)); letter-spacing: 0.03em; }
  .read { min-height: 40px; padding: 0 16px; border-radius: 20px; border: 1px solid var(--accent); background: transparent; color: var(--accent); font-size: calc(15px * var(--text-scale)); cursor: pointer; }
  .type { width: 100%; display: flex; gap: 8px; align-items: center; margin-top: 8px; border: 1px solid var(--edge); border-radius: 24px; padding: 3px 3px 3px 16px; }
  .type:focus-within { border-color: var(--accent); }
  .type input { flex: 1; min-width: 0; height: 42px; border: none; background: transparent; color: var(--text); font: inherit; font-size: calc(16px * var(--text-scale)); outline: none; }
  .type input::placeholder { color: var(--muted); }
  .type button { width: 42px; height: 42px; border-radius: 21px; border: none; background: var(--accent); color: var(--on-accent); display: grid; place-items: center; cursor: pointer; }
  .type button:disabled { background: var(--raised); color: var(--muted); cursor: default; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .seconds { display: none; margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  @media (prefers-reduced-motion: reduce) {
    .meter, .time { display: none; }
    .seconds { display: block; } /* the countdown as a number, nothing moving (17.13) */
  }
  .modes { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
  .mode { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--surface); color: var(--text); cursor: pointer; font-size: calc(14px * var(--text-scale)); }
</style>

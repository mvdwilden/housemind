<script lang="ts">
  // Help is the manual (E-506, the administrator's wish of 12-11-2026): the Help button opens
  // the manual that ships with the app (E-238) at once – the same formatted page as online
  // (E-324), its own page in a frame with its own contents – with "Vraag het Vesta" above it
  // (E-253). The tour has its own button beside Help (E-249); the short questions per level are
  // no longer shown, Vesta still knows them (E-246). Groter/Kleiner switches between this
  // window and the whole screen, a new window opens it on its own; on a phone the manual has
  // the whole screen already. Lees meer under Vesta's answer goes straight to its part; an
  // older one to a Help question goes to the part help-links.yaml names for it.
  import { untrack } from "svelte";
  import type { T } from "../lib/ui";

  export interface HelpFocus {
    kind: "manual" | "help";
    id: string; // a manual section id, or an older Help question: "help:l2:q18"
  }

  interface Props {
    t: T;
    manual?: { available: boolean; links: Record<string, string> };
    lang: string;
    theme?: string | null; // "light" or "dark" as the app shows it; null: the device decides
    large?: boolean; // the app's large text (E-46)
    name: string;
    focus?: HelpFocus | null;
    big?: boolean; // Groter: the whole screen (E-324)
    onclose?: () => void; // Escape inside the manual
    onask?: (text: string) => void; // "Vraag het Vesta" (E-253)
    onlisten?: () => void; // ask her aloud, when this device can listen
  }
  let { t, manual, lang, theme = null, large = false, name, focus = null, big = $bindable(false), onclose, onask, onlisten }: Props = $props();
  let question = $state("");
  function ask(event: SubmitEvent): void {
    event.preventDefault();
    const text = question.trim();
    if (!text) return;
    onask?.(text);
    question = "";
  }

  // The part of the manual asked for: its id, or the part a Help question links to.
  function target(asked: HelpFocus | null): string | null {
    if (!asked) return null;
    if (asked.kind === "manual") return asked.id;
    return manual?.links[asked.id.replace(/^help:/, "").replace(":", ".")] ?? null;
  }

  let frame = $state<HTMLIFrameElement | null>(null);
  let part = $state<string | null>(untrack(() => target(focus)));
  let width = $state(typeof innerWidth === "number" ? innerWidth : 1024);
  // The page: the server puts in her name, the theme and large text (B-192).
  const page = $derived(
    `manual/${lang}?${new URLSearchParams({ ...(theme ? { thema: theme === "dark" ? "donker" : "licht" } : {}), ...(large ? { tekst: "groot" } : {}) })}`,
  );
  // Below 900 px the window has the whole screen already; a new window has no login in the
  // Home Assistant app, and a phone needs none.
  const resizable = $derived(width >= 900);
  const separate = $derived(width >= 640 && !navigator.userAgent.includes("Home Assistant/"));

  function still(): boolean {
    return matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // To the part asked for, also when the page is open already (the same anchor twice).
  function toPart(): void {
    if (!part) return;
    frame?.contentDocument?.getElementById(part)?.scrollIntoView({ block: "start", behavior: still() ? "auto" : "smooth" });
  }

  function apart(): void {
    window.open(new URL(page + (part ? `#${part}` : ""), location.href).href, "_blank", "noopener");
  }

  // Escape inside the manual's page closes the window, as everywhere (B-193).
  function message(event: MessageEvent): void {
    if (event.origin === location.origin && event.data === "housemind:manual-close") onclose?.();
  }

  // Vesta's Lees meer while the manual is open: to that part.
  $effect(() => {
    const id = target(focus);
    untrack(() => {
      if (!id) return;
      part = id;
      toPart();
    });
  });
</script>

<svelte:window bind:innerWidth={width} onmessage={message} />

<div class="reader">
  <div class="top">
    {#if onask}
      <!-- Vraag het Vesta: her answer comes from the manual and what the Help knows, with Lees meer. -->
      <form class="ask" onsubmit={ask}>
        <label class="sr" for="help-ask">{t("ui.vesta.ask_help", { name })}</label>
        <input id="help-ask" bind:value={question} placeholder={t("ui.vesta.ask_help", { name })} maxlength="500" autocomplete="off" enterkeyhint="send" />
        {#if onlisten && !question.trim()}
          <button type="button" class="mic" aria-label={t("ui.vesta.talk", { name })} onclick={onlisten}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
          </button>
        {/if}
        <button type="submit" aria-label={t("ui.send")} disabled={!question.trim()}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6" /></svg>
        </button>
      </form>
    {/if}
    {#if manual?.available && (resizable || separate)}
      <span class="side">
        {#if resizable}<button aria-pressed={big} onclick={() => (big = !big)}>{big ? t("ui.manual.smaller") : t("ui.manual.larger")}</button>{/if}
        {#if separate}<button onclick={apart}>{t("ui.manual.apart")}</button>{/if}
      </span>
    {/if}
  </div>
  {#if manual?.available}
    <!-- The manual that ships with the app (knowledge/manual), as its own page (E-324). -->
    <iframe bind:this={frame} title={t("ui.manual.title")} src={page + (part ? `#${part}` : "")} onload={toPart}></iframe>
  {:else}
    <p class="none">{t("ui.manual.none")}</p>
  {/if}
</div>

<style>
  /* The manual fills the window, and the whole screen where the window has it (E-324). */
  .reader { display: flex; flex-direction: column; gap: 10px; flex: 1; min-height: 0; }
  .top { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .ask { flex: 1 1 280px; display: flex; gap: 8px; align-items: center; border: 1px solid var(--edge); border-radius: 26px; padding: 4px 4px 4px 18px; background: var(--raised); }
  .ask:focus-within { border-color: var(--accent); }
  .ask input { flex: 1; min-width: 0; height: 44px; border: none; background: transparent; color: var(--text); font: inherit; outline: none; }
  .ask button { width: 44px; height: 44px; border-radius: 22px; border: none; background: var(--accent); color: var(--on-accent); display: grid; place-items: center; cursor: pointer; }
  .ask button:disabled { background: var(--surface); color: var(--muted); cursor: default; }
  .ask .mic { background: transparent; color: var(--accent); border: 1px solid var(--line); }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .side { display: flex; gap: 8px; margin-left: auto; }
  .side button { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--line); background: var(--raised); color: var(--text); cursor: pointer; }
  .side button[aria-pressed="true"] { border-color: var(--accent); }
  .none { margin: 0; color: var(--muted); line-height: 1.5; }
  iframe { flex: 1; width: 100%; min-height: min(70vh, 640px); border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
</style>

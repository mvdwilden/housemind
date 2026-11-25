<script lang="ts">
  import type { Snippet } from "svelte";
  import { trap } from "../lib/focus";

  interface Props {
    title: string;
    closeLabel: string;
    onclose: () => void;
    wide?: boolean;
    full?: boolean; // the whole screen on a phone (the manual, E-238)
    whole?: boolean; // the whole screen everywhere (the manual's Groter, E-324)
    children: Snippet;
    foot?: Snippet; // short messages at the foot of the sheet, never over it (E-259, B-107)
    brief?: Snippet; // "Bewaard" as plain text in the head, next to Close (E-174)
  }

  let { title, closeLabel, onclose, wide = false, full = false, whole = false, children, foot, brief }: Props = $props();
</script>

<svelte:window onkeydown={(e) => e.key === "Escape" && onclose()} />

<div class="bg" role="presentation" onclick={onclose}></div>
<div class="sheet" class:wide class:full class:whole role="dialog" aria-modal="true" aria-label={title} tabindex="-1" use:trap>
  <div class="head">
    <h2>{title}</h2>
    {#if brief}<span class="side">{@render brief()}</span>{/if}
    <button class="x" aria-label={closeLabel} onclick={onclose}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
    </button>
  </div>
  <div class="body">{@render children()}</div>
  {#if foot}<div class="foot">{@render foot()}</div>{/if}
</div>

<style>
  .bg { position: fixed; inset: 0; background: rgba(3, 6, 14, 0.6); z-index: 20; }
  .sheet {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 21; max-width: 560px; margin: 0 auto;
    max-height: 88vh; display: flex; flex-direction: column; background: var(--surface);
    border-radius: 22px 22px 0 0; border: 1px solid var(--line); border-bottom: none; outline: none;
    padding-bottom: env(safe-area-inset-bottom);
  }
  .sheet.wide { max-width: 760px; }
  @media (max-width: 899px) {
    .sheet.full { top: 0; max-height: none; border-radius: 0; border: none; }
  }
  .head { display: flex; align-items: center; justify-content: space-between; padding: 16px 16px 8px 20px; }
  h2 { font-family: var(--font-display); font-size: calc(19px * var(--text-scale)); margin: 0; }
  .side { margin-left: auto; margin-right: 8px; display: flex; min-width: 0; }
  .x { width: 44px; height: 44px; border-radius: 22px; border: none; background: var(--raised); color: var(--muted); display: grid; place-items: center; cursor: pointer; }
  .body { flex: 1 1 auto; min-height: 0; overflow: auto; padding: 4px 20px 20px; display: flex; flex-direction: column; gap: 14px; container: sheet / inline-size; }
  .foot { flex: none; padding: 10px 16px 14px; border-top: 1px solid var(--line); }
  @media (min-width: 900px) {
    .sheet { top: 50%; bottom: auto; transform: translateY(-50%); border-radius: 22px; border: 1px solid var(--line); }
  }
  .sheet.whole { inset: 0; max-width: none; max-height: none; transform: none; border-radius: 0; border: none; }
</style>

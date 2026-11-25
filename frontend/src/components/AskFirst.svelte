<script lang="ts">
  // One yes before something goes for good (QA 2, L9; like ForgetPair): the question takes the
  // place of the button, then Ja or Nee. The focus moves to Nee and comes back afterwards.
  import { tick } from "svelte";
  import type { T } from "../lib/ui";

  interface Props {
    text: string; // on the button, like "Verwijderen" or "×"
    label: string; // what the button does, for a screen reader
    question: string;
    onyes: () => void;
    t: T;
  }
  let { text, label, question, onyes, t }: Props = $props();
  let asking = $state(false);
  let no = $state<HTMLButtonElement | null>(null);
  let button = $state<HTMLButtonElement | null>(null);

  async function ask(on: boolean): Promise<void> {
    asking = on;
    await tick();
    (on ? no : button)?.focus();
  }
</script>

{#if asking}
  <span class="ask" role="group" aria-label={label}>
    <span>{question}</span>
    <button class="primary" onclick={() => { asking = false; onyes(); }}>{t("ui.yes")}</button>
    <button bind:this={no} onclick={() => ask(false)}>{t("ui.no")}</button>
  </span>
{:else}
  <button class="remove" bind:this={button} aria-label={label} onclick={() => ask(true)}>{text}</button>
{/if}

<style>
  .ask { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .ask button, .remove { min-height: 44px; min-width: 44px; padding: 0 14px; border-radius: 22px; border: 1px solid var(--edge); background: var(--surface); color: var(--text); cursor: pointer; }
  .ask .primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
</style>

<script lang="ts">
  // "Zelf een stap zetten" (20.10): an administrator picks Observeren, Voorstellen or Autonoom
  // for a part (from level 3) or a skill in a room (Expert), and confirms with yes or no. What
  // the catalogue or their own limit does not allow is grey, with the reason. Each control
  // keeps its own state, so a choice never lands on another row.
  import type { OwnStep } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    own: OwnStep;
    what: string;
    t: T;
    send: (message: Record<string, unknown>) => void;
  }
  let { own, what, t, send }: Props = $props();
  let choosing = $state(false);
  let picked = $state<{ step: string | null; name: string } | null>(null);
  const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
  const current = $derived(own.choices.find((c) => c.step === own.step)?.name ?? own.step);

  function yes(): void {
    send({ type: "admin_step", key: own.key, step: picked?.step ?? null });
    picked = null;
    choosing = false;
  }
</script>

<div class="own">
  {#if own.step}<p class="set">{t("ui.ready.own.set", { step: current ?? "" })}</p>{/if}
  {#if picked}
    <div class="confirm" role="group" aria-label={t("ui.ready.own.button")}>
      <p>{cap(picked.step ? t("ui.ready.own.confirm", { what, step: picked.name }) : t("ui.ready.own.confirm_remove", { what }))}</p>
      <div class="buttons">
        <button class="primary" onclick={yes}>{t("ui.yes")}</button>
        <button onclick={() => (picked = null)}>{t("ui.no")}</button>
      </div>
    </div>
  {:else if choosing}
    <div class="choices" role="group" aria-label={t("ui.ready.own.button")}>
      {#each own.choices as choice (choice.step)}
        <button class="chip {choice.step}" class:on={own.step === choice.step} disabled={!choice.allowed}
          onclick={() => (picked = { step: choice.step, name: choice.name })}>{choice.name}</button>
      {/each}
      {#if own.step}<button class="link" onclick={() => (picked = { step: null, name: "" })}>{t("ui.ready.own.remove")}</button>{/if}
      <button class="link" onclick={() => (choosing = false)}>{t("ui.ready.own.cancel")}</button>
    </div>
    {#each own.choices.filter((c) => c.why) as choice (choice.step)}
      <p class="why">{choice.name}: {choice.why}</p>
    {/each}
  {:else}
    <button class="link" onclick={() => (choosing = true)}>{t("ui.ready.own.button")}</button>
  {/if}
</div>

<style>
  .own { display: flex; flex-direction: column; gap: 4px; }
  .set, .why { margin: 0; font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .choices, .buttons { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .link { align-self: flex-start; border: none; background: none; color: var(--accent); font-weight: 600; cursor: pointer; min-height: 44px; padding: 0; }
  .chip { min-height: 44px; padding: 0 14px; border-radius: 22px; border: 1px solid var(--line); background: var(--surface); color: var(--text); cursor: pointer; }
  /* Each choice in its step's colour (StepPill): an edge, and filled when it is the one set. */
  .chip.observe { border-color: var(--step-observe); }
  .chip.propose { border-color: var(--step-propose); }
  .chip.autonomous { border-color: var(--step-autonomous); }
  .chip.on { font-weight: 600; }
  .chip.observe.on { background: var(--step-observe); color: var(--on-step-observe); }
  .chip.propose.on { background: var(--step-propose); color: var(--on-step-propose); }
  .chip.autonomous.on { background: var(--step-autonomous); color: var(--on-step-autonomous); }
  .chip:disabled { opacity: 0.45; cursor: not-allowed; }
  .confirm { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 12px; }
  .confirm p { margin: 0; }
  .confirm button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--surface); color: var(--text); cursor: pointer; }
  .confirm .primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
</style>

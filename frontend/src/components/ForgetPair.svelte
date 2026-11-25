<script lang="ts">
  // "Vergeet dit" (E-94, 20.13): HouseMind forgets what it learned about one skill in one room,
  // after a yes. Its own state, like OwnStep.
  import type { T } from "../lib/ui";

  interface Props {
    skill: string;
    area: string;
    what: string;
    t: T;
    send: (message: Record<string, unknown>) => void;
  }
  let { skill, area, what, t, send }: Props = $props();
  let asking = $state(false);
  const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
</script>

{#if asking}
  <div class="confirm" role="group" aria-label={t("ui.ready.forget.button")}>
    <p>{cap(t("ui.ready.forget.confirm", { what }))}</p>
    <div class="buttons">
      <button class="primary" onclick={() => { send({ type: "forget_pair", skill, area }); asking = false; }}>{t("ui.yes")}</button>
      <button onclick={() => (asking = false)}>{t("ui.no")}</button>
    </div>
  </div>
{:else}
  <button class="link" onclick={() => (asking = true)}>{t("ui.ready.forget.button")}</button>
{/if}

<style>
  .link { align-self: flex-start; border: none; background: none; color: var(--accent); font-weight: 600; cursor: pointer; min-height: 44px; padding: 0; }
  .buttons { display: flex; flex-wrap: wrap; gap: 8px; }
  .confirm { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 12px; }
  .confirm p { margin: 0; }
  .confirm button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--surface); color: var(--text); cursor: pointer; }
  .confirm .primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
</style>

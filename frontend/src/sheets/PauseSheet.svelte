<script lang="ts">
  // Pause the whole house or one domain (E-83). Emergencies keep working.
  import type { Snapshot } from "../lib/types";
  import type { T } from "../lib/ui";

  let { snap, t, onpause }: { snap: Snapshot; t: T; onpause: (minutes: number | null, domain: string | null) => void } = $props();
  let domain = $state<string | null>(null);
  const DOMAINS = ["lighting", "covers", "climate"];
</script>

<div class="group" role="group" aria-label={t("ui.pause_what")}>
  <button aria-pressed={domain === null} onclick={() => (domain = null)}>{t("ui.pause_house")}</button>
  {#each DOMAINS as d (d)}
    <button aria-pressed={domain === d} onclick={() => (domain = d)}>{t(`ui.domain.${d}`)}</button>
  {/each}
</div>
<div class="options">
  <!-- Each with when it ends (E-405): "1 uur · tot 22:00", "Tot morgen · tot 06:00". -->
  <button onclick={() => onpause(60, domain)}>{t("pause.one_hour")}{#if snap.paused.hour}{" "}<span class="until">· {t("pause.until_at", { at: snap.paused.hour })}</span>{/if}</button>
  <!-- Longer than an hour only for an administrator (QA 3, gat 3); the server holds to it too. -->
  {#if snap.user.isAdmin}
    <button onclick={() => onpause(-1, domain)}>{t("pause.until_tomorrow")}{#if snap.paused.morning}{" "}<span class="until">· {t("pause.until_at", { at: snap.paused.morning })}</span>{/if}</button>
    <button onclick={() => onpause(0, domain)}>{t("pause.until_resumed")}</button>
  {/if}
  {#if snap.paused.active || Object.keys(snap.paused.domains).length}
    <button class="resume" onclick={() => onpause(null, domain)}>{t("pause.resume")}</button>
  {/if}
</div>
<p class="muted">{t("pause.emergency_note")}{#if !snap.user.isAdmin}{" "}{t("pause.longer_note")}{/if}</p>

<style>
  .group { display: flex; flex-wrap: wrap; gap: 6px; }
  .group button { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--line); background: var(--raised); color: var(--muted); cursor: pointer; }
  .group button[aria-pressed="true"] { color: var(--text); border-color: var(--accent); }
  .options { display: flex; flex-direction: column; gap: 8px; }
  .options button { min-height: 52px; border-radius: 14px; border: 1px solid var(--line); background: var(--raised); text-align: left; padding: 0 16px; cursor: pointer; }
  .options .resume { border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .until { color: var(--muted); font-weight: 400; }
  .muted { color: var(--muted); margin: 0; }
</style>

<script lang="ts">
  // The safe mode after a failed update (E-301): Vesta in grey, what is wrong and what you can
  // do. Only the emergency reactions work; nothing else is switched, learned or kept.
  import Orb from "../lib/Orb.svelte";
  import type { T } from "../lib/ui";

  interface Props {
    reason: string; // failed | newer
    admin: boolean;
    t: T;
  }
  let { reason, admin, t }: Props = $props();
</script>

<main class="safe">
  <div class="orb"><Orb state="inactive" level={1} size={120} label={t("safe.title")} /></div>
  <h1>{t("safe.title")}</h1>
  <p>{t(`safe.${reason}`)}</p>
  <h2>{t("safe.what")}</h2>
  <ol>
    <li>{t("safe.backup")}</li>
    <li>{t("safe.share")}</li>
  </ol>
  {#if admin}
    <a class="button" href="api/diagnostics" download="housemind-diagnostics.json">{t("ui.settings.upkeep.diagnostics")}</a>
  {/if}
</main>

<style>
  .safe { max-width: 560px; margin: 0 auto; padding: 48px 16px; display: flex; flex-direction: column; gap: 12px; }
  .orb { display: flex; justify-content: center; }
  h1 { margin: 0; font-family: var(--font-display); font-size: calc(22px * var(--text-scale)); text-align: center; }
  h2 { margin: 8px 0 0; font-size: calc(16px * var(--text-scale)); }
  p, li { line-height: 1.5; }
  .button { align-self: flex-start; display: inline-flex; align-items: center; min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); color: inherit; text-decoration: none; }
</style>

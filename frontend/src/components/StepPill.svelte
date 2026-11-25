<script lang="ts">
  // A step as a small coloured label, the same everywhere in the app (wish from the real
  // house): Observeren calm, Voorstellen the learning colour, Autonoom the accent – filled like
  // the 'Vraag Vesta' button, only smaller. The colours are --step-* in app.css, so a colour
  // scheme changes them all at once. Alleen meten and Met rust gelaten are no step: an outline.
  interface Props {
    step: string;
    name: string;
    count?: number;
    held?: boolean; // the step HouseMind earned is held back by a limit
  }
  let { step, name, count, held = false }: Props = $props();
  const STEPS = ["observe", "propose", "autonomous"];
</script>

<span class="pill {STEPS.includes(step) ? step : 'other'}" class:held data-step={step}>{#if count !== undefined}<b>{count}</b> {/if}{name}</span>

<style>
  .pill { display: inline-flex; align-items: center; gap: 4px; padding: 2px 10px; border-radius: 11px; font-size: calc(12px * var(--text-scale)); font-weight: 600; line-height: 18px; white-space: nowrap; border: 1px solid transparent; }
  .pill b { font-family: var(--font-mono); font-weight: 600; }
  .observe { background: var(--step-observe); color: var(--on-step-observe); }
  .propose { background: var(--step-propose); color: var(--on-step-propose); }
  .autonomous { background: var(--step-autonomous); color: var(--on-step-autonomous); }
  .other { border-color: var(--edge); color: var(--muted); }
  .held { text-decoration: underline dotted; text-underline-offset: 3px; }
</style>

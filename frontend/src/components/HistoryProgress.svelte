<script lang="ts">
  // While HouseMind reads the history (E-257): Vesta's sentence and a bar with how far she is.
  // The house goes on meanwhile; with less motion the bar does not slide.
  import { historyLine, progressWidth, type HistoryView } from "../lib/history";
  import type { T } from "../lib/ui";

  interface Props {
    view: HistoryView | undefined;
    t: T;
  }
  let { view, t }: Props = $props();
</script>

{#if view?.state === "reading"}
  <div class="reading" role="status">
    <p>{historyLine(view, t)}</p>
    <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progressWidth(view)} aria-label={t("ui.history.title")}>
      <i style:width="{progressWidth(view)}%"></i>
    </div>
  </div>
{/if}

<style>
  .reading { display: flex; flex-direction: column; gap: 6px; }
  p { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .bar { height: 8px; border-radius: 4px; background: var(--line); overflow: hidden; }
  .bar i { display: block; height: 100%; background: var(--accent); transition: width 0.4s ease; }
  @media (prefers-reduced-motion: reduce) { .bar i { transition: none; } }
</style>

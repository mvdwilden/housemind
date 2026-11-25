<script lang="ts">
  // The day ring around Vesta on Ontzorgd (E-435, design 52): a dot for each time HouseMind did
  // something today, on a clock of 24 hours – midnight on top. No lines, no words: a tap on
  // Vesta still gives the one sentence.
  import { onRing } from "../lib/dayview";
  import type { T } from "../lib/ui";

  interface Props {
    dots: number[];
    size: number;
    t: T;
  }
  let { dots, size, t }: Props = $props();
  const c = $derived(size / 2);
  const r = $derived(size / 2 - 8);
</script>

<svg class="ring" width={size} height={size} viewBox="0 0 {size} {size}" role="img" aria-label={t("ui.flow.ring", {}, dots.length)}>
  <circle cx={c} cy={c} {r} class="track" />
  {#each dots as at, i (i)}
    {@const p = onRing(at, r, c)}
    <circle cx={p.x} cy={p.y} r="5" class="dot" />
  {/each}
</svg>

<style>
  .ring { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); pointer-events: none; }
  .track { fill: none; stroke: var(--line); stroke-width: 2; }
  .dot { fill: var(--accent); }
</style>

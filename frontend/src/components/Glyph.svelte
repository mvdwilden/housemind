<script lang="ts">
  // The shape that goes with each state, so status is never colour alone (E-46). On a light
  // background in the state's deep colour: the bright one is under 3:1 there (M-23).
  import type { OrbShape } from "../lib/orb";

  let { shape, color, deep = color, size = 14 }: { shape: OrbShape; color: string; deep?: string; size?: number } = $props();
</script>

<svg class="glyph" width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
  style:--bright={color} style:--deep={deep}>
  {#if shape === "circle"}<circle cx="8" cy="8" r="5.5" fill="currentColor" stroke="none" />
  {:else if shape === "dots"}<circle cx="3" cy="8" r="1.6" fill="currentColor" stroke="none" /><circle cx="8" cy="8" r="1.6" fill="currentColor" stroke="none" /><circle cx="13" cy="8" r="1.6" fill="currentColor" stroke="none" />
  {:else if shape === "plus"}<path d="M8 2.5v11M2.5 8h11" />
  {:else if shape === "triangle"}<path d="M8 2.5 14 13.5H2z" />
  {:else if shape === "bars"}<path d="M5.5 3v10M10.5 3v10" />
  {:else if shape === "shield"}<path d="M8 1.8 13.2 3.8v4.1c0 3-2.2 5.3-5.2 6.3-3-1-5.2-3.3-5.2-6.3V3.8z" />
  {:else if shape === "crescent"}<path d="M11.5 12.5A6 6 0 1 1 9 2.6a4.5 4.5 0 0 0 2.5 9.9z" fill="currentColor" stroke="none" />
  {:else}<circle cx="8" cy="8" r="6" /><circle cx="8" cy="8" r="2.8" />{/if}
</svg>

<style>
  .glyph { color: var(--bright); flex: none; }
  :global(:root[data-theme="light"]) .glyph { color: var(--deep); }
  @media (prefers-color-scheme: light) {
    :global(:root:not([data-theme="dark"])) .glyph { color: var(--deep); }
  }
</style>

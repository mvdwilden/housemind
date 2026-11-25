<script lang="ts">
  // Vesta's colours explained (E-404, B-330): a tap on her word shows the seven states – colour,
  // shape and word – from the same list as the Orb itself, and which one goes first.
  import Glyph from "./Glyph.svelte";
  import { ORB_LOOKS, type OrbState } from "../lib/orb";
  import type { T } from "../lib/ui";

  interface Props {
    t: T;
    now: string; // the state she shows now
  }
  let { t, now }: Props = $props();
  const SHOWN: OrbState[] = ["calm", "thinking", "learning", "attention", "paused", "sleeping", "secured", "emergency"];
</script>

<p class="muted">{t("ui.orb_legend.intro")}</p>
<ul class="legend">
  {#each SHOWN as state (state)}
    {@const look = ORB_LOOKS[state]}
    <li class:now={state === now}>
      <Glyph shape={look.shape} color={look.color} deep={look.deep} size={22} />
      <div>
        <p class="word">{t(look.textKey)}{#if state === now}<span class="here">{t("ui.orb_legend.now")}</span>{/if}</p>
        <p class="what">{t(`ui.orb_legend.${state}`)}</p>
      </div>
    </li>
  {/each}
</ul>
<p class="muted">{t("ui.orb_legend.first")}</p>

<style>
  .legend { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
  .legend li { display: flex; gap: 12px; align-items: flex-start; }
  .legend li.now .word { color: var(--accent); }
  .word { margin: 0; font-weight: 600; display: flex; gap: 8px; align-items: baseline; }
  .here { font-size: calc(12px * var(--text-scale)); font-weight: 500; color: var(--muted); }
  .what { margin: 2px 0 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .muted { margin: 0 0 12px; color: var(--muted); }
  .muted:last-child { margin: 12px 0 0; }
</style>

<script lang="ts">
  import FloorPlan from "./FloorPlan.svelte";
  import Network from "./Network.svelte";
  import RoomCards from "./RoomCards.svelte";
  import type { Floor, LivePath, ProposalView, Room } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    view: "cards" | "plan" | "network";
    rooms: Room[];
    floors: Floor[];
    t: T;
    onopen: (room: Room) => void;
    onview: (view: "cards" | "plan" | "network") => void;
    live?: LivePath | null; // the newest decision as a path in the network (E-439)
    waiting?: ProposalView | null; // the question still open, as a dotted path with its answers
    orb?: string; // the state Vesta shows now: the network has her in the middle
    level?: number;
    ai?: boolean; // AI is on: its ring around her in the network
    onanswer?: (proposal: number, answer: string) => void; // the waiting question's answers
  }
  let { view, rooms, floors, t, onopen, onview, live = null, waiting = null, orb = "calm", level = 4, ai = false, onanswer }: Props = $props();
  const VIEWS = ["cards", "plan", "network"] as const;
</script>

<section class="house">
  <div class="head">
    <h2>{t("ui.rooms")}</h2>
    <div class="switch" role="group" aria-label={t("ui.other_view")}>
      {#each VIEWS as v (v)}
        <button aria-pressed={view === v} onclick={() => onview(v)}>{t(`ui.view.${v}`)}</button>
      {/each}
    </div>
  </div>
  {#if view === "plan"}
    <FloorPlan {floors} {rooms} {t} {onopen} />
  {:else if view === "network"}
    <Network {rooms} {t} {onopen} {live} {waiting} {orb} {level} {ai} {onanswer} />
  {:else}
    <RoomCards {rooms} {t} {onopen} />
  {/if}
</section>

<style>
  .house { display: flex; flex-direction: column; gap: 10px; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  .switch { display: flex; background: var(--surface); border: 1px solid var(--line); border-radius: 18px; padding: 2px; }
  .switch button { min-height: 36px; padding: 0 12px; border: none; border-radius: 16px; background: none; color: var(--muted); cursor: pointer; font-size: calc(14px * var(--text-scale)); }
  .switch button[aria-pressed="true"] { background: var(--raised); color: var(--text); font-weight: 600; }
</style>

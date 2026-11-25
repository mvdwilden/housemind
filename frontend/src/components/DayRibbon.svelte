<script lang="ts">
  // Today per room on Huis (E-436, design 52): a ribbon of the day for the busiest rooms, with
  // a mark for each thing HouseMind did, asked or left – each kind its own shape, never colour
  // alone.
  import type { T } from "../lib/ui";

  interface Props {
    rooms: { room: string; marks: { at: number; outcome: string }[] }[];
    t: T;
  }
  let { rooms, t }: Props = $props();
  const kind = (outcome: string) => (outcome === "executed" ? "done" : outcome === "proposed" ? "asked" : "left");
</script>

<section class="card" aria-label={t("ui.flow.today")}>
  <h2>{t("ui.flow.today")}</h2>
  <div class="grid">
    {#each rooms as row (row.room)}
      <span class="room">{row.room}</span>
      <span class="track">
        {#each row.marks as mark, i (i)}<span class="mark {kind(mark.outcome)}" style:left="{mark.at * 100}%"></span>{/each}
      </span>
    {/each}
    <span></span>
    <span class="hours mono" aria-hidden="true"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></span>
  </div>
  <p class="legend">
    <span><span class="mark done"></span>{t("ui.flow.legend_done")}</span>
    <span><span class="mark asked"></span>{t("ui.flow.legend_asked")}</span>
    <span><span class="mark left"></span>{t("ui.flow.legend_left")}</span>
  </p>
</section>

<style>
  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 18px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  .grid { display: grid; grid-template-columns: minmax(70px, max-content) minmax(0, 1fr); column-gap: 12px; row-gap: 10px; align-items: center; font-size: calc(13px * var(--text-scale)); }
  .room { color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 140px; }
  .track { position: relative; height: 14px; border-bottom: 1px solid var(--line); }
  .track .mark { position: absolute; top: 2px; transform: translateX(-50%); }
  .mark { display: inline-block; width: 10px; height: 10px; border-radius: 5px; box-sizing: border-box; }
  .mark.done { background: var(--accent); }
  .mark.asked { border: 2px solid var(--learning); background: var(--surface); }
  .mark.left { background: var(--edge); border-radius: 2px; height: 4px; margin-top: 3px; }
  .hours { display: flex; justify-content: space-between; color: var(--muted); font-size: calc(11px * var(--text-scale)); }
  .legend { margin: 0; display: flex; flex-wrap: wrap; gap: 14px; color: var(--muted); font-size: calc(12px * var(--text-scale)); }
  .legend > span { display: inline-flex; align-items: center; gap: 6px; }
</style>

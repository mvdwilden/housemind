<script lang="ts">
  // The rooms as cards: the icon of the room (green with someone in it), its name and summary,
  // and small what is on now – lamps with their brightness first, at most three (E-510).
  import type { Room } from "../lib/types";
  import type { T } from "../lib/ui";
  import DeviceIcon from "./DeviceIcon.svelte";

  let { rooms, t, onopen }: { rooms: Room[]; t: T; onopen: (room: Room) => void } = $props();
  const MOST = 3;
</script>

<ul class="cards">
  {#each rooms as room (room.id)}
    {@const on = room.active ?? []}
    <li>
      <button class="room" onclick={() => onopen(room)}>
        <span class="tile" class:on={room.occupied} title={t(room.occupied ? "ui.room.occupied" : "ui.room.empty")}>
          <DeviceIcon name={room.icon ?? "room"} size={22} />
        </span>
        <span class="words">
          <strong>{room.name}</strong>
          <span>{room.summary}</span>
        </span>
        {#if on.length}
          <span class="active" aria-hidden="true">
            {#each on.slice(0, MOST) as item, i (i)}
              <span class="item" class:lamp={item.icon === "lamp"} title={item.name}>
                <DeviceIcon name={item.icon ?? "device"} size={18} pct={item.pct} level={item.level} closed={item.closed} />
                {#if item.icon === "lamp" && item.pct != null}<span class="pct">{item.pct}%</span>{/if}
              </span>
            {/each}
            {#if on.length > MOST}<span class="more">+{on.length - MOST}</span>{/if}
          </span>
        {/if}
      </button>
    </li>
  {/each}
</ul>

<style>
  .cards { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
  @media (min-width: 700px) { .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  .room { width: 100%; display: flex; gap: 12px; align-items: center; text-align: left; padding: 10px 14px; border-radius: 16px; background: var(--surface); border: 1px solid var(--line); cursor: pointer; min-height: 56px; }
  .tile { width: 38px; height: 38px; border-radius: 12px; background: var(--raised); border: 1px solid var(--line); display: inline-flex; align-items: center; justify-content: center; flex: none; color: var(--muted); }
  .tile.on { border-color: var(--accent); color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .words { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .words span { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .active { margin-left: auto; display: flex; gap: 8px; align-items: center; flex: none; color: var(--accent); }
  .item { display: inline-flex; align-items: center; gap: 3px; }
  .item.lamp { color: var(--learning); }
  .pct, .more { font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); color: var(--muted); }
</style>

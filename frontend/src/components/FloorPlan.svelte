<script lang="ts">
  // A schematic floor plan per floor (HA has floors and areas, no drawings).
  import type { Floor, Room } from "../lib/types";
  import type { T } from "../lib/ui";
  import DeviceIcon from "./DeviceIcon.svelte";

  let { floors, rooms, t, onopen }: { floors: Floor[]; rooms: Room[]; t: T; onopen: (room: Room) => void } = $props();
  const byId = $derived(new Map(rooms.map((r) => [r.id, r])));
</script>

<div class="plan">
  {#each floors as floor (floor.id)}
    <section>
      {#if floor.name}<h3>{floor.name}</h3>{/if}
      <div class="grid">
        {#each floor.rooms as id (id)}
          {@const room = byId.get(id)}
          {#if room}
            <button class="tile" class:occupied={room.occupied} class:lit={room.lightsOn > 0} onclick={() => onopen(room)}
              aria-label="{room.name}: {room.summary}">
              <strong>{room.name}</strong>
              <span class="info">
                <!-- A lamp per lamp that is on, with how bright (E-510). -->
                {#each (room.active ?? []).filter((a) => a.icon === "lamp").slice(0, 3) as lamp, i (i)}
                  <span class="lamp" title={lamp.name}><DeviceIcon name="lamp" size={16} pct={lamp.pct} />{#if lamp.pct != null}<span class="mono">{lamp.pct}%</span>{/if}</span>
                {/each}
                {#if room.lightsOn && !(room.active ?? []).some((a) => a.icon === "lamp")}<span class="lamp" role="img" aria-label={t("ui.room.lights_on")}><DeviceIcon name="lamp" size={16} pct={100} /></span>{/if}
                {#if room.temperature !== null}<span class="mono">{room.temperature.toFixed(1)}°</span>{/if}
              </span>
              <span class="corner" aria-hidden="true"><DeviceIcon name={room.icon ?? "room"} size={18} /></span>
            </button>
          {/if}
        {/each}
      </div>
    </section>
  {/each}
</div>

<style>
  .plan { display: flex; flex-direction: column; gap: 14px; }
  h3 { margin: 0 0 6px; font-size: calc(13px * var(--text-scale)); color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 6px; padding: 6px; border-radius: 16px; border: 1px solid var(--line); background: var(--ink); }
  .tile { position: relative; min-height: 76px; border-radius: 10px; border: 1px solid var(--line); background: var(--surface); display: flex; flex-direction: column; justify-content: space-between; align-items: flex-start; padding: 8px 10px; text-align: left; cursor: pointer; gap: 4px; }
  .tile strong { font-size: calc(14px * var(--text-scale)); font-weight: 600; }
  .tile.occupied { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .tile.lit { background: linear-gradient(160deg, rgba(243, 182, 91, 0.18), var(--surface) 70%); }
  .info { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; color: var(--muted); font-size: calc(13px * var(--text-scale)); padding-right: 22px; }
  .lamp { display: inline-flex; align-items: center; gap: 2px; color: var(--learning); }
  .lamp .mono { color: var(--muted); }
  .corner { position: absolute; right: 8px; bottom: 8px; color: var(--muted); }
  .mono { font-family: var(--font-mono); }
</style>

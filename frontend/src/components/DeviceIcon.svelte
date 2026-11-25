<script lang="ts">
  // One icon of the set (E-508, B-434): a device, a room or a part of the day, with what it
  // shows of the state – a lamp's brightness (its fill, and from 28 px its number), a
  // battery's level, how far a shutter is closed. Always next to a word: hidden from a screen
  // reader, the word says it.
  import { DEVICE_PATHS, LAMP_GLASS, NUMBER_FROM, batteryBars, lampFillTop, shutterPath } from "../lib/deviceicons";

  interface Props {
    name: string;
    size?: number;
    pct?: number | null; // a lamp's brightness
    level?: number | null; // a battery's level
    closed?: number | null; // how far a shutter is closed
  }
  let { name, size = 20, pct = null, level = null, closed = null }: Props = $props();
  const uid = `lamp-${Math.random().toString(36).slice(2, 9)}`;
  const path = $derived(name === "shutter" && closed != null ? shutterPath(closed) : (DEVICE_PATHS[name] ?? DEVICE_PATHS.device));
  const lamp = $derived(name === "lamp" && pct != null);
  const top = $derived(lamp ? lampFillTop(pct ?? 0) : 0);
  const number = $derived(lamp && size >= NUMBER_FROM ? String(Math.round(pct ?? 0)) : "");
  const bars = $derived(name === "battery" && level != null ? batteryBars(level) : []);
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="device-icon">
  <path d={path} />
  {#if lamp}
    <clipPath id={uid}><path d={LAMP_GLASS} /></clipPath>
    <rect x="4" y={top} width="16" height={17.2 - top} fill="currentColor" stroke="none" opacity="0.3" clip-path="url(#{uid})" />
    {#if number}
      <text x="12" y={10.4 + (number.length >= 3 ? 5.2 : 7) / 3} text-anchor="middle" font-size={number.length >= 3 ? 5.2 : 7}
        font-weight="700" fill="currentColor" stroke="none">{number}</text>
    {/if}
  {/if}
  {#each bars as x (x)}<path d="M{x} 10.3v3.4" stroke-width="2.1" />{/each}
</svg>

<style>
  .device-icon { flex: none; display: block; }
  text { font-family: var(--font-body); }
</style>

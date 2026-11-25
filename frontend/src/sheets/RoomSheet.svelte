<script lang="ts">
  // One room: its devices; tap one to read what HouseMind does with it, and why (E-93).
  import AskFirst from "../components/AskFirst.svelte";
  import DeviceIcon from "../components/DeviceIcon.svelte";
  import { tone } from "../lib/deviceicons";
  import type { Device, DeviceGroup, Room } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    room: Room;
    t: T;
    level: number;
    onwhynot: (entity: string) => void;
    why: Record<string, string>;
    send: (message: Record<string, unknown>) => void;
  }
  let { room, t, level, onwhynot, why, send }: Props = $props();
  let opened = $state<string | null>(null);
  // Per device (E-426): its name and type, its entities, its settings folded away.
  let groups = $derived<DeviceGroup[]>(
    room.groups
      ? room.groups.map((g) => ({ ...g, items: g.items.map((id) => room.devices.find((d) => d.id === id)).filter((d) => d !== undefined) }))
      : [{ id: "", name: "", model: "", items: room.devices }],
  );
  function explain(id: string) {
    opened = opened === id ? null : id;
    if (opened) onwhynot(id); // always fresh: what HouseMind thinks now
  }
  let name = $state("");
  function save(event: SubmitEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    send({ type: "scene_save", area: room.id, name: name.trim() });
    name = "";
  }
</script>

<p class="summary">{room.summary}</p>
{#if room.temperature === null && room.role !== "outside"}
  <!-- A room without a temperature (E-410): the answer where the question comes up. -->
  <p class="tip">{t("ui.room.no_temperature")}</p>
{/if}
{#if room.scenes?.length || level >= 3}
  <section class="scenes" aria-label={t("ui.scenes.title")}>
    <h3>{t("ui.scenes.title")}</h3>
    <div class="chips">
      {#each room.scenes ?? [] as scene (scene.id)}
        <span class="scene">
          <!-- A moment is learned and remembered here, not switched on: that is Home Assistant's
               (QA 3, schermen 28). -->
          <span class="chip">{scene.name}</span>
          {#if level >= 3 && scene.source === "person"}
            <AskFirst text="×" label={`${t("ui.scenes.remove")}: ${scene.name}`} question={t("ui.scenes.remove_confirm", { name: scene.name })} {t}
              onyes={() => send({ type: "scene_remove", id: scene.id })} />
          {/if}
        </span>
      {/each}
    </div>
    {#if level >= 3}
      <form onsubmit={save}>
        <input bind:value={name} maxlength="40" placeholder={t("ui.scenes.name")} aria-label={t("ui.scenes.name")} />
        <button type="submit">{t("ui.scenes.save")}</button>
      </form>
      <p class="muted">{t("ui.scenes.hint")}</p>
    {/if}
  </section>
{/if}
{#if room.zones?.length}
  <section class="zones" aria-label={t("ui.room.zones_title")}>
    <h3>{t("ui.room.zones_title")}</h3>
    <ul>
      {#each room.zones as zone (zone.id)}
        <li class="zone">
          <span class="dot" class:on={zone.on}></span>
          <span class="name">{zone.name}{#if zone.note}<small>{zone.note}</small>{/if}</span>
          <span class="state">{zone.state}</span>
        </li>
      {/each}
    </ul>
  </section>
{/if}
{#if room.devices.length}<p class="muted">{t("ui.room.tap_hint")}</p>{/if}
{#snippet row(device: Device)}
  <li>
    <button class="row" aria-expanded={opened === device.id} onclick={() => explain(device.id)}>
      <!-- Its icon instead of a dot (E-510): warm for a lamp that is on, green for the rest. -->
      <span class="icon {tone(device.icon ?? '', device.on && !device.kind)}"><DeviceIcon name={device.icon ?? "device"} size={20} pct={device.pct} level={device.level} closed={device.closed} /></span>
      <span class="name">{device.name}{#if device.function}<small class="fn">{device.function}{#if device.fromAi} <span class="from-ai">{t("ui.knowledge.from_ai")}</span>{/if}</small>{/if}</span>
      <span class="state">{device.state}</span>
      <span class="chev" aria-hidden="true">{opened === device.id ? "▴" : "▾"}</span>
    </button>
    {#if opened === device.id}
      <p class="why" role="status">{why[device.id] ?? "…"}</p>
    {/if}
  </li>
{/snippet}
{#each groups as group (group.id)}
  {@const own = group.items.filter((d) => d.kind !== "setting")}
  {@const settings = group.items.filter((d) => d.kind === "setting")}
  <section class="device" aria-label={group.name || t("ui.room.no_device")}>
    <!-- No heading when the device is just one thing with the same name (QA 3, schermen 18). -->
    {#if (group.name || groups.length > 1) && !(group.items.length === 1 && group.items[0].name === group.name)}
      <h4>{group.name || t("ui.room.no_device")}{#if group.model}<small>{group.model}</small>{/if}</h4>
    {/if}
    {#if own.length}<ul>{#each own as device (device.id)}{@render row(device)}{/each}</ul>{/if}
    {#if settings.length}
      <details>
        <summary>{t("ui.room.settings", {}, settings.length)}</summary>
        <ul>{#each settings as device (device.id)}{@render row(device)}{/each}</ul>
      </details>
    {/if}
  </section>
{/each}
{#if room.devices.length === 0}<p class="muted">{t("ui.room.no_devices")}</p>{/if}

<style>
  .summary { margin: 0; color: var(--muted); }
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  li { border-radius: 12px; background: var(--raised); }
  .row { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 48px; padding: 8px 12px; border: none; border-radius: 12px; background: transparent; color: inherit; text-align: left; cursor: pointer; font-size: calc(15px * var(--text-scale)); }
  .chev { color: var(--muted); font-size: calc(12px * var(--text-scale)); }
  .why { margin: 0; padding: 0 12px 12px 30px; color: var(--muted); font-size: calc(14px * var(--text-scale)); line-height: 1.5; }
  .dot { width: 8px; height: 8px; border-radius: 4px; background: var(--line); flex: none; }
  .dot.on { background: var(--learning); }
  .icon { display: inline-flex; flex: none; color: var(--muted); }
  .icon.lamp { color: var(--learning); }
  .icon.on { color: var(--accent); }
  .name { flex: 1; min-width: 0; }
  .state { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  button { min-height: 40px; padding: 0 12px; border-radius: 20px; border: 1px solid var(--line); background: var(--surface); cursor: pointer; font-size: calc(14px * var(--text-scale)); }
  .muted { color: var(--muted); margin: 0; }
  .device { display: flex; flex-direction: column; gap: 6px; }
  .device h4 { margin: 6px 0 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; font-size: calc(15px * var(--text-scale)); }
  .device h4 small { color: var(--muted); font-weight: 400; font-size: calc(13px * var(--text-scale)); }
  .fn { display: block; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  details summary { cursor: pointer; padding: 6px 2px; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  details ul { margin-top: 6px; }
  .scenes, .zones { display: flex; flex-direction: column; gap: 8px; }
  .zones h3 { margin: 4px 0 0; font-size: calc(15px * var(--text-scale)); }
  .zone { display: flex; align-items: center; gap: 10px; min-height: 48px; padding: 8px 12px; font-size: calc(15px * var(--text-scale)); }
  .zone .name { display: flex; flex-direction: column; gap: 2px; }
  .zone small { color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  .scenes h3 { margin: 4px 0 0; font-size: calc(15px * var(--text-scale)); }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .scene { display: inline-flex; align-items: center; gap: 2px; }
  .chip { display: inline-flex; align-items: center; min-height: 40px; padding: 0 12px; border-radius: 20px; border: 1px solid var(--line); background: var(--surface); font-size: calc(14px * var(--text-scale)); }
  form { display: flex; gap: 8px; }
  input { flex: 1; min-width: 0; min-height: 40px; padding: 0 12px; border-radius: 20px; border: 1px solid var(--edge); background: var(--surface); color: inherit; font: inherit; }
  .tip { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
</style>

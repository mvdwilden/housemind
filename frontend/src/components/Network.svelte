<script lang="ts">
  // The house as a network, as in the design 'Het huis in beweging' (E-439, design 52.6):
  // Vesta in the middle, breathing in her own colour with her word under her, the AI ring
  // around her when AI is on (apart from the core), the rooms as named blocks around that and
  // each room's devices as small dots. What HouseMind does flows as a line – its room, Vesta,
  // the device – only for the few seconds it happens; the line under the drawing stays as what
  // it did last, with 'Waarom?'. A question that waits for you stays a dotted line for as long
  // as it is open, with its two quick answers under the drawing. On a narrow screen the
  // drawing stands upright; a room is always a 44 px target (M-21). Less motion: all stands
  // still (app.css follows the device's own setting).
  import Glyph from "./Glyph.svelte";
  import DeviceIcon from "./DeviceIcon.svelte";
  import Orb from "../lib/Orb.svelte";
  import { orbLook } from "../lib/orb";
  import { onMount } from "svelte";
  import { ROOM_H, ROOM_ICON, curve, grouped, networkLayout, shownDevices, type Measure } from "../lib/network";
  import type { LivePath, ProposalView, Room } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    rooms: Room[];
    t: T;
    onopen: (room: Room) => void;
    live?: LivePath | null;
    waiting?: ProposalView | null; // the question still open: a dotted line with its answers
    orb?: string; // the state Vesta shows now, held as in the header
    level?: number;
    ai?: boolean; // AI is on: its ring around Vesta
    onanswer?: (proposal: number, answer: string) => void;
  }
  let { rooms, t, onopen, live = null, waiting = null, orb = "calm", level = 4, ai = false, onanswer }: Props = $props();
  let width = $state(640);
  let fonts = $state(0); // once the app's own letters are in, the names are measured again

  /** Text size Large: the blocks grow with their names. */
  function textScale(): number {
    if (typeof document === "undefined") return 1;
    return parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--text-scale")) || 1;
  }

  /** The names measured in the app's own letter, bold (the room on the path is bold). */
  function measurer(_fonts: number): Measure | undefined {
    const pen = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
    if (!pen) return undefined;
    return (text, size) => {
      pen.font = `600 ${size}px Figtree, system-ui, sans-serif`;
      return pen.measureText(text).width;
    };
  }

  onMount(() => {
    document.fonts?.ready.then(() => fonts++);
  });

  // The line flows for a short while after it happens: the milliseconds the house says are left.
  let flowing = $state(false);
  $effect(() => {
    const left = live?.flowFor ?? 0;
    flowing = left > 0;
    if (left <= 0) return;
    const stop = setTimeout(() => (flowing = false), left);
    return () => clearTimeout(stop);
  });

  // A group is one dot for its devices (E-515); a path to one of them ends at its group's dot.
  const shown = $derived(
    rooms.map((room) => {
      const { devices, standsFor } = grouped(room.devices);
      const dot = (id: string | null | undefined) => (id ? (standsFor.get(id) ?? id) : null);
      return {
        ...room,
        standsFor,
        devices: shownDevices(devices, [live?.area === room.id ? dot(live.entity) : null, waiting?.areaId === room.id ? dot(waiting.entity) : null]),
      };
    }),
  );
  /** The dot a device is drawn in: its own, or its group's. */
  function dotOf(area: string, entity: string): string {
    return shown.find((r) => r.id === area)?.standsFor.get(entity) ?? entity;
  }
  /** What the pointer shows on a dot (E-515): the device and how it is; a group says how many. */
  function tip(d: { name: string; state: string; members?: string[] }): string {
    return d.members?.length ? t("ui.network.group", { device: d.name, state: d.state }, d.members.length) : `${d.name}: ${d.state}`;
  }
  const layout = $derived(networkLayout(shown, width, textScale(), measurer(fonts)));
  const look = $derived(orbLook(orb, level));
  const word = $derived(t(look.textKey));
  /** A path: from its room into Vesta, and from her out to the device. */
  function route(area: string, entity: string) {
    const room = layout.rooms.find((r) => r.id === area);
    if (!room) return null;
    const brain = { x: layout.cx, y: layout.cy };
    const device = room.devices.find((d) => d.id === dotOf(area, entity)) ?? null;
    const name = rooms.find((r) => r.id === room.id)?.devices.find((d) => d.id === entity)?.name ?? "";
    const half = Math.min(name.length * 3.6, layout.width / 2 - 8);
    return {
      room,
      device,
      into: curve(room, brain, 1),
      out: device ? curve(brain, device, 1) : null,
      name,
      label: device ? { x: Math.min(Math.max(device.x, half + 8), layout.width - half - 8), y: room.below ? device.y + 26 : device.y - 16 } : null,
    };
  }
  const flow = $derived(flowing && live ? route(live.area, live.entity) : null);
  const ask = $derived(waiting?.areaId ? route(waiting.areaId, waiting.entity) : null);
  /** Which path a device is the end of: what it does now wins over what it asks. */
  function end(room: string, device: string): "flow" | "wait" | null {
    if (flow?.device && flow.room.id === room && live && device === dotOf(room, live.entity)) return "flow";
    if (ask?.device && ask.room.id === room && waiting?.entity && device === dotOf(room, waiting.entity)) return "wait";
    return null;
  }
</script>

<div class="net" class:upright={layout.upright} bind:clientWidth={width} role="group" aria-label={t("ui.view.network")}>
  <ul class="legend" aria-label={t("ui.network.legend")}>
    <li><svg width="28" height="8" viewBox="0 0 28 8" aria-hidden="true"><line x1="2" y1="4" x2="26" y2="4" class="key flows" /></svg>{t("ui.network.flows")}</li>
    <li><svg width="28" height="8" viewBox="0 0 28 8" aria-hidden="true"><line x1="2" y1="4" x2="26" y2="4" class="key waits" /></svg>{t("ui.network.waits")}</li>
    {#if ai}<li><svg width="28" height="8" viewBox="0 0 28 8" aria-hidden="true"><line x1="2" y1="4" x2="26" y2="4" class="key ai" /></svg>{t("ui.network.ai")}</li>{/if}
  </ul>

  <div class="stage" style:height="{layout.height}px">
    <svg width={layout.width} height={layout.height} viewBox="0 0 {layout.width} {layout.height}" aria-hidden="true">
      {#each layout.rooms as room (room.id)}
        <line x1={layout.cx} y1={layout.cy} x2={room.x} y2={room.y} class="link" />
        {#each room.devices as d (d.id)}<line x1={room.x} y1={room.edge} x2={d.x} y2={d.y} class="link thin" />{/each}
      {/each}
      {#if ai}<circle cx={layout.cx} cy={layout.cy} r={layout.ring} class="ring" />{/if}
      {#if ask}
        <path d={ask.into} class="flow wait" />
        {#if ask.out}<path d={ask.out} class="flow wait" />{/if}
      {/if}
      {#if flow}
        <path d={flow.into} class="flow" />
        {#if flow.out}<path d={flow.out} class="flow" />{/if}
      {/if}
      {#each shown as room, i (room.id)}
        {#each layout.rooms[i].devices as spot, j (spot.id)}
          {@const d = room.devices[j]}
          {@const at = end(room.id, spot.id)}
          <!-- The pointer on a dot, icon and all, says which device it is (E-515). -->
          <g class="spot" data-device={d.id}>
            <title>{tip(d)}</title>
            {#if at}
              <circle cx={spot.x} cy={spot.y} r="14" class="halo" class:wait={at === "wait"} />
              <circle cx={spot.x} cy={spot.y} r="6" class="device target" class:wait={at === "wait"} />
            {:else if layout.upright || !d.icon}
              <circle cx={spot.x} cy={spot.y} r="5" class="device" class:on={d.on} />
            {:else}
              <!-- On a computer each device shows what it is (E-510): its icon in its circle. -->
              <g class="dev" class:on={d.on && !d.kind} class:lamp={d.icon === "lamp" && d.on}>
                <circle cx={spot.x} cy={spot.y} r={layout.dot} class="device big" />
                <g transform="translate({spot.x - layout.dot + 2.5} {spot.y - layout.dot + 2.5})">
                  <DeviceIcon name={d.icon} size={layout.dot * 2 - 5} pct={d.pct} level={d.level} closed={d.closed} />
                </g>
              </g>
            {/if}
          </g>
        {/each}
      {/each}
      {#if ask?.label && !(flow?.label && flow.name === ask.name)}
        <text x={ask.label.x} y={ask.label.y} class="label wait" text-anchor="middle">{ask.name}</text>
      {/if}
      {#if flow?.label}
        <text x={flow.label.x} y={flow.label.y} class="label" text-anchor="middle">{flow.name}</text>
      {/if}
    </svg>

    <div class="brain" style:left="{layout.cx}px" style:top="{layout.cy}px">
      <Orb state={orb} {level} size={layout.orb} label={word} decorative />
    </div>
    <p class="word" style:left="{layout.cx}px" style:top="{layout.cy + layout.orb / 2 + 8}px" style:max-width="{layout.ring * 2 - 16}px">
      <Glyph shape={look.shape} color={look.color} deep={look.deep} size={12} /> {word}
    </p>
    {#if ai}
      <span class="ai" aria-hidden="true" style:left="{layout.cx + layout.ring * 0.72 + 4}px" style:top="{layout.cy - layout.ring * 0.72 - 18}px">{t("ui.flow.lane_ai")}</span>
    {/if}

    {#each shown as room, i (room.id)}
      {@const spot = layout.rooms[i]}
      <button class="room" class:occupied={room.occupied} class:path={flow?.room.id === room.id || ask?.room.id === room.id}
        class:wait={ask?.room.id === room.id && flow?.room.id !== room.id}
        style:left="{spot.x - spot.w / 2}px" style:top="{spot.y - ROOM_H / 2}px" style:width="{spot.w}px" onclick={() => onopen(room)}>
        {#if room.occupied}<span class="here" aria-hidden="true"></span>{/if}
        {#if !layout.upright}<span class="room-icon" aria-hidden="true"><DeviceIcon name={room.icon ?? "room"} size={ROOM_ICON} /></span>{/if}
        <span class="name">{room.name}</span>
        {#if room.occupied}<span class="sr">, {t("ui.room.occupied")}</span>{/if}
      </button>
    {/each}
  </div>

  {#if waiting}
    {@const question = waiting.id}
    <div class="live asking" role="group" aria-labelledby="net-asking">
      <span class="mono" id="net-asking">{t("ui.network.asking")} · {waiting.at}</span>
      <span class="text">{waiting.question}</span>
      {#if onanswer}
        <span class="answers">
          <button class="primary" onclick={() => onanswer(question, "do")}>{t("notify.answer.do")}</button>
          <button onclick={() => onanswer(question, "not_now")}>{t("notify.answer.not_now")}</button>
        </span>
      {/if}
    </div>
  {/if}
  <!-- No "Laatst om …" line under it (QA 3, schermen 16): Wat ik bedacht, just above, says it. -->
</div>

<style>
  .net {
    position: relative;
    width: 100%;
    background: var(--ink);
    border: 1px solid var(--line);
    border-radius: 16px;
    overflow: hidden;
  }
  /* The AI's violet (--ai) is one colour for the whole app, in app.css (E-524). */

  .legend { list-style: none; margin: 0; padding: 12px 14px 0; display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px 16px; font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .legend li { display: flex; align-items: center; gap: 8px; }
  .key { stroke-width: 3; stroke-linecap: round; }
  .key.flows { stroke: var(--accent); }
  .key.waits { stroke: var(--learning); stroke-dasharray: 2 5; }
  .key.ai { stroke: var(--ai); stroke-width: 2; stroke-dasharray: 3 5; }

  .stage { position: relative; width: 100%; }
  .stage svg { position: absolute; left: 0; top: 0; display: block; }
  .link { stroke: var(--line); stroke-width: 1.5; }
  .link.thin { stroke-width: 1; opacity: 0.8; }
  .ring { fill: none; stroke: var(--ai); stroke-width: 2; stroke-dasharray: 3 7; opacity: 0.7; animation: flow 4s linear infinite; }
  .flow { fill: none; stroke: var(--accent); stroke-width: 3; stroke-linecap: round; stroke-dasharray: 6 4; animation: flow 1s linear infinite; }
  .flow.wait { stroke: var(--learning); stroke-dasharray: 2 6; animation-duration: 2.6s; }
  @keyframes flow { to { stroke-dashoffset: -20; } }
  .device { fill: var(--raised); stroke: var(--edge); stroke-width: 1.2; }
  .device.on { fill: var(--muted); stroke: var(--muted); }
  .device.big { fill: var(--surface); }
  .dev { color: var(--muted); }
  .dev.on { color: var(--accent); }
  .dev.on .device.big { stroke: var(--accent); }
  .dev.lamp { color: var(--learning); }
  .dev.lamp .device.big { stroke: var(--learning); }
  .room-icon { display: inline-flex; flex: none; color: var(--muted); }
  .room.occupied .room-icon { color: var(--accent); }
  .device.target { fill: var(--accent); stroke: var(--accent); }
  .device.target.wait { fill: var(--learning); stroke: var(--learning); }
  .halo { fill: var(--accent); opacity: 0.22; }
  .halo.wait { fill: var(--learning); }
  .label { fill: var(--accent); font-family: var(--font-body); font-size: calc(13px * var(--text-scale)); paint-order: stroke; stroke: var(--ink); stroke-width: 4px; stroke-linejoin: round; }
  .label.wait { fill: var(--learning); }

  .brain { position: absolute; transform: translate(-50%, -50%); line-height: 0; pointer-events: none; }
  .word { position: absolute; transform: translateX(-50%); margin: 0; padding: 1px 6px; border-radius: 8px; background: var(--ink); text-align: center; font-size: calc(13px * var(--text-scale)); line-height: 1.25; color: var(--text); pointer-events: none; }
  .word :global(.glyph) { vertical-align: -1px; }
  .ai { position: absolute; font-size: calc(12px * var(--text-scale)); color: var(--ai); pointer-events: none; white-space: nowrap; }

  .room {
    position: absolute;
    height: 44px; /* a room is a 44 px target (M-21) */
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 0 10px;
    border-radius: 14px;
    background: var(--surface);
    border: 1px solid var(--line);
    color: var(--text);
    font-family: var(--font-body);
    font-size: calc(15px * var(--text-scale));
    cursor: pointer;
  }
  .room .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  /* On a phone a longer name takes two lines. */
  .upright .room { padding: 0 8px; font-size: calc(14px * var(--text-scale)); line-height: 1.12; text-align: center; }
  .upright .room .name { white-space: normal; overflow-wrap: break-word; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; }
  /* Someone there: a dot in the corner, so it takes nothing from the name. */
  .room .here { position: absolute; left: 7px; top: 7px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
  .room.occupied { border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); }
  .room.path { border: 2px solid var(--accent); box-shadow: 0 0 20px color-mix(in srgb, var(--accent) 30%, transparent); font-weight: 600; }
  .room.path.wait { border-color: var(--learning); box-shadow: 0 0 20px color-mix(in srgb, var(--learning) 30%, transparent); }
  .room:hover { background: var(--raised); }
  .room:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

  .live {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 14px;
    margin: 0 12px 12px;
    padding: 10px 14px;
    border-radius: 16px;
    background: var(--surface);
    border: 1px solid var(--line);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
    font-size: calc(15px * var(--text-scale));
    color: var(--text);
  }
  .live .mono { font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .live .text { flex: 1 1 14em; min-width: 0; }
  .live.asking { border-left: 3px solid var(--learning); }
  .asking .mono { color: var(--learning); }
  .answers { display: flex; flex-wrap: wrap; gap: 8px; }
  .answers button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--raised); color: var(--text); font-weight: 500; cursor: pointer; }
  .answers .primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }

  @media (prefers-reduced-motion: reduce) {
    .ring, .flow { animation: none; }
  }
</style>

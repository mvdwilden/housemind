<script lang="ts">
  import { orbLook } from "./orb";

  interface Props {
    state: string;
    level: number;
    size?: number;
    label: string;
    ontap?: () => void;
    onhold?: () => void;
    speaking?: boolean; // Vesta's voice is playing: the glow moves with it (17.8)
    voice?: number; // how loud her voice is now (0 – 1): she grows and glows with it
    listening?: boolean; // the microphone is open: a ring beats around the Orb (17.8)
    decorative?: boolean; // only to see, no button: the network's Vesta beside the header's (E-439)
  }

  let { state, level, size = 180, label, ontap, onhold, speaking = false, listening = false, voice = 0, decorative = false }: Props = $props();

  const look = $derived(orbLook(state, level));
  let holdTimer: ReturnType<typeof setTimeout> | null = null;
  let held = false;

  function down(): void {
    held = false;
    holdTimer = setTimeout(() => {
      held = true;
      onhold?.();
    }, 600);
  }

  function up(): void {
    if (holdTimer) clearTimeout(holdTimer);
    if (!held) ontap?.();
  }
</script>

{#snippet inside()}
  <span class="glow" aria-hidden="true"></span>
  <span class="core" aria-hidden="true">
    <svg viewBox="0 0 100 100" style:opacity={look.network}>
      <g class="turn">
        <g stroke="#fff" stroke-opacity="0.85" stroke-width="0.9" fill="none">
          <path d="M27 42 L45 33 L65 40 L75 58 L58 71 L37 65 Z" />
          <path d="M45 33 L58 71 M27 42 L65 40 M37 65 L75 58" />
        </g>
        <g fill="#fff">
          <circle cx="27" cy="42" r="1.8" /><circle cx="45" cy="33" r="2" /><circle cx="65" cy="40" r="1.8" />
          <circle cx="75" cy="58" r="1.6" /><circle cx="58" cy="71" r="1.8" /><circle cx="37" cy="65" r="1.6" />
        </g>
      </g>
      <g class="turn back" stroke="#fff" stroke-opacity="0.5" stroke-width="0.7" fill="none">
        <path d="M50 36 L63 57 L37 57 Z M50 36 L50 50 L63 57 M50 50 L37 57" />
      </g>
    </svg>
  </span>
{/snippet}

{#if decorative}
  <span
    class="orb still"
    class:breathes={look.breathes}
    style:--size="{size}px"
    style:--orb-color={look.color}
    style:--orb-deep={look.deep}
    style:--orb-glow={look.glow}
    style:--breath={look.state === "thinking" ? "2.2s" : look.state === "sleeping" ? "5s" : "3.5s"}
    style:--turn={look.state === "thinking" ? "12s" : look.state === "sleeping" ? "60s" : "30s"}
    aria-hidden="true">{@render inside()}</span>
{:else}
  <button
    class="orb"
    class:breathes={look.breathes}
    class:speaking
    class:listening
    style:--size="{size}px"
    style:--orb-color={look.color}
    style:--orb-deep={look.deep}
    style:--orb-glow={look.glow}
    style:--breath={look.state === "thinking" ? "2.2s" : look.state === "sleeping" ? "5s" : "3.5s"}
    style:--turn={look.state === "thinking" ? "12s" : look.state === "sleeping" ? "60s" : "30s"}
    style:--voice={voice}
    aria-label={label}
    onpointerdown={down}
    onpointerup={up}
    onpointerleave={() => holdTimer && clearTimeout(holdTimer)}
    onkeydown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), ontap?.())}
  >{@render inside()}</button>
{/if}

<style>
  .orb {
    position: relative;
    width: calc(var(--size) * 1.45);
    height: calc(var(--size) * 1.45);
    border: none;
    padding: 0;
    background: transparent;
    cursor: pointer;
    border-radius: 50%;
    touch-action: manipulation;
    /* The colours flow into each other (E-217); registered in app.css so they can. */
    transition:
      --orb-color 1.5s ease,
      --orb-deep 1.5s ease,
      --orb-glow 1.5s ease;
  }
  .orb.still {
    display: inline-block;
    cursor: default;
    pointer-events: none;
  }
  .orb:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 4px;
  }
  .glow {
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: radial-gradient(circle, var(--orb-glow) 0%, transparent 62%);
    opacity: var(--vesta-glow, 1); /* a colour scheme may soften her glow; her colours stay */
  }
  .core {
    position: absolute;
    left: 50%;
    top: 50%;
    width: var(--size);
    height: var(--size);
    margin: calc(var(--size) / -2) 0 0 calc(var(--size) / -2);
    border-radius: 50%;
    background: radial-gradient(circle at 36% 30%, #fff 0%, var(--orb-color) 22%, var(--orb-deep) 68%, #070a12 100%);
    box-shadow: 0 0 0 1.5px var(--vesta-ring, transparent), 0 0 calc(var(--size) / 4) var(--orb-glow), inset 0 calc(var(--size) / -14) calc(var(--size) / 6) rgba(0, 0, 0, 0.45);
    overflow: hidden;
  }
  .core svg {
    width: 100%;
    height: 100%;
    transition: opacity 0.6s ease;
  }
  /* Breathing you can see (found in a real house: at 6 s and 3.5 % nobody noticed it). */
  .breathes .core {
    animation: breathe var(--breath, 3.5s) ease-in-out infinite;
  }
  .breathes .glow {
    animation: glow var(--breath, 3.5s) ease-in-out infinite;
  }
  /* The network inside turns slowly – the mind at work (asked for in a real house). */
  .breathes .turn {
    transform-box: view-box;
    transform-origin: 50px 50px;
    animation: turn var(--turn, 30s) linear infinite;
  }
  .breathes .turn.back {
    animation-direction: reverse;
    animation-duration: calc(var(--turn, 30s) * 1.6);
  }
  /* Vesta listens: a ring beats around the Orb, in its own colour. */
  .listening::after {
    content: "";
    position: absolute;
    left: 50%;
    top: 50%;
    width: var(--size);
    height: var(--size);
    margin: calc(var(--size) / -2) 0 0 calc(var(--size) / -2);
    border-radius: 50%;
    border: 2px solid var(--orb-color); /* her own colour (17.8) */
    animation: ring 1.2s ease-out infinite;
    pointer-events: none;
  }
  @keyframes ring {
    from { transform: scale(0.95); opacity: 0.9; }
    to { transform: scale(1.3); opacity: 0; }
  }
  /* Vesta speaks: she grows and glows with her voice, like the meter while she listens. */
  .speaking .glow {
    animation: none;
    opacity: calc(0.4 + var(--voice, 0) * 0.6);
    transform: scale(calc(0.95 + var(--voice, 0) * 0.3));
    transition: transform 70ms linear, opacity 70ms linear;
  }
  .speaking .core {
    animation: none;
    transform: scale(calc(1 + var(--voice, 0) * 0.07));
    transition: transform 70ms linear;
  }
  @keyframes turn {
    to { transform: rotate(360deg); }
  }
  @keyframes breathe {
    0%, 100% { transform: scale(0.93); }
    50% { transform: scale(1.07); }
  }
  @keyframes glow {
    0%, 100% { opacity: 0.35; transform: scale(0.92); }
    50% { opacity: 1; transform: scale(1.12); }
  }
  @media (prefers-reduced-motion: reduce) {
    .orb { transition: none; }
    .breathes .core, .breathes .glow, .breathes .turn, .speaking .glow { animation: none; }
    .speaking .core, .speaking .glow { transform: none; }
    .listening::after { animation: none; transform: scale(1.1); }
  }
</style>

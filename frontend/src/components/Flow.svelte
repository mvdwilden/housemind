<script lang="ts">
  // One decision as a stream (E-434, design 52): a line that flows is done, slow dots wait for
  // you, a line that stops was not done. Status never by colour alone: each step has its word,
  // the end its shape. Less motion stops the lines (app.css).
  import type { FlowKind, FlowStep } from "../lib/flow";

  interface Props {
    steps: FlowStep[];
    kind: FlowKind;
    direction?: "column" | "row";
  }
  let { steps, kind, direction = "column" }: Props = $props();
</script>

{#if direction === "row"}
  <div class="row {kind}">
    {#each steps as step, i (i)}
      {#if i > 0}
        <svg class="link" width="34" height="16" viewBox="0 0 34 16" aria-hidden="true">
          <line class="line" x1="2" y1="8" x2="26" y2="8" />
          <path class="head" d="M24 3l6 5-6 5" />
        </svg>
      {/if}
      <div class="box" class:last={i === steps.length - 1}>
        {#if step.label}<span class="label" class:mono={step.mono}>{step.label}</span>{/if}
        <span>{step.text}</span>
      </div>
    {/each}
  </div>
{:else}
  <ol class="column {kind}">
    {#each steps as step, i (i)}
      <li class:last={i === steps.length - 1}><span class="label">{step.label}</span> {step.text}</li>
    {/each}
  </ol>
{/if}

<style>
  .label { color: var(--muted); }
  .column { --rail: var(--accent); list-style: none; margin: 4px 0 0; padding: 0; position: relative; display: flex; flex-direction: column; gap: 12px; font-size: calc(14px * var(--text-scale)); line-height: 1.45; }
  .column::before { content: ""; position: absolute; left: 4px; top: 12px; bottom: 12px; width: 3px; background: repeating-linear-gradient(to bottom, var(--rail) 0 6px, transparent 6px 10px); animation: rail 1.1s linear infinite; }
  .column.wait::before { background: repeating-linear-gradient(to bottom, var(--learning) 0 3px, transparent 3px 7px); animation-duration: 2.6s; }
  .column.stop::before { background: var(--line); animation: none; }
  .column li { position: relative; padding-left: 24px; }
  .column li::before { content: ""; position: absolute; left: 0; top: 0.35em; width: 11px; height: 11px; border-radius: 6px; background: var(--muted); }
  .done li.last::before { background: var(--accent); box-shadow: 0 0 10px var(--accent); }
  .wait li.last::before { background: var(--surface); border: 2px solid var(--learning); }
  .stop li.last::before { top: 0.6em; width: 14px; height: 3px; border-radius: 2px; background: var(--muted); }
  .wait li.last, .done li.last { font-weight: 600; }
  @keyframes rail { to { background-position: 0 10px; } }
  .wait::before { animation-name: rail-wait; }
  @keyframes rail-wait { to { background-position: 0 7px; } }

  .row { display: flex; flex-wrap: wrap; row-gap: 8px; align-items: stretch; font-size: calc(14px * var(--text-scale)); line-height: 1.4; }
  .box { flex: 1 1 120px; min-width: 0; display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; border-radius: 14px; background: var(--raised); border: 1px solid transparent; }
  .box .label { font-size: calc(12px * var(--text-scale)); }
  .box .label.mono { font-family: var(--font-mono); }
  .done .box.last { border-color: var(--accent); }
  .wait .box.last { border: 1px dashed var(--learning); }
  .link { flex: none; align-self: center; }
  .line { stroke: var(--accent); stroke-width: 2.5; stroke-linecap: round; stroke-dasharray: 6 4; animation: flow 1.1s linear infinite; }
  .head { fill: none; stroke: var(--accent); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  .wait .line, .wait .head { stroke: var(--learning); }
  .wait .line { stroke-dasharray: 2 7; animation-duration: 2.6s; }
  .stop .line, .stop .head { stroke: var(--edge); }
  .stop .line { stroke-dasharray: none; animation: none; }
  @keyframes flow { to { stroke-dashoffset: -20; } }
</style>

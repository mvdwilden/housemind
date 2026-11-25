<script lang="ts">
  // A proposal: question, reason with counts, and four answers (E-133, E-162). A stand for a
  // room says per lamp what it gets: its own percentage and colour, off, or as it is (E-499).
  import DeviceIcon from "./DeviceIcon.svelte";
  import { kelvinCss } from "../lib/stands";
  import type { ProposalView } from "../lib/types";
  import type { T } from "../lib/ui";

  // note: the line under Voortaan zelf / Nooit zo, once under a stack, not under each card (QA 3).
  let { proposal, t, onanswer, note = true }: { proposal: ProposalView; t: T; onanswer: (answer: string) => void; note?: boolean } = $props();
</script>

<article class="proposal" aria-label={proposal.question}>
  <p class="meta">{t("ui.proposal.label")} · {proposal.domain}{proposal.area ? ` · ${proposal.area}` : ""}</p>
  <!-- What it is about (E-511): its icon beside the question. -->
  <h3>{#if proposal.icon && !proposal.lamps?.length}<span class="dev-icon" aria-hidden="true"><DeviceIcon name={proposal.icon} size={22} pct={proposal.pct} level={proposal.level} closed={proposal.closed} /></span>{/if}{proposal.question}</h3>
  {#if proposal.reason}<p class="reason">{proposal.reason}</p>{/if}
  {#if proposal.lamps?.length}
    <ul class="lamps" aria-label={t("ui.proposal.lamps")}>
      {#each proposal.lamps as lamp, i (i)}
        <li>
          <!-- Each lamp as it will be (E-511): filled to its stand, plain when it goes off. -->
          <span class="lamp" class:plain={lamp.off} aria-hidden="true"><DeviceIcon name="lamp" size={20} pct={lamp.off ? null : (lamp.pct ?? (lamp.stays ? 100 : null))} /></span>
          <span class="name">{lamp.name}</span>
          <span class="what">
            {#if lamp.pct !== undefined}{lamp.pct}%{/if}
            {#if lamp.kelvin !== undefined}<span class="swatch" style="background: {kelvinCss(lamp.kelvin)}" aria-hidden="true"></span>{lamp.kelvin} K{/if}
            {#if lamp.colour}<span class="swatch" style="background: {lamp.colour}" aria-hidden="true"></span>{/if}
            {#if lamp.text}{lamp.pct !== undefined && !lamp.colour ? "· " : ""}{lamp.text}{/if}
          </span>
        </li>
      {/each}
    </ul>
  {/if}
  <div class="answers">
    <button class="primary" onclick={() => onanswer("do")}>{t("notify.answer.do")}</button>
    <button onclick={() => onanswer("not_now")}>{t("notify.answer.not_now")}</button>
    {#if proposal.canRule}
      <button onclick={() => onanswer("from_now")}>{t("notify.answer.from_now")}</button>
      <button onclick={() => onanswer("never")}>{t("notify.answer.never")}</button>
    {/if}
  </div>
  {#if proposal.canRule && note}<p class="note">{t("ui.proposal.rule_note")}</p>{/if}
</article>

<style>
  .dev-icon { display: inline-flex; vertical-align: -4px; margin-right: 8px; color: var(--muted); }
  .lamp { display: inline-flex; flex: none; color: var(--learning); }
  .lamp.plain { color: var(--muted); }
  .proposal { background: var(--surface); border: 1px solid var(--line); border-left: 3px solid var(--learning); border-radius: 18px; padding: 16px; display: flex; flex-direction: column; gap: 8px; }
  .meta { margin: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); text-transform: uppercase; letter-spacing: 0.04em; }
  h3 { margin: 0; font-family: var(--font-display); font-size: calc(18px * var(--text-scale)); font-weight: 600; }
  .reason { margin: 0; color: var(--muted); }
  .lamps { list-style: none; margin: 4px 0 0; padding: 10px 12px; border-radius: 12px; background: var(--raised); display: flex; flex-direction: column; gap: 6px; }
  .lamps li { display: flex; gap: 10px; align-items: center; min-width: 0; }
  .name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .what { margin-left: auto; display: inline-flex; gap: 6px; align-items: center; font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); color: var(--muted); white-space: nowrap; }
  .swatch { width: 12px; height: 12px; border-radius: 3px; border: 1px solid var(--edge); }
  .answers { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 4px; }
  button { min-height: 44px; border-radius: 22px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; font-weight: 500; }
  .primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
  .note { margin: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); }
</style>

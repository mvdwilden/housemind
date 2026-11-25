<script lang="ts">
  // Straks, in Brein › Ritme under the bars: the next moments of the day – it gets dark, the
  // house goes to sleep, getting up, it gets light – on a line of time, and per moment what
  // HouseMind will then do with what it may: do it, ask first, only watch, or not and why.
  // Worked out quietly by the server (brain/later.py): nothing is switched. The step is a word
  // in a chip, never only a colour (E-46).
  import type { Later } from "../lib/types";
  import type { T } from "../lib/ui";
  import Icon from "./Icon.svelte";
  import DeviceIcon from "./DeviceIcon.svelte";

  interface Props {
    later: Later | undefined;
    t: T;
    ontest?: () => void; // an administrator on Expert: open Test het huis
  }
  let { later, t, ontest }: Props = $props();

  const ICONS: Record<string, string> = { dark: "sunset", light: "sun", bed: "moon", wake: "sunrise" };
</script>

<section class="later" aria-labelledby="later-title" data-section="later">
  <div class="head">
    <h2 id="later-title">{t("later.title")}</h2>
    {#if later}<span class="mono muted small">{t("later.at", { at: later.at })}</span>{/if}
  </div>
  <p class="muted">{t("later.note")}</p>
  {#if later?.blocked}
    <p>{later.blocked}</p>
  {:else if later}
    {#if later.moments.length}
      <ol class="line">
        {#each later.moments as moment (moment.key)}
          <li class="moment">
            <span class="dot" aria-hidden="true"><Icon name={ICONS[moment.key] ?? "sunrise"} size={14} /></span>
            <p class="when"><span class="mono time">± {moment.at}</span> · <b>{moment.title}</b>{#if moment.how}<span class="muted">{` · ${moment.how}`}</span>{/if}</p>
            {#if moment.lines.length}
              <ul class="lines">
                {#each moment.lines as line, i (i)}
                  <li>
                    <span class="icon" aria-hidden="true"><DeviceIcon name={line.icon ?? "device"} size={20} pct={line.pct ?? null} /></span>
                    <span class="text">{line.text}</span>
                    <span class="chip {line.outcome}">{line.chip}</span>
                  </li>
                {/each}
              </ul>
            {:else if moment.nothing}
              <p class="muted small nothing">{moment.nothing}</p>
            {/if}
          </li>
        {/each}
      </ol>
    {:else}
      <p class="muted">{t("later.none")}</p>
    {/if}
    {#each later.unknown as note (note)}<p class="muted small">{note}</p>{/each}
    {#if ontest}
      <p class="small test">{t("later.test_before")} <button class="link" onclick={ontest}>{t("later.test_link")}</button>.</p>
    {/if}
  {/if}
</section>

<style>
  .later { display: flex; flex-direction: column; gap: 8px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line); }
  .head { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  p { margin: 0; line-height: 1.45; }
  .muted { color: var(--muted); }
  .small { font-size: calc(13px * var(--text-scale)); }
  .mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
  /* A line of time: a dot per moment, the lines of a moment beside it. */
  .line { list-style: none; margin: 6px 0 0; padding: 0 0 0 28px; position: relative; display: flex; flex-direction: column; gap: 14px; }
  .line::before { content: ""; position: absolute; left: 10px; top: 8px; bottom: 8px; width: 2px; background: var(--line); }
  .moment { position: relative; display: flex; flex-direction: column; gap: 6px; }
  .dot { position: absolute; left: -28px; top: 0; width: 22px; height: 22px; border-radius: 50%; background: var(--raised); border: 2px solid var(--accent); color: var(--accent); display: grid; place-items: center; }
  .time { color: var(--accent); }
  .lines { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .lines li { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px; border-radius: 12px; background: var(--raised); }
  .icon { display: inline-flex; color: var(--muted); flex: none; margin-top: 1px; }
  .text { flex: 1; min-width: 0; }
  .chip { flex: none; max-width: 45%; font-size: calc(12px * var(--text-scale)); font-weight: 600; border-radius: 12px; padding: 2px 9px; border: 1px solid var(--edge); color: var(--muted); }
  .chip.executed { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
  .chip.proposed { border-color: var(--learning); color: var(--learning); }
  .chip.denied { border-style: dashed; }
  /* On a phone the chip goes under its line, so the words keep their width. */
  @media (max-width: 560px) {
    .lines li { flex-wrap: wrap; row-gap: 4px; }
    .text { flex-basis: calc(100% - 40px); }
    .chip { max-width: none; margin-left: 30px; }
  }
  .test { color: var(--muted); }
  .link { border: none; background: none; padding: 0; color: var(--accent); font: inherit; font-weight: 600; cursor: pointer; text-decoration: underline; min-height: 0; }
</style>

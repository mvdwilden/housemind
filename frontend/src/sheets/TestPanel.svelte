<script lang="ts">
  // Test het huis (E-474 – E-481, E-518, E-519, design 70, 85): for an administrator on Expert,
  // in the real house and in a demo house. Right of the main screen on a wide screen, like Speel
  // het huis; on a phone as its own page. Speel het na: the server lays the chosen situation over
  // the picture of now and says per line what HouseMind would do and how that goes for real;
  // nothing is switched until Doe het echt, which does it all in the house and puts it back after
  // a minute (E-518). While that runs, the play takes the place of the list. Loop zelf rond is
  // the testkwartier (E-477). What just happened in the room follows the house (E-478).
  import DeviceIcon from "../components/DeviceIcon.svelte";
  import type { TestView } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    view?: TestView;
    demo: boolean;
    t: T;
    send: (message: Record<string, unknown>) => void;
    onhelp: () => void;
    onclose?: () => void;
  }
  let { view, demo, t, send, onhelp, onclose }: Props = $props();

  function test(message: Record<string, unknown>): void {
    send({ type: "test", ...message });
  }
  const chosen = $derived((view?.situations ?? []).filter((s) => s.on).length);
  const willing = $derived((view?.lines ?? []).some((line) => line.will));
  const live = $derived((view?.plays ?? []).find((play) => play.running));
  const past = $derived((view?.plays ?? []).find((play) => !play.running));
  let minutes = $state(15);
</script>

<div class="test">
  <div class="top">
    <h2>{t("test.open")}</h2>
    {#if onclose}<button class="link" onclick={onclose}>{t("test.close")}</button>{/if}
  </div>
  <p class="intro">{t(demo ? "test.intro_demo" : "test.intro")} <button class="link" onclick={onhelp}>{t("test.how")}</button></p>

  {#if view}
    <label class="room">
      <span>{t("test.room")}</span>
      {#if view.rooms.find((r) => r.id === view.area)?.icon}<span class="ic" aria-hidden="true"><DeviceIcon name={view.rooms.find((r) => r.id === view.area)?.icon ?? "room"} size={20} /></span>{/if}
      <select value={view.area ?? ""} onchange={(e) => test({ do: "area", area: (e.currentTarget as HTMLSelectElement).value })}>
        {#each view.rooms as room (room.id)}<option value={room.id}>{room.name}</option>{/each}
      </select>
    </label>
    {#if view.now}<p class="now">{view.now}</p>{/if}

    {#if view.blocked}
      <p class="blocked" role="status">{view.blocked}</p>
    {:else}
      <section aria-labelledby="test-play">
        <h3 id="test-play">{t("test.play.title")}</h3>
        <p class="muted">{t("test.play.note")}</p>
        <div class="chips" role="group" aria-labelledby="test-play">
          {#each view.situations as s (s.key)}
            <button aria-pressed={s.on} class:implied={s.implied} title={s.implied ? t("test.play.implied") : undefined} onclick={() => test({ do: "pretend", key: s.key, on: !s.on })}>{t(`test.pretend.${s.key}`)}</button>
          {/each}
          {#if chosen}<button class="clear" onclick={() => test({ do: "clear" })}>{t("test.pretend.clear")}</button>{/if}
        </div>
        {#if live}
          {@render played(live)}
        {:else}
          <div class="result" aria-live="polite">
            {#if view.lines.length}
              <ul>
                {#each view.lines as line, i (i)}
                  <li class={line.kind} class:will={line.will}>
                    <span>{#if line.icon}<span class="ic" class:lamp={line.icon === "lamp" && line.pct != null} aria-hidden="true"><DeviceIcon name={line.icon} size={20} pct={line.pct} /></span>{/if}{line.text}</span>
                    <span class="real">{line.real}</span>
                    {#if line.skip}<span class="skip">{line.skip}</span>{/if}
                  </li>
                {/each}
              </ul>
              {#if willing}
                <button class="play" onclick={() => test({ do: "play" })}>{t("test.play.button")}</button>
                <p class="muted">{t("test.play.after")}</p>
              {/if}
            {:else if view.nothing}
              <p class="muted">{view.nothing}</p>
            {/if}
          </div>
          {#if past}{@render played(past)}{/if}
        {/if}
      </section>
    {/if}
    {#if !view.blocked}
      <section aria-labelledby="test-quarter">
        <h3 id="test-quarter">{t("test.quarter.title")}</h3>
        <p class="muted">{t("test.quarter.note")}</p>
        {#if view.quarter.parts.length}
          <div class="chips" role="group" aria-labelledby="test-quarter">
            {#each view.quarter.minutes as m (m)}
              <button aria-pressed={minutes === m} onclick={() => (minutes = m)}>{t("test.quarter.minutes", { count: m })}</button>
            {/each}
          </div>
          <ul class="parts">
            {#each view.quarter.parts as part (part.key)}
              <li>
                <span>{part.name}</span>
                {#if part.running}
                  <span class="muted">{t("test.quarter.running", { at: part.running.until })}</span>
                  <button onclick={() => test({ do: "stop", id: part.running?.id })}>{t("test.quarter.stop")}</button>
                {:else}
                  <button class="start" onclick={() => test({ do: "quarter", part: part.key, minutes })}>{t("test.quarter.start")}</button>
                {/if}
              </li>
            {/each}
          </ul>
        {:else}
          <p class="muted">{t("test.quarter.none")}</p>
        {/if}
      </section>
    {/if}

    <section class="happened" aria-labelledby="test-happened">
      <h3 id="test-happened">{t("test.happened.title")}</h3>
      {#if view.happened.length}
        <ul>
          {#each view.happened as line (line.id)}
            <li>
              <time>{line.at}</time>
              <span>
                {line.text}
                {#if line.check}<span class="said-no">{line.check}</span>{/if}
                <!-- What happened, not how fast (QA 3, schermen 5). -->
                {#if line.test}<span class="meta">{t("test.happened.test")}</span>{/if}
              </span>
            </li>
          {/each}
        </ul>
      {:else}
        <p class="muted">{t("test.happened.none")}</p>
      {/if}
    </section>
    {#if view.anyRunning}
      <button class="stop-all" onclick={() => test({ do: "stop_all" })}>{t("test.stop_all")}</button>
    {/if}
  {/if}
</div>

{#snippet played(play: TestView["plays"][number])}
  <div class="played" class:running={play.running} aria-live="polite">
    <div class="played-top">
      <span>{play.running ? t("test.play.running", { at: play.until }) : t("test.play.over")}</span>
      {#if play.running}
        <div class="chips">
          <button onclick={() => test({ do: "keep", id: play.id })}>{t("test.try.keep")}</button>
          <button onclick={() => test({ do: "back", id: play.id })}>{t("test.try.back")}</button>
        </div>
      {/if}
    </div>
    <ul>
      {#each play.lines as line, i (i)}
        <li class={line.state}>
          <span>{#if line.icon}<span class="ic" class:lamp={line.icon === "lamp" && line.pct != null} aria-hidden="true"><DeviceIcon name={line.icon} size={20} pct={line.pct} /></span>{/if}{line.text}</span>
          <span class="say">{line.say}</span>
        </li>
      {/each}
    </ul>
  </div>
{/snippet}

<style>
  .ic { display: inline-flex; vertical-align: -5px; margin-right: 8px; color: var(--muted); }
  .ic.lamp { color: var(--learning); }
  .test { display: flex; flex-direction: column; gap: 18px; }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(20px * var(--text-scale)); font-weight: 600; }
  h3 { margin: 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  section { display: flex; flex-direction: column; gap: 10px; }
  .intro { margin: 0; line-height: 1.45; }
  .intro .link { display: inline; min-height: 0; }
  .muted { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); line-height: 1.4; }
  .room { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .room span { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .room select { min-height: 44px; min-width: 0; flex: 1; max-width: 260px; padding: 0 12px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); font: inherit; }
  .now { margin: 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); }
  .blocked { margin: 0; padding: 10px 12px; border-radius: 12px; border: 1px solid var(--emergency); color: var(--emergency); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chips button { min-height: 44px; padding: 0 14px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); cursor: pointer; }
  .chips button[aria-pressed="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); font-weight: 600; }
  .chips .clear { color: var(--muted); }
  /* A part of the day brings its light (E-519): that button is half on. */
  .chips button.implied { border-style: dashed; border-color: var(--accent); color: var(--accent); }
  .result ul, .happened ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; }
  .result li { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: 12px; background: var(--raised); border: 1px solid var(--line); font-size: calc(14px * var(--text-scale)); line-height: 1.4; }
  .result li.will { border-color: var(--accent); }
  .real { color: var(--muted); }
  .result li.auto .real { color: var(--accent); }
  .result li.ask .real, .result li.watch .real, .result li.hand .real { color: var(--learning); }
  .skip { color: var(--emergency); }
  .play { display: block; width: 100%; margin-top: 10px; min-height: 48px; padding: 0 18px; border-radius: 24px; border: 1px solid var(--accent); background: var(--accent); color: var(--on-accent); font-weight: 600; font-size: calc(16px * var(--text-scale)); cursor: pointer; }
  .result .play + .muted { margin-top: 6px; text-align: center; }
  .played { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--raised); }
  .played.running { border-color: var(--accent); }
  .played-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; font-weight: 600; font-size: calc(14px * var(--text-scale)); }
  .played.running .played-top > span { color: var(--accent); }
  .played ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
  .played li { display: flex; flex-direction: column; gap: 2px; font-size: calc(14px * var(--text-scale)); line-height: 1.4; }
  .played .say { color: var(--muted); }
  .played li.skipped .say { color: var(--emergency); }
  .happened { padding: 12px 14px; border-radius: 14px; border: 1px solid var(--accent); background: var(--raised); }
  .happened li { display: flex; gap: 10px; font-size: calc(14px * var(--text-scale)); line-height: 1.4; }
  .happened li time { flex: none; font-family: var(--font-mono); color: var(--muted); }
  .said-no, .meta { display: block; color: var(--muted); }
  .parts { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
  .parts li { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-height: 44px; }
  .parts li > span:first-child { flex: 1; min-width: 0; }
  .parts button, .stop-all { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); cursor: pointer; }
  .parts .start { border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .stop-all { align-self: stretch; border-color: var(--accent); font-weight: 600; }
</style>

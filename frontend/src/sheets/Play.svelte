<script lang="ts">
  // 'Speel het huis' (E-321, chapter 25): only in a demo house, in a browser on a wide screen,
  // as a column right of the main screen (the administrator's wish). Play lets the time run at
  // the speed of the slider and locks every change; Stop holds the time, and then you set things
  // up: free controls built from what the house has, stories and jumps (B-187 – B-191). Every
  // tap goes to the server; the house answers like a real one.
  import StepPill from "../components/StepPill.svelte";
  import { byRoom, demoNow, demoTime, lineTime, percent, speedIndex, speedKey } from "../lib/play";
  import type { DemoLive, PlayView } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    view?: PlayView;
    live: DemoLive;
    language: string;
    t: T;
    send: (message: Record<string, unknown>) => void;
    onhelp: () => void;
    onreadiness: () => void;
    onlogbook?: () => void; // Logboek is a tab from level 2
  }
  let { view, live, language, t, send, onhelp, onreadiness, onlogbook }: Props = $props();

  // The house's time runs on between pictures, at its own speed.
  let receivedAt = $state(Date.now());
  let tick = $state(Date.now());
  $effect(() => {
    void live.now;
    receivedAt = Date.now();
  });
  $effect(() => {
    const timer = setInterval(() => (tick = Date.now()), 1000);
    return () => clearInterval(timer);
  });
  const houseNow = $derived(demoNow(live, receivedAt, tick));
  const clock = $derived(demoTime(houseNow, language));
  const busy = $derived(live.busy !== null);
  // While the house plays, nothing can be changed: Stop first (B-188). The speed may change.
  const locked = $derived(busy || live.running);
  const speeds = $derived(view?.speeds ?? [0.05, 0.1, 1, 10, 20]);
  const speedAt = $derived(speedIndex(speeds, live.speed));
  const pct = $derived(live.busy ? percent(live.busy.done) : 0);
  let by = $state<"person" | "wall">("person");

  function play(message: Record<string, unknown>): void {
    send({ type: "play", ...message });
  }

  function since(iso: string): string {
    return new Date(iso).toLocaleTimeString(language, { hour: "2-digit", minute: "2-digit" });
  }

  function lastName(last: NonNullable<PlayView["last"]>): string {
    if (last.kind === "story") return t(`play.story.${last.key}.title`);
    if (last.kind === "jump") return t(`play.jump.${last.key}`);
    return "";
  }

  function switchWords(kind: string): [string, string] {
    return kind === "cover" ? [t("play.free.open"), t("play.free.closed")] : [t("play.free.turned_on"), t("play.free.turned_off")];
  }
</script>

<div class="play">
  <h2>{t("play.open")}</h2>
  <p class="intro">{t("play.intro")} <button class="link" onclick={onhelp}>{t("play.how")}</button></p>

  <!-- Play and Stop, and how fast the time runs (the administrator's wish: one slider). -->
  <section class="run" class:running={live.running} aria-labelledby="play-state">
    <div class="run-row">
      {#if live.running || busy}
        <!-- Stop always works: it also halts a story or jump that goes on (the administrator's
             wish – on a slow machine a week ahead takes minutes). -->
        <button class="go stop" onclick={() => play({ do: "stop" })}>
          <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" /></svg>
          {t("play.stop")}
        </button>
      {:else}
        <button class="go" disabled={busy} onclick={() => play({ do: "play" })}>
          <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4v16l13-8z" fill="currentColor" /></svg>
          {t("play.run")}
        </button>
      {/if}
      <div class="state">
        <b id="play-state">{live.busy ? (live.busy.kind === "story" ? t(`play.story.${live.busy.what}.title`) : t(`play.jump.${live.busy.what}`))
          : t(live.running ? "play.state.running" : "play.state.stopped")}</b>
        <span class="clock">{clock}</span>
      </div>
    </div>
    <label class="speed">
      <span>{t("play.time.speed")}: <b>{t(speedKey(speeds[speedAt]))}</b></span>
      <input type="range" min="0" max={speeds.length - 1} step="1" value={speedAt} disabled={busy}
        aria-valuetext={t(speedKey(speeds[speedAt]))}
        onchange={(e) => play({ do: "speed", value: speeds[Number((e.currentTarget as HTMLInputElement).value)] })} />
      <span class="ticks" aria-hidden="true">
        {#each speeds as speed (speed)}<span>{t(speedKey(speed))}</span>{/each}
      </span>
    </label>
    {#if live.busy}
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={pct} aria-label={t("play.progress", { pct })}>
        <span style:width="{pct}%"></span>
      </div>
      <p class="muted" role="status">{t("play.progress", { pct })}</p>
    {:else}
      <p class="muted" role="status">{t(live.running ? "play.locked" : "play.stopped_note")}</p>
    {/if}
  </section>

  <section aria-labelledby="play-time">
    <h3 id="play-time">{t("play.time.title")}</h3>
    <div class="chips" role="group" aria-label={t("play.time.jump")}>
      {#each view?.jumps ?? [] as jump (jump)}
        <button disabled={locked} onclick={() => play({ do: "jump", to: jump })}>{t(`play.jump.${jump}`)}</button>
      {/each}
    </div>
    <p class="muted">{t("play.time.note")}</p>
    {#if view}
      <div class="steps">
        <span>{t("play.steps")}</span>
        <StepPill step="observe" name={t("steps.observe")} count={view.steps.observe} />
        <StepPill step="propose" name={t("steps.propose")} count={view.steps.propose} />
        <StepPill step="autonomous" name={t("steps.autonomous")} count={view.steps.autonomous} />
        <button class="link" onclick={onreadiness}>{t("ui.ready.title")}</button>
      </div>
      {#if !view.learning}<p class="muted">{t("play.learning_off")}</p>{/if}
    {/if}
  </section>

  {#if view?.last}
    <section class="happened" aria-labelledby="play-happened">
      <h3 id="play-happened">{t("play.happened.title")}</h3>
      <p class="muted">{[lastName(view.last), t("play.happened.since", { at: since(view.last.since) })].filter(Boolean).join(" · ")}</p>
      {#if view.happened.length}
        <ul aria-live="polite">
          {#each view.happened as line, i (i)}
            <li class:notice={line.type === "notice"} class:emergency={line.kind === "emergency"}><time>{lineTime(line, houseNow, language)}</time> <span>{line.text}</span></li>
          {/each}
        </ul>
      {:else}
        <p class="muted" aria-live="polite">{t("play.happened.none")}</p>
      {/if}
      {#if onlogbook}<button class="link" onclick={onlogbook}>{t("tabs.logbook")}</button>{/if}
    </section>
  {/if}

  {#if view}
    <section aria-labelledby="play-stories">
      <h3 id="play-stories">{t("play.stories.title")}</h3>
      <p class="muted">{t("play.stories.intro")}</p>
      <div class="cards">
        {#each view.stories as key (key)}
          <article class="story" class:current={view.last?.kind === "story" && view.last.key === key}>
            <h4>{t(`play.story.${key}.title`)}</h4>
            <p>{t(`play.story.${key}.what`)}</p>
            <p class="expect"><b>{t("play.stories.expect")}</b> {t(`play.story.${key}.expect`)}</p>
            <button class="go" disabled={locked} onclick={() => play({ do: "story", key })}>
              <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4v16l13-8z" fill="currentColor" /></svg>
              {t("play.stories.play")}
            </button>
          </article>
        {/each}
      </div>
      <p class="muted">{t("play.stories.steps")}</p>
    </section>

    <section aria-labelledby="play-free">
      <h3 id="play-free">{t("play.free.title")}</h3>

      <details open>
        <summary>{t("play.free.people")}</summary>
        <div class="rows">
          {#each view.rooms as room (room.id)}
            <div class="row">
              <span>{room.name}</span>
              <div class="seg small" role="group" aria-label={room.name}>
                <button aria-pressed={room.on} disabled={busy} onclick={() => play({ do: "presence", id: room.id, on: true })}>{t(room.kind === "motion" ? "play.free.motion" : "play.free.someone")}</button>
                <button aria-pressed={!room.on} disabled={busy} onclick={() => play({ do: "presence", id: room.id, on: false })}>{t(room.kind === "motion" ? "play.free.no_motion" : "play.free.nobody")}</button>
              </div>
            </div>
          {/each}
          {#each view.residents as person (person.id)}
            <div class="row">
              <span>{person.name}</span>
              <div class="seg small" role="group" aria-label={person.name}>
                <button aria-pressed={person.home} disabled={busy} onclick={() => play({ do: "resident", id: person.id, on: true })}>{t("play.free.home")}</button>
                <button aria-pressed={!person.home} disabled={busy} onclick={() => play({ do: "resident", id: person.id, on: false })}>{t("play.free.away")}</button>
              </div>
            </div>
          {/each}
        </div>
        <div class="chips">
          <button disabled={busy} onclick={() => play({ do: "everyone", on: false })}>{t("play.free.everyone_away")}</button>
          <button disabled={busy} onclick={() => play({ do: "everyone", on: true })}>{t("play.free.everyone_home")}</button>
        </div>
        {#if view.rooms.some((r) => r.kind === "motion")}<p class="muted">{t("play.free.motion_note")}</p>{/if}
        {#if view.held}<p class="muted">{t("play.free.held")}</p>{/if}
      </details>

      {#if view.switches.length}
        <details>
          <summary>{t("play.free.switches")}</summary>
          <div class="seg" role="group" aria-label={t("play.free.by")}>
            <button aria-pressed={by === "person"} disabled={busy} onclick={() => (by = "person")}>{t("play.free.by_person")}</button>
            <button aria-pressed={by === "wall"} disabled={busy} onclick={() => (by = "wall")}>{t("play.free.by_wall")}</button>
          </div>
          {#each byRoom(view.switches) as [room, items] (room)}
            <h4 class="room">{room}</h4>
            <div class="rows">
              {#each items as item (item.id)}
                {@const [onWord, offWord] = switchWords(item.kind)}
                <div class="row">
                  <span>{item.name}</span>
                  <div class="seg small" role="group" aria-label={item.name}>
                    <button aria-pressed={item.on} disabled={busy} onclick={() => play({ do: "switch", id: item.id, on: true, by })}>{onWord}</button>
                    <button aria-pressed={!item.on} disabled={busy} onclick={() => play({ do: "switch", id: item.id, on: false, by })}>{offWord}</button>
                  </div>
                </div>
              {/each}
            </div>
          {/each}
        </details>
      {/if}

      {#if view.openings.length}
        <details>
          <summary>{t("play.free.openings")}</summary>
          <div class="rows">
            {#each view.openings as item (item.id)}
              <div class="row">
                <span>{item.name}</span>
                <div class="seg small" role="group" aria-label={item.name}>
                  <button aria-pressed={item.open} disabled={busy} onclick={() => play({ do: "opening", id: item.id, on: true })}>{t("play.free.open")}</button>
                  <button aria-pressed={!item.open} disabled={busy} onclick={() => play({ do: "opening", id: item.id, on: false })}>{t("play.free.closed")}</button>
                </div>
              </div>
            {/each}
          </div>
        </details>
      {/if}

      {#if view.weather || view.temperatures.length}
        <details>
          <summary>{t("play.free.weather")}</summary>
          {#if view.weather}
            {@const weather = view.weather}
            <h4 class="room">{t("play.free.sky")}{#if weather.temperature !== null} · {weather.temperature} °C{/if}</h4>
            <div class="chips" role="group" aria-label={t("play.free.sky")}>
              {#each weather.choices as choice (choice)}
                <button aria-pressed={weather.set === choice} disabled={busy} onclick={() => play({ do: "weather", value: choice })}>{t(`play.condition.${choice}`)}</button>
              {/each}
              <button aria-pressed={weather.set === null} disabled={busy} onclick={() => play({ do: "weather", value: null })}>{t("play.free.usual")}</button>
            </div>
            {#if weather.outdoor.length}
              <h4 class="room">{t("play.free.outdoor")}</h4>
              <div class="chips" role="group" aria-label={t("play.free.outdoor")}>
                {#each weather.outdoor as degrees (degrees)}
                  <button aria-pressed={weather.outdoorSet === degrees} disabled={busy} onclick={() => play({ do: "outdoor", value: degrees })}>{degrees} °C</button>
                {/each}
                <button aria-pressed={weather.outdoorSet === null} disabled={busy} onclick={() => play({ do: "outdoor", value: null })}>{t("play.free.usual")}</button>
              </div>
            {/if}
          {/if}
          {#if view.temperatures.length}
            <h4 class="room">{t("play.free.rooms")}</h4>
            <div class="rows">
              {#each view.temperatures as room (room.area)}
                <div class="row">
                  <span>{room.name}{#if room.value !== null} · <b>{room.value.toLocaleString(language)} °C</b>{/if}</span>
                  <div class="seg small" role="group" aria-label={room.name}>
                    <button disabled={busy} aria-label="{room.name}: {t('play.free.colder')}" title={t("play.free.colder")} onclick={() => play({ do: "room_temp", area: room.area, delta: -0.5 })}>−½</button>
                    <button disabled={busy} aria-label="{room.name}: {t('play.free.warmer')}" title={t("play.free.warmer")} onclick={() => play({ do: "room_temp", area: room.area, delta: 0.5 })}>+½</button>
                  </div>
                </div>
              {/each}
            </div>
          {/if}
        </details>
      {/if}

      {#if view.emergencies.length}
        <details>
          <summary>{t("play.free.emergencies")}</summary>
          <div class="rows">
            {#each view.emergencies as item (item.id)}
              {@const label = item.room ? `${t(`play.emergency.${item.key}`)} · ${item.room}` : t(`play.emergency.${item.key}`)}
              <div class="row" class:alarm={item.on}>
                <span>{label}</span>
                <div class="seg small" role="group" aria-label={label}>
                  <button aria-pressed={item.on} disabled={busy} onclick={() => play({ do: "emergency", id: item.id, on: true })}>{t("play.free.turned_on")}</button>
                  <button aria-pressed={!item.on} disabled={busy} onclick={() => play({ do: "emergency", id: item.id, on: false })}>{t("play.free.turned_off")}</button>
                </div>
              </div>
            {/each}
          </div>
          <button class="safe" disabled={busy} onclick={() => play({ do: "safe" })}>{t("play.free.safe")}</button>
        </details>
      {/if}

      {#if view.energy && (view.energy.prices || view.energy.solar)}
        {@const energy = view.energy}
        <details>
          <summary>{t("play.free.energy")}</summary>
          {#if energy.prices}
            <h4 class="room">{t("play.free.price")}</h4>
            <div class="chips" role="group" aria-label={t("play.free.price")}>
              {#each ["dear", "cheap", "negative"] as level (level)}
                <button aria-pressed={energy.price === level} disabled={busy} onclick={() => play({ do: "price", value: level })}>{t(`play.price.${level}`)}</button>
              {/each}
              <button aria-pressed={energy.price === null} disabled={busy} onclick={() => play({ do: "price", value: null })}>{t("play.free.usual")}</button>
            </div>
          {/if}
          {#if energy.solar}
            <h4 class="room">{t("play.free.panels")}</h4>
            <div class="chips" role="group" aria-label={t("play.free.panels")}>
              {#each ["sunny", "cloudy"] as sky (sky)}
                <button aria-pressed={energy.sky === sky} disabled={busy} onclick={() => play({ do: "sky", value: sky })}>{t(`play.panels.${sky}`)}</button>
              {/each}
              <button aria-pressed={energy.sky === null} disabled={busy} onclick={() => play({ do: "sky", value: null })}>{t("play.free.usual")}</button>
            </div>
          {/if}
        </details>
      {/if}
    </section>
  {:else}
    <p class="muted" aria-busy="true">…</p>
  {/if}
</div>

<style>
  .play { display: flex; flex-direction: column; gap: 22px; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(20px * var(--text-scale)); font-weight: 600; }
  .run { padding: 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--raised); }
  .run.running { border-color: var(--accent); }
  .run-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .go.stop { background: var(--surface); color: var(--text); border-color: var(--edge); }
  .state { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .state .clock { font-size: calc(18px * var(--text-scale)); }
  .speed { display: flex; flex-direction: column; gap: 6px; font-size: calc(14px * var(--text-scale)); color: var(--muted); }
  .speed b { color: var(--text); }
  .speed input { width: 100%; min-height: 44px; accent-color: var(--accent); }
  .ticks { display: flex; justify-content: space-between; gap: 4px; font-size: calc(11px * var(--text-scale)); }
  .ticks span { flex: 1; text-align: center; }
  .ticks span:first-child { text-align: left; }
  .ticks span:last-child { text-align: right; }
  section { display: flex; flex-direction: column; gap: 10px; }
  h3 { margin: 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  h4 { margin: 0; font-size: calc(16px * var(--text-scale)); }
  h4.room { font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; margin-top: 6px; }
  .intro { margin: 0; line-height: 1.45; }
  .intro .link { display: inline; min-height: 0; }
  .muted { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); line-height: 1.4; }
  .clock { margin: 0; font-family: var(--font-display); font-size: calc(24px * var(--text-scale)); font-weight: 600; }
  .progress { height: 8px; border-radius: 4px; background: var(--raised); border: 1px solid var(--line); overflow: hidden; }
  .progress span { display: block; height: 100%; background: var(--accent); }
  .seg { display: flex; gap: 6px; flex-wrap: wrap; }
  .seg button { flex: 1; min-width: max-content; min-height: 44px; padding: 0 14px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); color: var(--muted); cursor: pointer; }
  .seg.small { flex: none; flex-wrap: nowrap; }
  .seg.small button { min-height: 40px; padding: 0 12px; font-size: calc(14px * var(--text-scale)); }
  .seg button[aria-pressed="true"], .chips button[aria-pressed="true"] { color: var(--text); border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); font-weight: 600; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chips button { min-height: 44px; padding: 0 14px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: default; }
  .steps { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; font-size: calc(14px * var(--text-scale)); color: var(--muted); }
  .steps .link { min-height: 32px; }
  .happened { padding: 12px 14px; border-radius: 14px; border: 1px solid var(--accent); background: var(--raised); }
  .happened ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
  .happened li { display: flex; gap: 10px; font-size: calc(14px * var(--text-scale)); line-height: 1.4; }
  .happened li time { flex: none; font-family: var(--font-mono); color: var(--muted); }
  .happened li.notice span { font-weight: 600; }
  .happened li.emergency span { color: var(--emergency); }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
  .story .go { margin-top: auto; }
  .story { display: flex; flex-direction: column; gap: 6px; padding: 12px 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--raised); }
  .story.current { border-color: var(--accent); }
  .story p { margin: 0; font-size: calc(14px * var(--text-scale)); line-height: 1.4; }
  .story .expect { color: var(--muted); }
  .story .expect b { display: block; color: var(--text); font-weight: 600; }
  .go { align-self: flex-start; display: inline-flex; align-items: center; gap: 6px; min-height: 44px; padding: 0 18px; border-radius: 22px; border: 1px solid var(--accent); background: var(--accent); color: var(--on-accent); font-weight: 600; cursor: pointer; }
  details { border: 1px solid var(--line); border-radius: 14px; padding: 0 14px; display: flex; flex-direction: column; gap: 8px; }
  details[open] { padding-bottom: 14px; }
  summary { min-height: 44px; display: flex; align-items: center; font-weight: 600; cursor: pointer; }
  details > :not(summary) { margin-top: 8px; }
  .rows { display: flex; flex-direction: column; gap: 6px; }
  .row { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; min-height: 44px; }
  .row > span { min-width: 0; overflow-wrap: anywhere; }
  .row.alarm > span { color: var(--emergency); font-weight: 600; }
  .safe { align-self: flex-start; min-height: 44px; padding: 0 18px; border-radius: 22px; border: 1px solid var(--accent); background: var(--raised); color: var(--accent); font-weight: 600; cursor: pointer; }
</style>

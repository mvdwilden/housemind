<script lang="ts">
  // All short messages in one stack: what HouseMind said, what it just did, "Saved." – each
  // with a bar that shows when it goes away. Hovering or focusing pauses it (WCAG 2.2.1); a
  // question never goes away by itself. At most three at a time, the newest at the bottom. A
  // notice that is used again (a new round) starts its bar over.
  import type { Notice } from "../lib/connection.svelte";
  import type { T } from "../lib/ui";

  interface Props {
    notices: Notice[];
    t: T;
    onclose: (id: number) => void;
    onconfirm: (notice: Notice, yes: boolean) => void;
    onwhy: (notice: Notice) => void;
    onundo: (notice: Notice) => void;
  }
  let { notices, t, onclose, onconfirm, onwhy, onundo }: Props = $props();

  // Per notice: how much time is left, in which round, and whether it is paused.
  const left = new Map<number, number>();
  const rounds = new Map<number, number>();
  let paused = $state<Set<number>>(new Set());
  let tick = $state(0);

  $effect(() => {
    const step = 100;
    const timer = setInterval(() => {
      for (const n of notices) {
        if (n.ms === null || paused.has(n.id)) continue;
        const rest = timeLeft(n) - step;
        left.set(n.id, rest);
        if (rest <= 0) onclose(n.id);
      }
      tick++;
    }, step);
    return () => clearInterval(timer);
  });

  function timeLeft(n: Notice): number {
    if (rounds.get(n.id) !== n.round) {
      rounds.set(n.id, n.round);
      left.set(n.id, n.ms ?? 0);
    }
    return left.get(n.id) ?? n.ms ?? 0;
  }
  function share(n: Notice): number {
    void tick;
    return n.ms === null ? 1 : Math.max(0, timeLeft(n) / n.ms);
  }
  function hold(id: number, on: boolean) {
    const next = new Set(paused);
    if (on) next.add(id);
    else next.delete(id);
    paused = next;
  }
</script>

<div class="stack" role="status" aria-live="polite">
  {#each notices as n (n.id)}
    <div
      class="notice"
      class:saved={n.kind === "saved"}
      role="group"
      onmouseenter={() => hold(n.id, true)}
      onmouseleave={() => hold(n.id, false)}
      onfocusin={() => hold(n.id, true)}
      onfocusout={() => hold(n.id, false)}
    >
      {#if n.heard}<p class="heard">“{n.heard}”</p>{/if}
      <p>{#if n.voice}<strong class="voice">{n.voice}</strong>{/if}{n.text}</p>
      <div class="row">
        {#if n.confirm}
          <button class="yes" onclick={() => onconfirm(n, true)}>{t("ui.yes")}</button>
          <button onclick={() => onconfirm(n, false)}>{t("ui.no")}</button>
        {:else}
          {#if n.kind === "action"}
            <button class="link" onclick={() => onwhy(n)}>{t("ui.why")}</button>
            {#if n.undo}<button class="link" onclick={() => onundo(n)}>{t("ui.undo")}</button>{/if}
          {/if}
          <button onclick={() => onclose(n.id)}>{t("ui.ok")}</button>
        {/if}
      </div>
      {#if n.ms !== null}
        <div class="bar" aria-hidden="true"><span style:width="{share(n) * 100}%"></span></div>
      {/if}
    </div>
  {/each}
</div>

<style>
  .stack { display: flex; flex-direction: column; gap: 8px; }
  .notice { position: relative; overflow: hidden; padding: 12px 14px 14px; border-radius: 16px; background: var(--raised); border: 1px solid var(--line); box-shadow: 0 8px 30px rgba(0, 0, 0, 0.3); display: flex; flex-direction: column; gap: 8px; }
  .notice.saved { flex-direction: row; align-items: center; justify-content: space-between; padding: 8px 8px 10px 14px; }
  p { margin: 0; white-space: pre-line; }
  .row { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }
  button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--surface); cursor: pointer; font-weight: 500; }
  .link { border: none; background: none; color: var(--accent); font-weight: 600; padding: 0 8px; }
  .yes { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
  .bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: var(--line); }
  .bar span { display: block; height: 100%; background: var(--accent); }
  .voice { display: block; font-size: calc(13px * var(--text-scale)); letter-spacing: 0.02em; color: var(--accent); margin-bottom: 2px; }
  .heard { margin: 0 0 4px; color: var(--muted); font-size: calc(13px * var(--text-scale)); font-style: italic; }
</style>

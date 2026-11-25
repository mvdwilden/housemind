<script lang="ts">
  // The tour (E-249, B-98), like the first version: the screen dark but for one lit part, a
  // small card with "Stap 2 van 6", what it is in Vesta's words, and Overslaan / Terug /
  // Volgende. Her voice tells each step when it is on for this device. Escape skips, the
  // arrows go back and forth; with less motion nothing slides.
  import { trap } from "../lib/focus";
  import { tick } from "svelte";
  import { placeTip, type TourStep } from "../lib/tour";
  import type { T } from "../lib/ui";

  interface Props {
    steps: TourStep[];
    t: T;
    onread: (key: string) => void; // her voice for a text
    onend: (finished: boolean) => void;
  }
  let { steps, t, onread, onend }: Props = $props();

  let index = $state(0);
  let spot = $state<{ top: number; left: number; width: number; height: number } | null>(null);
  let place = $state({ top: 0, left: 0 });
  let tip = $state<HTMLElement | null>(null);
  let next = $state<HTMLButtonElement | null>(null);
  const step = $derived(steps[index]);
  const last = $derived(index === steps.length - 1);
  const offer = $derived(step?.key === "more");

  function still(): boolean {
    return matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function target(): HTMLElement | null {
    if (!step?.target) return null;
    // The one that is on the screen: the tabs at the bottom on a phone, at the side when wide.
    for (const element of document.querySelectorAll<HTMLElement>(`[data-tour="${step.target}"]`)) {
      const box = element.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) return element;
    }
    return null;
  }

  function measure(): void {
    const element = target();
    const width = Math.min(340, innerWidth - 24);
    const height = tip?.offsetHeight ?? 200;
    if (!element) {
      spot = null;
      place = placeTip(null, { width: innerWidth, height: innerHeight }, { width, height });
      return;
    }
    const box = element.getBoundingClientRect();
    spot = { top: box.top - 8, left: box.left - 8, width: box.width + 16, height: box.height + 16 };
    place = placeTip({ top: box.top - 8, bottom: box.bottom + 8, left: box.left }, { width: innerWidth, height: innerHeight }, { width, height });
  }

  // Each step: to its part of the screen, her words aloud, the focus on Next.
  $effect(() => {
    if (!step) return;
    onread(`ui.tour.${step.key}.text`);
    target()?.scrollIntoView({ block: "center", behavior: still() ? "auto" : "smooth" });
    void tick().then(() => {
      measure();
      setTimeout(measure, still() ? 0 : 350); // after the scroll
      next?.focus();
    });
  });

  $effect(() => {
    addEventListener("resize", measure);
    addEventListener("scroll", measure, true);
    return () => {
      removeEventListener("resize", measure);
      removeEventListener("scroll", measure, true);
    };
  });

  function go(by: number): void {
    if (index + by >= steps.length) onend(true);
    else index = Math.max(0, index + by);
  }

  function key(event: KeyboardEvent): void {
    if (event.key === "Escape") onend(false);
    if (event.key === "ArrowRight") go(1);
    if (event.key === "ArrowLeft") go(-1);
  }
</script>

<svelte:window onkeydown={key} />

<div class="tour" class:still={false}>
  {#if spot}
    <div class="spot" style:top="{spot.top}px" style:left="{spot.left}px" style:width="{spot.width}px" style:height="{spot.height}px"></div>
  {:else}
    <div class="dim"></div>
  {/if}
  {#if step}
    <div class="tip" role="dialog" aria-modal="true" aria-labelledby="tour-title" aria-describedby="tour-text" tabindex="-1" use:trap bind:this={tip}
      style:top="{place.top}px" style:left="{place.left}px" style:width="min(340px, calc(100vw - 24px))">
      {#if !offer}<p class="count">{t("ui.tour.step", { n: index + 1, total: steps.length })}</p>{/if}
      <h3 id="tour-title">{t(`ui.tour.${step.key}.title`)}</h3>
      <p id="tour-text" aria-live="polite">{t(`ui.tour.${step.key}.text`)}</p>
      <div class="buttons">
        {#if offer}
          <button onclick={() => onend(false)}>{t("ui.tour.no")}</button>
          <span class="space"></span>
          <button class="primary" bind:this={next} onclick={() => go(1)}>{t("ui.tour.show")}</button>
        {:else}
          <button onclick={() => onend(false)}>{t("ui.tour.skip")}</button>
          <span class="space"></span>
          {#if index > 0}<button onclick={() => go(-1)}>{t("ui.tour.back")}</button>{/if}
          <button class="primary" bind:this={next} onclick={() => go(1)}>{last ? t("ui.tour.finish") : t("ui.tour.next")}</button>
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .tour { position: fixed; inset: 0; z-index: 50; }
  .dim { position: fixed; inset: 0; background: rgba(5, 8, 14, 0.68); }
  .spot { position: fixed; border-radius: 16px; box-shadow: 0 0 0 9999px rgba(5, 8, 14, 0.68), 0 0 0 2px var(--accent), 0 0 24px rgba(124, 156, 255, 0.45); transition: all 0.3s ease; pointer-events: none; }
  .tip { position: fixed; background: var(--surface); border: 1px solid var(--line); border-radius: 16px; padding: 16px 16px 12px; box-shadow: 0 20px 50px rgba(0, 0, 0, 0.4); transition: top 0.3s, left 0.3s; outline: none; }
  .count { margin: 0; color: var(--accent); font-size: calc(12px * var(--text-scale)); font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; }
  h3 { margin: 2px 0 6px; font-size: calc(17px * var(--text-scale)); }
  p { margin: 0 0 12px; color: var(--muted); line-height: 1.5; font-size: calc(15px * var(--text-scale)); }
  .buttons { display: flex; gap: 8px; align-items: center; }
  .space { flex: 1; }
  button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--raised); color: var(--text); font-size: calc(15px * var(--text-scale)); cursor: pointer; }
  .primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  @media (prefers-reduced-motion: reduce) {
    .spot, .tip { transition: none; }
  }
</style>

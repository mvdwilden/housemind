<script lang="ts">
  // One list of parts (E-399, B-320): per part Uit, Observeren, Voorstellen or Autonoom – in
  // Huisinstellingen and, on Expert, in the wizard (E-401). Off for a core part only on Expert.
  import type { PartView } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    parts: PartView[];
    expert: boolean;
    lang: string;
    t: T;
    onchange: (part: string, state: string) => void;
  }
  let { parts, expert, lang, t, onchange }: Props = $props();
  const STATES = ["off", "observe", "propose", "autonomous"];

  function since(iso: string | null | undefined): string {
    if (!iso) return "";
    return new Date(iso).toLocaleDateString(lang, { day: "numeric", month: "short" });
  }
</script>

<ul class="parts">
  {#each parts as part (part.part)}
    <li class:off={part.state === "off"}>
      <label for="part-{part.part}">
        <span class="name">{t(`ui.domain.${part.part}`)}</span>
        {#if part.experimental}<span class="tag">{t("ui.parts.experimental")}</span>{/if}
        {#if part.state === "off" && part.offSince}<span class="since">{t("ui.parts.off_since", { date: since(part.offSince) })}</span>{/if}
      </label>
      <select id="part-{part.part}" class="step-choice {part.state}" value={part.state}
        onchange={(e) => onchange(part.part, e.currentTarget.value)}>
        {#each STATES as s (s)}
          {#if s !== "off" || expert || part.experimental || part.state === "off"}
            <option value={s}>{t(`steps.${s}`)}</option>
          {/if}
        {/each}
      </select>
    </li>
  {/each}
</ul>
<p class="muted">{t("ui.parts.hint")}</p>
{#if !expert}<p class="muted">{t("ui.parts.involved")}</p>{/if}

<style>
  .parts { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .parts li { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 52px; border-bottom: 1px solid var(--line); }
  .parts li.off .name { color: var(--muted); }
  label { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; min-width: 0; }
  .name { font-weight: 600; }
  .tag { font-size: calc(12px * var(--text-scale)); color: var(--learning); border: 1px solid currentColor; border-radius: 999px; padding: 0 6px; }
  .since { font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  select { min-height: 44px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); padding: 0 12px; font: inherit; }
  /* Each part in its step's colour (StepPill), the same as everywhere else; off is plain. */
  select.step-choice { border-left-width: 6px; }
  select.step-choice.observe { border-left-color: var(--step-observe); }
  select.step-choice.propose { border-left-color: var(--step-propose); }
  select.step-choice.autonomous { border-left-color: var(--step-autonomous); }
  .muted { margin: 8px 0 0; color: var(--muted); }
</style>

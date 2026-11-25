<script lang="ts">
  import type { T } from "../lib/ui";

  // Where Vesta's voice goes now (20.19): to the cloud, or it stays in the house.
  interface Props {
    version: string;
    t: T;
    speech?: { cloud: string[]; local: boolean } | null;
    languages?: Record<string, string>; // each language in its own name
    haOldest?: string; // the lowest Home Assistant version
    name?: string; // Vesta's name
    reaction?: { ms: number | null; maxMs: number | null }; // Expert: from trigger to done (QA 3)
  }
  let { version, t, speech = null, languages = {}, haOldest = "", name = "Vesta", reaction }: Props = $props();
  // Good to know (E-413): only what is true of this version; the facts come from the app itself.
  const facts = $derived([
    { key: "version", text: `${t("ui.about_facts.version", { version })}${haOldest ? ` ${t("ui.about_facts.version_ha", { ha: haOldest })}` : ""}` },
    { key: "privacy", text: t("ui.about_facts.privacy") },
    { key: "access", text: t("ui.about_facts.access", { name }) },
    { key: "offline", text: t("ui.about_facts.offline") },
    { key: "languages", text: Object.values(languages).join(", ") },
    { key: "open", text: t("ui.about_facts.open") },
    { key: "apart", text: t("ui.about_facts.apart") },
    { key: "care", text: t("ui.about_facts.care") },
    // On Expert one line: how fast HouseMind follows a sensor (the meter that was in Brein).
    ...(reaction
      ? [{ key: "reaction", text: reaction.ms === null
          ? t("ui.about_facts.reaction_none")
          : t("ui.about_facts.reaction", { ms: Math.round(reaction.ms), max: Math.round(reaction.maxMs ?? reaction.ms) }) }]
      : []),
  ]);

  // The privacy statement (E-165): one source in the language files, also used by the website.
  const sections = ["stored", "out", "see", "rights"] as const;
  type Block = { list: boolean; lines: string[] };
  function blocks(text: string): Block[] {
    const out: Block[] = [];
    for (const raw of text.split("\n")) {
      const list = raw.startsWith("- ");
      const line = list ? raw.slice(2) : raw;
      const last = out[out.length - 1];
      if (last && last.list === list) last.lines.push(line);
      else out.push({ list, lines: [line] });
    }
    return out;
  }
</script>

<p><strong>HouseMind</strong> <span class="mono">{version}</span></p>
<p>{t("ui.about_text")}</p>
<p class="muted">{t("ui.about_privacy")}</p>
<details class="privacy">
  <summary>{t("privacy.title")}</summary>
  <p>{t("privacy.intro")}</p>
  {#if speech?.cloud.length}
    <p class="speech">{t("privacy.speech_cloud", { engines: speech.cloud.join(", ") })}</p>
  {:else if speech?.local}
    <p class="speech">{t("privacy.speech_local")}</p>
  {/if}
  {#each sections as key (key)}
    <h4>{t(`privacy.${key}_title`)}</h4>
    {#each blocks(t(`privacy.${key}`)) as block, i (i)}
      {#if block.list}
        <ul>{#each block.lines as line, j (j)}<li>{line}</li>{/each}</ul>
      {:else}
        {#each block.lines as line, j (j)}<p>{line}</p>{/each}
      {/if}
    {/each}
  {/each}
  <p class="muted">{t("privacy.contact")}</p>
</details>
<p class="muted">{t("ui.about_disclaimer")}</p>
<section class="facts" aria-labelledby="about-facts">
  <h4 id="about-facts">{t("ui.about_facts.title")}</h4>
  <dl>
    {#each facts as fact (fact.key)}
      <dt>{t(`ui.about_facts.${fact.key}_label`)}</dt>
      <dd>{fact.text}</dd>
    {/each}
  </dl>
</section>

<style>
  p { margin: 0; line-height: 1.5; }
  .muted { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .mono { font-family: var(--font-mono); font-size: calc(13px * var(--text-scale)); color: var(--muted); }
  .privacy { display: flex; flex-direction: column; gap: 8px; }
  .privacy summary { cursor: pointer; color: var(--accent); font-weight: 600; min-height: 44px; display: flex; align-items: center; }
  .privacy h4 { margin: 12px 0 0; font-size: calc(15px * var(--text-scale)); }
  .privacy ul { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 6px; line-height: 1.5; }
  .privacy p { margin-top: 6px; }
  .facts { border-top: 1px solid var(--line); padding-top: 12px; }
  .facts h4 { margin: 0 0 8px; font-size: calc(15px * var(--text-scale)); }
  .facts dl { margin: 0; display: grid; grid-template-columns: minmax(0, 11em) minmax(0, 1fr); gap: 8px 14px; font-size: calc(14px * var(--text-scale)); line-height: 1.5; }
  .facts dt { color: var(--muted); }
  .facts dd { margin: 0; }
  @media (max-width: 520px) {
    .facts dl { grid-template-columns: minmax(0, 1fr); gap: 2px; }
    .facts dd { margin-bottom: 8px; }
  }
</style>

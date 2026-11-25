<script lang="ts">
  // Vesta's name and voice (E-220, E-230) – in House settings and when setting up (E-281): one
  // component, so the setting is in one place.
  import type { HouseSettings } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    settings: HouseSettings;
    t: T;
    send: (message: Record<string, unknown>) => void;
    get: (what: string) => void;
    houseLanguage?: boolean; // House settings: the language of the house can change (E-320)
  }
  let { settings, t, send, get, houseLanguage = false }: Props = $props();
  const setting = (k: string, value: unknown) => send({ type: "house_setting", key: k, value });

  // Vesta's name and voice (E-220, E-230): the recommended voice is the one of HouseMind's own
  // Assist pipeline in HA; choosing another one is the administrator's call.
  const vesta = $derived(settings?.vesta);
  let vestaName = $state("");
  $effect(() => {
    if (vesta && !vestaName) vestaName = vesta.name;
  });
  const voiceNow = $derived(vesta?.chosen ?? vesta?.recommended ?? null);
  const engineNow = $derived(vesta?.engines.find((e) => e.id === voiceNow?.engine));
  function chooseEngine(id: string): void {
    if (!vesta) return;
    if (id === "") return setting("vesta_voice", null); // back to the recommended voice
    const engine = vesta.engines.find((e) => e.id === id);
    setting("vesta_voice", { engine: id, language: vesta.language, voice: engine?.voices[0]?.id ?? null });
    setTimeout(() => get("settings"), 300);
  }
  // The language of the house (E-320): a yes or no first, then her pipeline follows.
  let pickedLanguage = $state<string | null>(null);
  function changeLanguage(yes: boolean): void {
    if (yes && pickedLanguage) {
      setting("house_language", pickedLanguage);
      setTimeout(() => get("settings"), 600);
    }
    pickedLanguage = null;
  }
  function chooseVoice(id: string): void {
    if (!voiceNow) return;
    setting("vesta_voice", { engine: voiceNow.engine, language: vesta?.language ?? null, voice: id || null });
    setTimeout(() => get("settings"), 300);
  }
</script>

{#if vesta}
  <section data-section="vesta">
    <h3>{vesta.name}</h3>
    <label class="field">{t("ui.settings.vesta.name")}
      <input maxlength="24" bind:value={vestaName} onchange={() => vestaName.trim() && setting("vesta_name", vestaName.trim())} />
    </label>
    <label class="field">{t("ui.settings.vesta.engine")}
      <select value={vesta.chosen ? vesta.chosen.engine : ""} onchange={(e) => chooseEngine(e.currentTarget.value)}>
        <option value="">{t("ui.settings.vesta.recommended")}{vesta.recommended ? ` (${vesta.engines.find((x) => x.id === vesta.recommended?.engine)?.name ?? vesta.recommended.engine}${vesta.recommended.voice ? ", " + vesta.recommended.voice : ""})` : ""}</option>
        {#each vesta.engines as engine (engine.id)}
          <option value={engine.id}>{engine.name}</option>
        {/each}
      </select>
    </label>
    {#if vesta.chosen && engineNow?.voices.length}
      <label class="field">{t("ui.settings.vesta.voice")}
        <select value={vesta.chosen.voice ?? ""} onchange={(e) => chooseVoice(e.currentTarget.value)}>
          {#each engineNow.voices as voice (voice.id)}
            <option value={voice.id}>{voice.name}</option>
          {/each}
        </select>
      </label>
    {/if}
    <p class="muted">{t("ui.settings.vesta.hint", { name: vesta.name })}</p>
    {#if houseLanguage}
      <label class="field">{t("ui.settings.vesta.house_language")}
        <select value={pickedLanguage ?? vesta.houseLanguage} onchange={(e) => (pickedLanguage = e.currentTarget.value === vesta.houseLanguage ? null : e.currentTarget.value)}>
          {#each Object.entries(vesta.languages) as [code, name] (code)}<option value={code}>{name}</option>{/each}
        </select>
      </label>
      {#if pickedLanguage}
        <div class="confirm" role="group" aria-label={t("ui.settings.vesta.house_language")}>
          <p>{t("ui.settings.vesta.house_language_confirm", { chosen: vesta.languages[pickedLanguage] ?? pickedLanguage, name: vesta.name })}</p>
          <div class="buttons">
            <button class="primary" onclick={() => changeLanguage(true)}>{t("ui.yes")}</button>
            <button onclick={() => changeLanguage(false)}>{t("ui.no")}</button>
          </div>
        </div>
      {/if}
      <p class="muted">{t("ui.settings.vesta.house_language_hint", { name: vesta.name })}</p>
    {/if}
  </section>
{/if}

<style>
  section { display: flex; flex-direction: column; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
  h3 { margin: 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  .muted { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .field { display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .confirm { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 12px; }
  .confirm p { margin: 0; }
  .buttons { display: flex; flex-wrap: wrap; gap: 8px; }
  .confirm button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--surface); color: var(--text); cursor: pointer; }
  .confirm .primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  select, input:not([type="checkbox"]) { min-height: 44px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); padding: 0 12px; font: inherit; }
</style>

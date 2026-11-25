<script lang="ts">
  // Personal preferences (E-128): what you can set grows with your level. Three groups (E-265,
  // B-112): about me (the same on every device), this device (only here: colour scheme, text
  // size, voice, wall tablet; less motion follows the device itself, E-319) and help. Help and the House settings themselves have one
  // place: the header (E-260).
  import SettingsGroups, { type SettingsGroup } from "../components/SettingsGroups.svelte";
  import { device } from "../lib/device.svelte";
  import { kioskWanted, setKioskWanted } from "../lib/kiosk";
  import { startScheme } from "../lib/schemes";
  import { setTabletArea } from "../lib/tablet";
  import { setVoiceWanted, voiceWanted } from "../lib/voice";
  import { canListen } from "../lib/listen";
  import type { MyData, Snapshot } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    snap: Snapshot;
    t: T;
    send: (message: Record<string, unknown>) => void;
    onabout: () => void;
    focus?: string | null; // a part to open, when Vesta offered it (E-244)
    myData?: MyData; // "Mijn gegevens" (E-300), when asked for
  }
  let { snap, t, send, onabout, focus = null, myData }: Props = $props();
  let forgetting = $state(false);
  const p = $derived(snap.prefs);
  const set = (key: string, value: unknown) => send({ type: "set_pref", key, value });
  const languages = $derived(["auto", ...Object.keys(snap.languageNames)]);
  // This device: its own choice, else where the person was (E-266).
  const scheme = $derived(device.choices.scheme ?? startScheme(p.theme));
  const textSize = $derived(device.choices.textSize ?? p.textSize);
  const dark = $derived((snap.themes ?? []).find((s) => s.id === "diep"));
  const light = $derived((snap.themes ?? []).find((s) => s.id === "licht"));
  const groups: SettingsGroup[] = $derived([
    { key: "about_me", title: t("ui.prefs.group.about_me"), hint: t("ui.prefs.group.about_me_hint"), sections: ["level", "address", "timing", "language", "notifications", "daily", "my_data"] },
    { key: "device", title: t("ui.prefs.group.device"), hint: t("ui.prefs.group.device_hint"), sections: ["scheme", "display", "voice", "tablet"] },
  ]);
  let kiosk = $state(true);
  try {
    kiosk = kioskWanted(window.localStorage);
  } catch {
    kiosk = true;
  }
  let aloud = $state(true);
  try {
    aloud = voiceWanted(window.localStorage);
  } catch {
    aloud = true;
  }
  function setAloud(on: boolean) {
    aloud = on;
    try {
      setVoiceWanted(window.localStorage, on);
    } catch {
      // blocked storage: only for this visit
    }
  }
</script>

<SettingsGroups {groups} {focus} back={`← ${t("ui.my_preferences")}`}>
  {#snippet content(key: string)}
    {#if key === "about_me"}
      <section data-section="level">
        <h3>{t("ui.level")}</h3>
        <div class="levels">
          {#each snap.levels as option (option.level)}
            <button class="level" aria-pressed={option.level === snap.level} onclick={() => send({ type: "set_level", level: option.level })}>
              <strong>{t(`levels.${option.key}`)}</strong>
              <span>{t(`levels.${option.key}_hint`)}</span>
            </button>
          {/each}
        </div>
      </section>

      <!-- Time to talk and to read (E-299): goes with you to every device. -->
      <section data-section="timing">
        <h3>{t("ui.prefs.timing")}</h3>
        <div class="seg" role="group" aria-label={t("ui.prefs.timing")}>
          {#each ["normal", "longer", "long"] as choice (choice)}
            <button aria-pressed={(p.timing ?? "normal") === choice} onclick={() => set("timing", choice)}>{t(`ui.prefs.timing_${choice}`)}</button>
          {/each}
        </div>
        <p class="muted">{t("ui.prefs.timing_hint", { name: snap.vesta?.name ?? "Vesta" })}</p>
      </section>
      {#if snap.vesta?.formal}
        <section data-section="address">
          <h3>{t("ui.prefs.address", { name: snap.vesta.name })}</h3>
          <div class="seg" role="group" aria-label={t("ui.prefs.address", { name: snap.vesta.name })}>
            {#each ["je", "u"] as form (form)}
              <button aria-pressed={(p.address ?? "je") === form} onclick={() => set("address", form)}>{t(`ui.prefs.address_${form}`)}</button>
            {/each}
          </div>
          <p class="muted">{t("ui.prefs.address_hint", { name: snap.vesta.name })}</p>
        </section>
      {/if}

      <section data-section="language">
        <h3>{t("ui.prefs.language")}</h3>
        <select value={p.language} aria-label={t("ui.prefs.language")} onchange={(e) => set("language", e.currentTarget.value)}>
          {#each languages as l (l)}<option value={l}>{l === "auto" ? t("ui.prefs.language_auto") : snap.languageNames[l]}</option>{/each}
        </select>
      </section>

      <section data-section="notifications">
        <h3>{t("ui.prefs.notifications")}</h3>
        {#if snap.youGet?.length}
          <!-- What you get, from your own level and choices (E-407, B-333). -->
          <div class="card">
            <p class="card-title">{t("ui.you_get.title")}</p>
            <ul class="plain">{#each snap.youGet as line (line)}<li>{line}</li>{/each}</ul>
          </div>
        {/if}
        {#if snap.level === 1}
          <!-- Messages in danger (E-20, E-289, E-291): yes is emergencies and safety; not chosen is emergencies only. -->
          <p id="danger-label">{t("ui.prefs.danger")}</p>
          <div class="seg" role="group" aria-labelledby="danger-label">
            {#each [true, false] as yes (yes)}
              <button aria-pressed={p.emergencyPush === yes} onclick={() => set("emergency_push", yes)}>{t(yes ? "ui.yes" : "ui.no")}</button>
            {/each}
          </div>
          <p class="muted">{t(p.emergencyPush === true ? "ui.prefs.danger_yes" : p.emergencyPush === false ? "ui.prefs.danger_no" : "ui.prefs.danger_unset")}</p>
        {:else if snap.level === 2}
          <div class="seg" role="group" aria-label={t("ui.prefs.amount")}>
            {#each ["less", "normal", "more"] as a (a)}
              <button aria-pressed={p.amount === a} onclick={() => set("amount", a)}>{t(`ui.prefs.amount_${a}`)}</button>
            {/each}
          </div>
          <p class="muted">{t("ui.prefs.emergency_always")}</p>
        {:else}
          {#each ["important", "info", "learning"] as kind (kind)}
            <label class="check"><input type="checkbox" checked={p.kinds[kind as "info"]} onchange={(e) => set("kinds", { ...p.kinds, [kind]: e.currentTarget.checked })} /> {t(`ui.notifications.kind.${kind}`)}</label>
          {/each}
          {#if snap.level >= 4}
            <label class="field">{t("ui.prefs.max_per_day")}
              <input type="number" min="0" max="50" value={p.maxPerDay} onchange={(e) => set("max_per_day", Number(e.currentTarget.value))} />
            </label>
            <p class="muted">{t("ui.prefs.max_per_day_hint")}</p>
            <label class="field">{t("ui.prefs.questions_per_day")}
              <input type="number" min="0" max="20" value={p.questionsPerDay ?? 5} onchange={(e) => set("questions_per_day", Number(e.currentTarget.value))} />
            </label>
          {/if}
          <p class="muted">{t("ui.prefs.emergency_always")}</p>
        {/if}
        {#if snap.level >= 2}
          <label class="field">{t("ui.prefs.bundle_at")}
            <input type="time" value={p.bundleAt ?? "19:00"} onchange={(e) => e.currentTarget.value && set("bundle_at", e.currentTarget.value)} />
          </label>
        {/if}
        {#if snap.level >= 4}
          <fieldset class="quiet">
            <legend>{t("ui.prefs.quiet")}</legend>
            <label class="field">{t("ui.prefs.quiet_from")}
              <input type="time" value={p.quietFrom ?? ""} onchange={(e) => set("quiet_from", e.currentTarget.value)} />
            </label>
            <label class="field">{t("ui.prefs.quiet_until")}
              <input type="time" value={p.quietUntil ?? ""} onchange={(e) => set("quiet_until", e.currentTarget.value)} />
            </label>
            <p class="muted">{t("ui.prefs.quiet_hint")}</p>
          </fieldset>
        {/if}
      </section>

      {#if snap.level >= 2}
        <section data-section="daily">
          <h3>{t("ui.prefs.daily")}</h3>
          {#each ["weather", "agenda", "energy"] as part (part)}
            <label class="check"><input type="checkbox" checked={p.briefing[part as "weather"]} onchange={(e) => set("briefing", { ...p.briefing, [part]: e.currentTarget.checked })} /> {t(`ui.prefs.briefing_${part}`)}</label>
          {/each}
          <label class="check"><input type="checkbox" checked={p.bedtimeCheck} onchange={(e) => set("bedtime_check", e.currentTarget.checked)} /> {t("ui.prefs.bedtime_check")}</label>
          <label class="field">{t("ui.prefs.report_day")}
            <select value={p.reportDay} onchange={(e) => set("report_day", Number(e.currentTarget.value))}>
              {#each [0, 1, 2, 3, 4, 5, 6] as day (day)}<option value={day}>{t(`ui.weekday.${day}`)}</option>{/each}
            </select>
          </label>
          {#if snap.level >= 4}
            <label class="field">{t("ui.prefs.spoken_per_day")}
              <input type="number" min="0" max="20" value={p.spokenPerDay} onchange={(e) => set("spoken_per_day", Number(e.currentTarget.value))} />
            </label>
          {/if}
          <p class="muted">{t("ui.prefs.daily_hint")}</p>
        </section>
      {/if}
      <!-- Mijn gegevens (E-300): what HouseMind keeps about you, as a file, or forget it. -->
      <section data-section="my_data">
        <h3>{t("ui.my_data.title")}</h3>
        <p class="muted">{t("ui.my_data.note")}</p>
        {#if myData?.known}
          <ul class="plain">
            <li>{t("ui.my_data.level", { level: t(`levels.${["carefree", "informed", "involved", "expert"][myData.level - 1]}`) })}</li>
            <li>{t("ui.my_data.conversations", { count: myData.conversations })}</li>
            <li>{t("ui.my_data.answers", { count: myData.answers.length })}</li>
            <li>{t("ui.my_data.consents", { count: Object.keys(myData.consents).length })}</li>
          </ul>
          {#if myData.consents.care}
            <!-- Care: their own yes, to take back here (QA 2, M-8). -->
            <p>{t("ui.my_data.care")}</p>
            <button onclick={() => { send({ type: "care_withdraw" }); setTimeout(() => send({ type: "get", what: "my_data" }), 300); }}>{t("ui.my_data.care_withdraw")}</button>
          {/if}
          {#if myData.rules.length}
            <p>{t("ui.my_data.rules")}</p>
            <ul class="plain">{#each myData.rules as rule (rule)}<li>{rule}</li>{/each}</ul>
          {/if}
        {:else}
          <button onclick={() => send({ type: "get", what: "my_data" })}>{t("ui.my_data.show")}</button>
        {/if}
        {#if snap.keepTerms?.length}
          <!-- How long HouseMind keeps what, short to long (E-409, B-335). -->
          <details class="keep">
            <summary>{t("ui.my_data.keep.title")}</summary>
            <ul class="plain">{#each snap.keepTerms as line (line)}<li>{line}</li>{/each}</ul>
          </details>
        {/if}
        <a class="button" href="api/my-data" download>{t("ui.my_data.file")}</a>
        {#if forgetting}
          <div class="confirm" role="group" aria-label={t("ui.my_data.forget")}>
            <p>{t("ui.my_data.forget_confirm")}</p>
            <button class="primary" onclick={() => { send({ type: "forget_me", yes: true }); forgetting = false; }}>{t("ui.yes")}</button>
            <button onclick={() => (forgetting = false)}>{t("ui.no")}</button>
          </div>
        {:else}
          <button onclick={() => (forgetting = true)}>{t("ui.my_data.forget")}</button>
        {/if}
      </section>
    {:else if key === "device"}
      <!-- Colour schemes (E-266, E-267): the files that passed the check, for this device only. -->
      <section data-section="scheme">
        <h3>{t("ui.prefs.scheme")}</h3>
        <div class="schemes" role="group" aria-label={t("ui.prefs.scheme")}>
          <button class="scheme" aria-pressed={scheme === "auto"} onclick={() => device.set("scheme", "auto")}>
            <span class="swatch split" aria-hidden="true"><span style:background={dark?.colours.inkt}></span><span style:background={light?.colours.inkt}></span></span>
            <span class="words"><strong>{t("ui.prefs.scheme_auto")}</strong><span>{t("ui.prefs.scheme_auto_hint")}</span></span>
          </button>
          {#each snap.themes ?? [] as s (s.id)}
            <button class="scheme" aria-pressed={scheme === s.id} onclick={() => device.set("scheme", s.id)}>
              <span class="swatch" style:background={s.colours.inkt} aria-hidden="true">
                <span class="card" style:background={s.colours.vlak} style:color={s.colours.tekst} style:border-color={s.colours.rand}>Aa</span>
                <span class="dot" style:background={s.colours.accent}></span>
              </span>
              <span class="words"><strong>{s.name}</strong><span>{s.hint}</span></span>
            </button>
          {/each}
        </div>
        <p class="muted">{t("ui.prefs.scheme_hint")}</p>
      </section>

      <section data-section="display">
        <h3>{t("ui.prefs.display")}</h3>
        <label class="field">{t("ui.prefs.text_size")}
          <select value={textSize} onchange={(e) => device.set("textSize", e.currentTarget.value as "normal" | "large")}>
            {#each ["normal", "large"] as v (v)}<option value={v}>{t(`ui.prefs.text_${v}`)}</option>{/each}
          </select>
        </label>
      </section>

      {#if snap.vesta}
        <section data-section="voice">
          <h3>{t("ui.prefs.voice")}</h3>
          <label class="check"><input type="checkbox" checked={aloud} onchange={(e) => setAloud(e.currentTarget.checked)} /> {t("ui.prefs.voice_aloud", { name: snap.vesta.name })}</label>
          <p class="muted">{t("ui.prefs.voice_hint", { name: snap.vesta.name })}</p>
          {#if !canListen()}<p class="muted">{t("ui.prefs.voice_no_mic")}</p>{/if}
        </section>
      {/if}

      <section data-section="tablet">
        <h3>{t("ui.tablet.title")}</h3>
        <label class="field">{t("ui.tablet.use")}
          <select value="" onchange={(e) => { const area = e.currentTarget.value; if (area) { setTabletArea(area); location.reload(); } }}>
            <option value="">{t("ui.tablet.none")}</option>
            {#each snap.areas as area (area.id)}<option value={area.id}>{area.name}</option>{/each}
          </select>
        </label>
        <label class="check"><input type="checkbox" checked={kiosk} onchange={(e) => setKioskWanted(e.currentTarget.checked)} /> {t("ui.tablet.kiosk")}</label>
        <p class="muted">{t("ui.tablet.hint")}</p>
      </section>
      <p class="muted">{t("ui.prefs.device_hint")}</p>
    {/if}
  {/snippet}
</SettingsGroups>
<!-- No group Hulp (QA 3, schermen 7): the question mark and the compass are on every screen;
     Over HouseMind is one link at the foot. -->
<button class="about" data-section="about" onclick={onabout}>{t("ui.about")}</button>

<style>
  section { display: flex; flex-direction: column; gap: 8px; }
  h3 { margin: 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  .levels { display: flex; flex-direction: column; gap: 6px; }
  .level { text-align: left; padding: 10px 14px; border-radius: 14px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; display: flex; flex-direction: column; gap: 2px; min-height: 48px; }
  .level[aria-pressed="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .level span { color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .seg { display: flex; gap: 6px; }
  .seg button { flex: 1; min-height: 44px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); color: var(--muted); cursor: pointer; }
  .seg button[aria-pressed="true"] { color: var(--text); border-color: var(--accent); }
  .check { display: flex; gap: 10px; align-items: center; min-height: 44px; }
  .check input { width: 22px; height: 22px; accent-color: var(--accent); }
  .field { display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .quiet { border: 1px solid var(--line); border-radius: 14px; padding: 8px 12px 12px; margin: 0; display: flex; flex-direction: column; gap: 8px; }
  .quiet legend { color: var(--muted); font-size: calc(14px * var(--text-scale)); padding: 0 4px; }
  #danger-label { margin: 0; }
  select, input[type="number"], input[type="time"] { min-height: 44px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); padding: 0 12px; font: inherit; }
  .muted { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .about { align-self: flex-start; min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--edge); background: var(--raised); cursor: pointer; }
  .schemes { display: flex; flex-direction: column; gap: 6px; }
  .scheme { display: flex; align-items: center; gap: 12px; text-align: left; padding: 8px 12px; border-radius: 14px; border: 1px solid var(--line); background: var(--raised); color: var(--text); cursor: pointer; min-height: 56px; }
  .scheme[aria-pressed="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .words { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .words span { color: var(--muted); font-size: calc(13px * var(--text-scale)); }
  /* A small example of each scheme: its background, a card with text, and its accent. */
  .swatch { flex: none; position: relative; width: 56px; height: 40px; border-radius: 10px; border: 1px solid var(--line); overflow: hidden; display: flex; align-items: center; padding-left: 6px; }
  .swatch .card { width: 28px; height: 24px; border-radius: 6px; border: 1px solid; display: grid; place-items: center; font-size: calc(12px * var(--text-scale)); font-weight: 600; }
  .swatch .dot { position: absolute; right: 6px; bottom: 6px; width: 10px; height: 10px; border-radius: 50%; }
  .swatch.split { padding: 0; }
  .swatch.split span { flex: 1; height: 100%; }
  .button { display: inline-flex; align-items: center; align-self: flex-start; min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); color: inherit; text-decoration: none; }
  .confirm { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 10px 12px; border: 1px solid var(--line); border-radius: 12px; }
  .confirm p { flex-basis: 100%; margin: 0; }
  .primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 600; }
  .card { border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; background: var(--surface); }
  .card-title { margin: 0 0 6px; font-weight: 600; }
  .keep summary { cursor: pointer; min-height: 40px; display: flex; align-items: center; color: var(--accent); font-weight: 600; }
</style>

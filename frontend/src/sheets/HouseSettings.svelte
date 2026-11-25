<script lang="ts">
  // House settings for administrators (E-82): learning, how far HouseMind may go, pets, AI.
  // What you look at and correct is in Brein (E-523, B-448): Wat HouseMind van je huis weet,
  // the existing automations and the thresholds; this sheet keeps what you set.
  import IntegrationSettings from "./IntegrationSettings.svelte";
  import AISettings from "./AISettings.svelte";
  import VestaVoice from "./VestaVoice.svelte";
  import PartsList from "../components/PartsList.svelte";
  import SettingsGroups, { type SettingsGroup } from "../components/SettingsGroups.svelte";
  import HistoryProgress from "../components/HistoryProgress.svelte";
  import { historyLine } from "../lib/history";
  import type { EnergySettingsView, HouseSettings, IntegrationView, SchemeProblem } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    settings: HouseSettings | undefined;
    energy?: EnergySettingsView | null;
    preview: string | undefined;
    integration?: IntegrationView;
    muted?: { key: string; text: string }[]; // what the house switched off (E-412)
    level: number;
    lang: string; // numbers and the export of the long lists (E-325)
    t: T;
    send: (message: Record<string, unknown>) => void;
    get: (what: string) => void;
    onreadiness?: () => void; // "Bekijk hoe ver HouseMind is" (E-256)
    onknowledge?: () => void; // Wat HouseMind van je huis weet, in Brein (E-524)
    focus?: string | null; // a part to open, when Vesta offered it (E-244)
  }
  let { settings, energy = null, preview, integration, muted, level, lang, t, send, get, onreadiness, onknowledge, focus = null }: Props = $props();
  // What an administrator switched off for everyone with "Niet meer tonen" (E-412).
  $effect(() => get("muted"));
  // Five groups and a search (E-265, B-112).
  let query = $state("");
  // While the history is read, the bar moves (E-257).
  $effect(() => {
    if (settings?.history?.state !== "reading") return;
    const timer = setInterval(() => get("settings"), 3000);
    return () => clearInterval(timer);
  });

  // Leren en stappen keeps what you set (E-523): learning, the history and the parts. Een
  // eerdere dag is the lightest way to start again; care is about a resident.
  const groups: SettingsGroup[] = $derived([
    { key: "learning", title: t("ui.settings.group.learning"), hint: t("ui.settings.group.learning_hint"), sections: ["learning", "history", "max_step"] },
    { key: "vesta", title: settings?.vesta?.name ?? "Vesta", hint: t("ui.settings.group.vesta_hint"), sections: ["vesta", ...(level >= 4 ? ["vesta_window"] : []), "pipeline", "assist", "ai"] },
    { key: "house", title: t("ui.settings.group.house"), hint: t("ui.settings.group.house_hint"), sections: ["people", "experimental", "muted", "pets", "security"] },
    ...(energy ? [{ key: "energy", title: t("ui.settings.group.energy"), hint: t("ui.settings.group.energy_hint"), sections: ["energy"] }] : []),
    { key: "upkeep", title: t("ui.settings.group.upkeep"), hint: t("ui.settings.group.upkeep_hint"), sections: ["upkeep", ...(level >= 4 ? ["settings_file"] : []), "themes", "integration"] },
    // Starting again, from light to heavy (E-271, B-121).
    { key: "restart", title: t("ui.settings.group.restart"), hint: t(level >= 4 ? "ui.settings.group.restart_hint_expert" : "ui.settings.group.restart_hint"), sections: [...(level >= 4 ? ["snapshots"] : []), "intro_again", "learn_again", "factory", "remove"] },
  ]);
  /** Why a colour scheme file is left out, in plain words (E-267). */
  function why(problem: SchemeProblem): string {
    const role = (r: string | null) => (r ? t(`ui.settings.themes.role.${r}`) : "");
    const number = (n: number | null) => (n ?? 0).toLocaleString(document.documentElement.lang || undefined, { maximumFractionDigits: 2 });
    return t(`ui.settings.themes.problem.${problem.code}`, {
      role: role(problem.role), on: role(problem.on), ratio: number(problem.ratio), need: number(problem.need), value: problem.value ?? "",
    });
  }
  /** What went to the AI, readable: the data block as indented JSON when it is JSON. */
  const energySet = (key: string, value: unknown) => {
    send({ type: "energy_setting", [key]: value });
    setTimeout(() => get("energy_settings"), 300);
  };
  let confirmRemove = $state(false);
  let forgetting = $state<string | null>(null); // someone who left HA (QA 2, M-9)
  // An earlier day (20.13), on Expert, at Opnieuw beginnen (E-523).
  let confirmSnapshot = $state(false);
  // Reading a settings file back in (E-296): first the list of what changes, then a yes.
  type FilePreview = { ok: boolean; reason?: string; changes: { settings: string[]; house_rules: number; scenes: number; area_roles: number }; skipped: string[] };
  let fileData = $state<unknown>(null);
  let filePreview = $state<FilePreview | null>(null);
  let fileError = $state("");
  async function post(apply: boolean): Promise<FilePreview> {
    const response = await fetch("api/settings-file", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ apply, file: fileData }) });
    return (await response.json()) as FilePreview;
  }
  async function readFile(e: Event): Promise<void> {
    fileError = "";
    filePreview = null;
    const file = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!file) return;
    try {
      fileData = JSON.parse(await file.text());
    } catch {
      fileError = t("ui.settings.file.refused.format");
      return;
    }
    const shown = await post(false);
    if (shown.ok) filePreview = shown;
    else fileError = t(`ui.settings.file.refused.${shown.reason ?? "format"}`);
  }
  async function applyFile(): Promise<void> {
    const done = await post(true);
    filePreview = null;
    fileData = null;
    fileError = done.ok ? "" : t(`ui.settings.file.refused.${done.reason ?? "format"}`);
    if (done.ok) send({ type: "get", what: "settings" });
  }
  let snapshotDay = $state("");
  $effect(() => {
    if (!snapshotDay && settings?.snapshots?.length) snapshotDay = settings.snapshots[0];
  });
  // Starting again (E-272 – E-274): learning again after a yes; the factory settings only after
  // the list of what goes and the typed word.
  let jump = $state<string | null>(null);
  let confirmLearn = $state(false);
  let confirmFactory = $state(false);
  let typed = $state("");
  const word = $derived(t("ui.restart.factory.word"));
  const wordOk = $derived(typed.trim().toLocaleUpperCase() === word.toLocaleUpperCase());
  const herName = $derived(settings?.vesta?.name ?? "Vesta");
  const setting = (k: string, value: unknown) => send({ type: "house_setting", key: k, value });


</script>

{#if !settings}
  <p class="muted">…</p>
{:else}
  <input class="search" type="search" bind:value={query} placeholder={t("ui.settings.search")} aria-label={t("ui.settings.search")} />
  <SettingsGroups {groups} focus={jump ?? focus} {query} back={`← ${t("ui.house_settings")}`} none={t("ui.settings.search_none")}>
    {#snippet content(group: string)}
      {#if group === "learning"}
        <section data-section="learning">
          <h3>{t("ui.settings.learning")}</h3>
          <label class="check">
            <input type="checkbox" checked={settings.consentLearning} onchange={(e) => { setting("consent_learning", e.currentTarget.checked); setTimeout(() => get("settings"), 300); }} />
            {t("ui.settings.learning_consent")}
          </label>
          <p class="muted">{t("ui.settings.learning_note")}</p>
          <p class="muted">{t("ui.restart.pointer")}</p>
          <button class="link" onclick={() => (jump = "learn_again")}>{t("ui.restart.go")}</button>
        </section>

        <!-- Learning from the history in Home Assistant (E-257): read only, never to an AI. -->
        <section data-section="history">
          <h3>{t("ui.history.title")}</h3>
          <label class="check">
            <input type="checkbox" checked={settings.historyLearning !== false} onchange={(e) => { setting("history_learning", e.currentTarget.checked); setTimeout(() => get("settings"), 300); }} />
            {t("ui.history.switch")}
          </label>
          {#if settings.history?.available}<p class="muted">{t("ui.history.keeps", { days: settings.history.available })}</p>{/if}
          <HistoryProgress view={settings.history} {t} />
          {#if settings.history?.state !== "reading" && historyLine(settings.history, t)}<p class="muted">{historyLine(settings.history, t)}</p>{/if}
          {#if settings.consentLearning && settings.historyLearning !== false && settings.history?.state !== "reading"}
            <button onclick={() => { send({ type: "history_again" }); setTimeout(() => get("settings"), 500); }}>{t("ui.history.again")}</button>
          {/if}
          <p class="muted">{t("ui.history.note")}</p>
        </section>

        <section data-section="max_step">
          <!-- One list of parts (E-399): it replaces how far HouseMind may go and the
               experimental ticks; off for a core part only on Expert. -->
          <h3>{t("ui.parts.title")}</h3>
          <PartsList parts={settings.parts ?? []} expert={level >= 4} {lang} {t}
            onchange={(part, state) => { setting("parts", { [part]: state }); setTimeout(() => get("settings"), 300); }} />
          {#if level >= 3}
            <!-- What you look at and correct is in Brein (E-523, B-448). -->
            <div class="to-brain">
              <p class="muted">{t("ui.settings.to_brain")}</p>
              {#if onreadiness}<button class="link" onclick={onreadiness}>{t("ui.settings.to_readiness")} →</button>{/if}
              {#if onknowledge}<button class="link" onclick={onknowledge}>{t("ui.knowledge.title")} →</button>{/if}
            </div>
          {:else if onreadiness}
            <button class="link" onclick={onreadiness}>{t("ui.ready.show")}</button>
          {/if}
        </section>

      {:else if group === "vesta"}
        <VestaVoice {settings} {t} {send} {get} houseLanguage />
        {#if level >= 4}
          <!-- Her window with a question, on or off for everyone in the house (E-450, B-364). -->
          <section data-section="vesta_window">
            <label class="check"><input type="checkbox" checked={settings.vestaWindow ?? true}
              onchange={(e) => { setting("vesta_window", e.currentTarget.checked); setTimeout(() => get("settings"), 300); }} />
              {t("ui.settings.vesta_window", { name: herName })}</label>
            <p class="muted">{t("ui.settings.vesta_window_hint", { name: herName })}</p>
          </section>
        {/if}
        <IntegrationSettings info={integration} part="pipeline" name={settings.vesta?.name ?? "Vesta"} {t} {send} />
        <IntegrationSettings info={integration} part="assist" name={settings.vesta?.name ?? "Vesta"} {t} {send} />
        <AISettings {settings} {preview} {level} {t} {send} {get} />
      {:else if group === "house"}
        <section data-section="people">
          <h3>{t("ui.settings.people")}</h3>
          <label class="field">{t("ui.settings.new_level")}
            <select value={settings.newPersonLevel ?? 1} onchange={(e) => { setting("new_person_level", Number(e.currentTarget.value)); setTimeout(() => get("settings"), 300); }}>
              {#each [1, 2, 3, 4] as n (n)}<option value={n}>{t(`levels.${["carefree", "informed", "involved", "expert"][n - 1]}`)}</option>{/each}
            </select>
          </label>
          <p class="muted">{t("ui.settings.new_level_note")}</p>
          <!-- No higher than this for anyone who is not an administrator (QA 3, gat 3). -->
          <label class="field">{t("ui.settings.resident_top_level")}
            <select value={settings.residentTopLevel ?? 3} onchange={(e) => { setting("resident_top_level", Number(e.currentTarget.value)); setTimeout(() => get("settings"), 300); }}>
              {#each [1, 2, 3, 4] as n (n)}<option value={n}>{t(`levels.${["carefree", "informed", "involved", "expert"][n - 1]}`)}</option>{/each}
            </select>
          </label>
          <p class="muted">{t("ui.settings.resident_top_level_note")}</p>
          <label class="check"><input type="checkbox" checked={settings.phonelessResidents ?? false}
            onchange={(e) => { setting("phoneless_residents", e.currentTarget.checked); setTimeout(() => get("settings"), 300); }} />
            {t("ui.settings.phoneless")}</label>
          <p class="muted">{t("ui.settings.phoneless_note")}</p>
          {#if settings.phonelessResidents}
            <!-- Their first names, only so that they never reach an AI (QA 2, B-174). -->
            <label class="field">{t("ui.settings.private_names")}
              <input type="text" value={(settings.privateNames ?? []).join(", ")} autocomplete="off"
                onchange={(e) => { setting("private_names", e.currentTarget.value.split(",").map((n) => n.trim()).filter(Boolean)); setTimeout(() => get("settings"), 300); }} />
            </label>
            <p class="muted">{t("ui.settings.private_names_note")}{#if settings.namesInRooms?.length} {t("ui.settings.private_names_found", { names: settings.namesInRooms.join(", ") })}{/if}</p>
          {/if}
          <label class="check"><input type="checkbox" checked={settings.livedIn ?? true}
            onchange={(e) => { setting("lived_in", e.currentTarget.checked); setTimeout(() => get("settings"), 300); }} />
            {t("ui.settings.lived_in.label")}</label>
          <p class="muted">{t("ui.settings.lived_in.note")}</p>
          {#if settings.departed?.length}
            <!-- Someone who left Home Assistant: forget them, after a yes (QA 2, M-9). -->
            <p class="name">{t("ui.settings.departed")}</p>
            {#each settings.departed as person (person.id)}
              {#if forgetting === person.id}
                <p>{t("ui.settings.departed_confirm", { name: person.name })}</p>
                <div class="row">
                  <button class="primary" onclick={() => { send({ type: "forget_person", id: person.id }); forgetting = null; setTimeout(() => get("settings"), 400); }}>{t("ui.yes")}</button>
                  <button onclick={() => (forgetting = null)}>{t("ui.no")}</button>
                </div>
              {:else}
                <button class="link" onclick={() => (forgetting = person.id)}>{t("ui.settings.departed_forget", { name: person.name })}</button>
              {/if}
            {/each}
            <p class="muted">{t("ui.settings.departed_note")}</p>
          {/if}
        </section>
        <!-- Care and wellbeing, once it is on in the list of parts (E-399): about a resident, so at Huis
             (E-523). -->
        <section data-section="experimental">
          {#if settings.experimental.includes("care")}
            {@const care = settings.care ?? { person: "", contact: "", consent: false }}
            <h3>{t("ui.domain.care")}</h3>
            <p>{t("ui.settings.care.text")}</p>
            <label class="field">{t("ui.settings.care.person")}
              <select value={care.person} onchange={(e) => { setting("care", { ...care, person: e.currentTarget.value }); setTimeout(() => get("settings"), 300); }}>
                <option value="">{t("ui.settings.care.choose")}</option>
                {#each settings.persons as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
              </select>
            </label>
            <label class="field">{t("ui.settings.care.contact")}
              <select value={care.contact} onchange={(e) => { setting("care", { ...care, contact: e.currentTarget.value }); setTimeout(() => get("settings"), 300); }}>
                <option value="">{t("ui.settings.care.choose")}</option>
                {#each settings.contacts as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
              </select>
            </label>
            {#if care.ownAccount}
              <!-- They have an account: they say yes themselves (QA 2, M-8). -->
              <p class="muted">{t(care.agreed ? "ui.settings.care.agreed" : "ui.settings.care.waiting")}</p>
            {:else if care.person}
              <label class="check"><input type="checkbox" checked={care.consent}
                onchange={(e) => { setting("care", { ...care, consent: e.currentTarget.checked }); setTimeout(() => get("settings"), 300); }} />
                {t("ui.settings.care.consent")}</label>
            {/if}
          {/if}
        </section>
        <section data-section="muted">
          <h3>{t("ui.notice.muted_title")}</h3>
          <p class="muted">{t("ui.notice.muted_hint")}</p>
          {#if muted === undefined}
            <p class="muted" aria-busy="true">…</p>
          {:else if muted.length === 0}
            <p class="muted">{t("ui.notice.muted_none")}</p>
          {:else}
            <ul class="muted-list">
              {#each muted as m (m.key)}
                <li><span>{m.text}</span><button onclick={() => send({ type: "unmute", key: m.key })}>{t("ui.notice.show_again")}</button></li>
              {/each}
            </ul>
          {/if}
        </section>
        <section data-section="pets">
          <h3>{t("ui.settings.pets")}</h3>
          <p class="muted">{t("ui.settings.pets_note")}</p>
          <div class="areas">
            {#each settings.areas as area (area.id)}
              <label class="check small">
                <input type="checkbox" checked={settings.petAreas.includes(area.id)}
                  onchange={(e) => { const set = new Set(settings.petAreas); if (e.currentTarget.checked) set.add(area.id); else set.delete(area.id); setting("pet_areas", [...set]); setTimeout(() => get("settings"), 300); }} />
                {area.name}
              </label>
            {/each}
          </div>
        </section>
        {#if settings.security && (settings.security.alarms.length || settings.security.cameras || settings.security.hasAlarm || settings.security.fireCovers?.length)}
          {@const security = settings.security}
          <!-- Which alarm is "the alarm" (B-67), and the sounds that only count after a choice:
               a crying baby with consent, the others from level 4 (16.2). -->
          <section data-section="security">
            <h3>{t("ui.settings.security.title")}</h3>
            {#if security.alarms.length}
              <label class="field">{t("ui.settings.security.alarm")}
                <!-- Without a choice: the only alarm in use, by itself (E-311). -->
                <select value={security.alarm ?? ""} onchange={(e) => { setting("alarm_entity", e.currentTarget.value || null); setTimeout(() => get("settings"), 300); }}>
                  <option value="">{security.auto ? t("ui.settings.security.alarm_auto", { name: security.alarms.find((a) => a.id === security.auto)?.name ?? "" }) : t("ui.settings.security.alarm_none")}</option>
                  {#each security.alarms as alarm (alarm.id)}<option value={alarm.id}>{alarm.name}{alarm.inUse ? ` – ${t("ui.settings.security.in_use")}` : ""}</option>{/each}
                </select>
              </label>
              <p class="muted">{t("ui.settings.security.alarm_note")}</p>
            {/if}
            {#if security.hasAlarm && security.selfArm && security.selfCan}
              <!-- The alarm on by itself (E-540): the administrator's standing yes, per moment. -->
              <p>{t("ui.settings.security.self")}</p>
              <p class="muted">{t("ui.settings.security.self_note")}</p>
              <div class="areas">
                {#each ["away", "night"] as const as moment (moment)}
                  <label class="check small">
                    <input type="checkbox" checked={security.selfArm[moment]} disabled={!security.selfCan[moment] && !security.selfArm[moment]}
                      onchange={(e) => { setting("alarm_self", { [moment]: e.currentTarget.checked }); setTimeout(() => get("settings"), 300); }} />
                    {t(`ui.settings.security.self_${moment}`)}
                  </label>
                {/each}
              </div>
              {#if !security.selfCan.away && !security.selfCan.night}<p class="muted">{t("ui.settings.security.self_code")}</p>{/if}
            {/if}
            {#if security.hasAlarm && security.coverings.length}
              <!-- What opens when the alarm goes off: only what an administrator chose (B-170). -->
              <p>{t("ui.settings.security.open_covers")}</p>
              <p class="muted">{t("ui.settings.security.open_covers_note")}</p>
              <div class="areas">
                {#each security.coverings as cover (cover.id)}
                  <label class="check small">
                    <input type="checkbox" checked={security.open.includes(cover.id)}
                      onchange={(e) => { const set = new Set(security.open); if (e.currentTarget.checked) set.add(cover.id); else set.delete(cover.id); setting("alarm_open_covers", [...set]); setTimeout(() => get("settings"), 300); }} />
                    {cover.name}{cover.area ? ` · ${cover.area}` : ""}
                  </label>
                {/each}
              </div>
            {/if}
            {#if security.hasAlarm && security.signalLamps?.length}
              <!-- Signal lamps at a break-in (E-543): red, flashing; nothing until chosen. -->
              <p>{t("ui.settings.security.signal")}</p>
              <p class="muted">{t("ui.settings.security.signal_note")}</p>
              <div class="areas">
                {#each security.signalLamps as lamp (lamp.id)}
                  <label class="check small">
                    <input type="checkbox" checked={(security.signal ?? []).includes(lamp.id)}
                      onchange={(e) => { const set = new Set(security.signal ?? []); if (e.currentTarget.checked) set.add(lamp.id); else set.delete(lamp.id); setting("alarm_signal_lamps", [...set]); setTimeout(() => get("settings"), 300); }} />
                    {lamp.name}{lamp.area ? ` · ${lamp.area}` : ""}{#if lamp.suggested && !(security.signal ?? []).length}<span class="muted"> · {t("ui.settings.security.suggested")}</span>{/if}
                  </label>
                {/each}
              </div>
            {/if}
            {#if security.fireCovers?.length}
              <!-- What goes up at a fire, to get out (E-546): nothing until chosen. -->
              <p>{t("ui.settings.security.fire_covers")}</p>
              <p class="muted">{t("ui.settings.security.fire_covers_note")}</p>
              <div class="areas">
                {#each security.fireCovers as cover (cover.id)}
                  <label class="check small">
                    <input type="checkbox" checked={(security.fireOpen ?? []).includes(cover.id)}
                      onchange={(e) => { const set = new Set(security.fireOpen ?? []); if (e.currentTarget.checked) set.add(cover.id); else set.delete(cover.id); setting("fire_open_covers", [...set]); setTimeout(() => get("settings"), 300); }} />
                    {cover.name}{cover.area ? ` · ${cover.area}` : ""}
                  </label>
                {/each}
              </div>
            {/if}
            {#if security.cameras}
              <p>{t("ui.settings.security.sounds")}</p>
              <p class="muted">{t("ui.settings.security.sounds_note")}</p>
              <div class="areas">
                {#each security.sounds.filter((s) => s === "baby" || level >= 4) as sound (sound)}
                  <label class="check small">
                    <input type="checkbox" checked={security.chosen.includes(sound)}
                      onchange={(e) => { const set = new Set(security.chosen); if (e.currentTarget.checked) set.add(sound); else set.delete(sound); setting("camera_sounds", [...set]); setTimeout(() => get("settings"), 300); }} />
                    {t(`ui.camera.kind.${sound}`)}
                  </label>
                {/each}
              </div>
              <p class="muted">{t("ui.settings.security.baby_note")}</p>
            {/if}
          </section>
        {/if}
      {:else if group === "energy"}
        {#if energy}
          <section data-section="energy">
            <h3>{t("ui.energy.settings")}</h3>
            <label class="field">{t("ui.energy.goal")}
              <select value={energy.goal} onchange={(e) => energySet("goal", e.currentTarget.value)}>
                {#each ["cost", "own_power", "co2", "mix"] as g (g)}<option value={g}>{t(`ui.energy.goals.${g}`)}</option>{/each}
              </select>
            </label>
            <p class="muted">{t("ui.energy.goal_hint")}</p>
            <label class="field">{t("ui.energy.price_mode")}
              <select value={energy.priceMode} onchange={(e) => energySet("priceMode", e.currentTarget.value)}>
                {#each ["auto", "market", "all_in"] as m (m)}<option value={m}>{t(`ui.energy.price_modes.${m}`)}</option>{/each}
              </select>
            </label>
            <label class="field">{t("ui.energy.peak")}<input type="number" min="1" max="50" step="0.5" value={energy.peakLimitKw ?? ""} onchange={(e) => energySet("peakLimitKw", e.currentTarget.value === "" ? null : Number(e.currentTarget.value))} /></label>
          </section>
        {/if}
      {:else if group === "upkeep"}
        <section data-section="upkeep">
          <h3>{t("ui.settings.upkeep.title")}</h3>
          <!-- Fetching knowledge only once bundles are published: before that the tick did nothing (QA 3, schermen 19). -->
          {#if settings.updatesPublished}
            <label class="check"><input type="checkbox" checked={settings.knowledgeUpdates}
              onchange={(e) => { setting("knowledge_updates", e.currentTarget.checked); setTimeout(() => get("settings"), 300); }} />
              {t("ui.settings.upkeep.knowledge")}</label>
            <p class="muted">{settings.knowledgeVersion ? t("ui.settings.upkeep.knowledge_version", { version: settings.knowledgeVersion }) : t("ui.settings.upkeep.knowledge_none")}</p>
          {/if}
          <label class="check"><input type="checkbox" checked={settings.calendar ?? false}
            onchange={(e) => { setting("calendar", e.currentTarget.checked); setTimeout(() => get("settings"), 300); }} />
            {t("ui.settings.calendar.label")}</label>
          <p class="muted">{t("ui.settings.calendar.note")}</p>
          {#if level >= 3}
            <a class="button" href="api/diagnostics" download="housemind-diagnostics.json">{t("ui.settings.upkeep.diagnostics")}</a>
            <p class="muted">{t("ui.settings.upkeep.diagnostics_hint")}</p>
          {/if}
        </section>

        <!-- Colour schemes as files (E-267): which are in the list, and why a file is left out. -->
        {#if level >= 4}
          <!-- One settings file to save and to read back in (E-296): first what changes. -->
          <section data-section="settings_file">
            <h3>{t("ui.settings.file.title")}</h3>
            <p class="muted">{t("ui.settings.file.note")}</p>
            <a class="button" href="api/export" download>{t("ui.settings.file.save")}</a>
            <label class="field">{t("ui.settings.file.read")}
              <input type="file" accept="application/json,.json" onchange={readFile} />
            </label>
            {#if fileError}<p class="error" role="alert">{fileError}</p>{/if}
            {#if filePreview}
              <div class="confirm">
                <p>{t("ui.settings.file.changes")}</p>
                <ul class="plain">
                  {#if filePreview.changes.settings.length}<li>{t("ui.settings.file.settings", { count: filePreview.changes.settings.length })}</li>{/if}
                  <li>{t("ui.settings.file.rules", { count: filePreview.changes.house_rules })}</li>
                  <li>{t("ui.settings.file.scenes", { count: filePreview.changes.scenes })}</li>
                  <li>{t("ui.settings.file.roles", { count: filePreview.changes.area_roles })}</li>
                  {#if filePreview.skipped.length}<li>{t("ui.settings.file.skipped", { names: filePreview.skipped.join(", ") })}</li>{/if}
                </ul>
                <button class="primary" onclick={() => applyFile()}>{t("ui.yes")}</button>
                <button onclick={() => { filePreview = null; fileData = null; }}>{t("ui.no")}</button>
              </div>
            {/if}
          </section>
        {/if}
        <section data-section="themes">
          <h3>{t("ui.settings.themes.title")}</h3>
          <p class="muted">{t("ui.settings.themes.text")}</p>
          {#if settings.themes}
            <!-- Only your own files and what was left out (QA 3, schermen 20): the shipped
                 schemes are in Mijn voorkeuren already. -->
            {#if settings.themes.ok.some((s) => s.own)}
              <ul class="plain">
                {#each settings.themes.ok.filter((s) => s.own) as scheme (scheme.id)}
                  <li>{scheme.name}{scheme.level === "AAA" ? " · AAA" : ""}</li>
                {/each}
              </ul>
            {/if}
            {#if settings.themes.problems.length}
              <p>{t("ui.settings.themes.left_out")}</p>
              <ul class="plain problems">
                {#each settings.themes.problems as problem, i (i)}
                  <li><span class="mono">{problem.file}</span> · {why(problem)}</li>
                {/each}
              </ul>
            {/if}
            {#if settings.themes.example}
              <details>
                <summary>{t("ui.settings.themes.example")}</summary>
                <pre>{settings.themes.example}</pre>
              </details>
            {/if}
          {/if}
        </section>
        <IntegrationSettings info={integration} part="link" name={settings.vesta?.name ?? "Vesta"} {t} {send} />
      {:else if group === "restart"}
        {#if level >= 4}
          <!-- What was learned, kept every night; one day back after a yes (20.13). -->
          <section data-section="snapshots">
            <h3>{t("ui.settings.snapshots.title")}</h3>
            <p class="muted">{t("ui.settings.snapshots.note")}</p>
            {#if !settings.snapshots?.length}
              <p class="muted">{t("ui.settings.snapshots.none")}</p>
            {:else}
              <label class="field">{t("ui.settings.snapshots.day")}
                <select bind:value={snapshotDay}>
                  {#each settings.snapshots as day (day)}<option value={day}>{day}</option>{/each}
                </select>
              </label>
              {#if confirmSnapshot}
                <div class="confirm">
                  <p>{t("ui.settings.snapshots.confirm", { day: snapshotDay })}</p>
                  <button class="primary" onclick={() => { send({ type: "snapshot_restore", day: snapshotDay, yes: true }); confirmSnapshot = false; setTimeout(() => get("settings"), 500); }}>{t("ui.yes")}</button>
                  <button onclick={() => (confirmSnapshot = false)}>{t("ui.no")}</button>
                </div>
              {:else}
                <button onclick={() => (confirmSnapshot = true)} disabled={!snapshotDay}>{t("ui.settings.snapshots.button")}</button>
              {/if}
            {/if}
          </section>
        {/if}
        <section data-section="intro_again">
          <h3>{t("ui.restart.intro.title")}</h3>
          <p class="muted">{t("ui.restart.intro.text", { name: herName })}</p>
          <button onclick={() => send({ type: "intro_again" })}>{t("ui.restart.intro.button")}</button>
        </section>
        <section data-section="learn_again">
          <h3>{t("ui.restart.learn.title")}</h3>
          <p class="muted">{t("ui.restart.learn.text")}</p>
          {#if confirmLearn}
            <p>{t("ui.restart.learn.gone")}</p>
            {#if settings.historyLearning}<p class="muted">{t("ui.restart.learn.history")}</p>{/if}
            <p>{t("ui.restart.learn.confirm")}</p>
            <div class="row">
              <button class="primary" onclick={() => { send({ type: "relearn", yes: true }); confirmLearn = false; setTimeout(() => get("settings"), 300); }}>{t("ui.restart.learn.confirm_yes")}</button>
              <button onclick={() => (confirmLearn = false)}>{t("ui.no")}</button>
            </div>
          {:else}
            <button onclick={() => (confirmLearn = true)}>{t("ui.restart.learn.button")}</button>
          {/if}
        </section>
        <section data-section="factory">
          <h3>{t("ui.restart.factory.title")}</h3>
          <p class="muted">{t("ui.restart.factory.text")}</p>
          {#if confirmFactory}
            <p>{t("ui.restart.factory.first")}</p>
            <p><strong>{t("ui.restart.factory.gone_title")}</strong></p>
            <ul class="gone">
              <li>{t("ui.restart.factory.gone.learned")}</li>
              <li>{t("ui.restart.factory.gone.logbook")}</li>
              <li>{t("ui.restart.factory.gone.rules")}</li>
              <li>{t("ui.restart.factory.gone.settings")}</li>
              <li>{t("ui.restart.factory.gone.ai")}</li>
              <li>{t("ui.restart.factory.gone.link", { name: herName })}</li>
            </ul>
            <p class="muted">{t("ui.restart.factory.stays")}</p>
            <a class="button" href="api/export" download>{t("ui.restart.factory.copy")}</a>
            <p class="muted">{t("ui.restart.factory.backup")}</p>
            <label class="field">{t("ui.restart.factory.type", { word })}
              <input bind:value={typed} aria-label={t("ui.restart.factory.type", { word })} autocomplete="off" autocapitalize="characters" spellcheck="false" />
            </label>
            <div class="row">
              <button class="danger" disabled={!wordOk} onclick={() => { send({ type: "factory_reset", word: typed }); confirmFactory = false; typed = ""; }}>{t("ui.restart.factory.erase")}</button>
              <button onclick={() => { confirmFactory = false; typed = ""; }}>{t("ui.restart.cancel")}</button>
            </div>
          {:else}
            <button onclick={() => (confirmFactory = true)}>{t("ui.restart.factory.button")}</button>
          {/if}
        </section>
        <section data-section="remove">
          <h3>{t("ui.settings.remove")}</h3>
          {#if settings.givenBack}
            <p>{t("ui.settings.remove_done")}</p>
            <button onclick={() => { send({ type: "give_back", yes: false }); setTimeout(() => get("settings"), 300); }}>{t("ui.settings.remove_undo")}</button>
          {:else if confirmRemove}
            <p>{t("ui.settings.remove_confirm")}</p>
            <div class="row">
              <button class="primary" onclick={() => { send({ type: "give_back", yes: true }); confirmRemove = false; setTimeout(() => get("settings"), 300); }}>{t("ui.yes")}</button>
              <button onclick={() => (confirmRemove = false)}>{t("ui.no")}</button>
            </div>
          {:else}
            <p class="muted">{t("ui.settings.remove_text")}</p>
            <button onclick={() => (confirmRemove = true)}>{t("ui.settings.remove_button")}</button>
          {/if}
        </section>
      {/if}
    {/snippet}
  </SettingsGroups>
{/if}

<style>
  details > summary { cursor: pointer; min-height: 40px; display: flex; align-items: center; gap: 6px; color: var(--accent); font-weight: 600; }
  .mono { font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); }
  .button { display: inline-flex; align-items: center; min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--line); color: inherit; text-decoration: none; }
  section { display: flex; flex-direction: column; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
  h3 { margin: 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  .muted { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .gone { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; }
  button.danger { background: var(--alert); border-color: var(--alert); color: var(--on-alert); font-weight: 600; }
  button.danger:disabled { opacity: 0.45; cursor: not-allowed; }
  button.link { align-self: flex-start; background: none; border: none; padding: 0; min-height: 40px; color: var(--accent); font-weight: 600; cursor: pointer; }
  .check { display: flex; gap: 10px; align-items: center; min-height: 44px; }
  .check.small { min-height: 36px; }
  .check input { width: 22px; height: 22px; accent-color: var(--accent); }
  .areas { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
  .field { display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  select, input:not([type="checkbox"]) { min-height: 44px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); padding: 0 12px; font: inherit; }
  button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; }
  .primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 600; }
  .row { display: flex; gap: 8px; }
  .error { margin: 0; color: var(--alert); }
  .link { border: none; background: none; color: var(--accent); font-weight: 600; cursor: pointer; min-height: 44px; padding: 0; font: inherit; }
  .plain { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .problems li { color: var(--important); }
  .to-brain { display: flex; flex-direction: column; align-items: flex-start; gap: 0; padding: 10px 14px; border-radius: 14px; background: var(--raised); }
  .search { width: 100%; min-height: 44px; border-radius: 22px; border: 1px solid var(--line); background: var(--surface); color: var(--text); padding: 0 16px; font: inherit; }
  .name { font-weight: 600; }
  section > p { margin: 0; }
  pre { margin: 0; max-height: 220px; overflow: auto; font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); background: var(--ink); padding: 10px; border-radius: 10px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .muted-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .muted-list li { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 12px; }
  .muted-list button { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--line); background: var(--raised); color: var(--text); cursor: pointer; flex: none; }
</style>

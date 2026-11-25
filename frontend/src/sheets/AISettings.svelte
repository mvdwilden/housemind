<script lang="ts">
  // The AI (E-160 – E-167): provider, key, models and budget, the consent with an example of
  // what goes out, and on Expert the log – in House settings and when setting up (E-278).
  import type { HouseSettings } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    settings: HouseSettings;
    preview: string | undefined;
    level: number;
    t: T;
    send: (message: Record<string, unknown>) => void;
    get: (what: string) => void;
  }
  let { settings, preview, level, t, send, get }: Props = $props();
  const PROVIDERS = ["anthropic", "gemini", "openai", "openrouter", "ollama", "openai_compatible", "home_assistant"];
  let ai = $state<HouseSettings["ai"] | null>(null);
  $effect(() => {
    if (settings && ai === null) ai = { ...settings.ai };
  });
  // The AI task chosen in Home Assistant, and where it works (E-422).
  const task = $derived(
    ai?.provider === "home_assistant" ? settings.ai.aiTasks?.find((x) => x.id === ai?.baseUrl) : undefined,
  );
  let key = $state("");
  function saveAi(event: SubmitEvent): void {
    event.preventDefault();
    if (!ai) return;
    send({ type: "ai_config", config: ai, key: key || undefined });
    key = "";
    setTimeout(() => get("settings"), 300);
  }
</script>

{#if ai}
  <section data-section="ai">
    <h3>{t("ui.settings.ai")}</h3>
    <p class="muted">{t(`ai.status.${settings.ai.status}`)}</p>
    <form onsubmit={saveAi}>
      <label class="check"><input type="checkbox" bind:checked={ai.enabled} /> {t("ui.settings.ai_enable")}</label>
      {#if ai.enabled}
        <label class="field">{t("ui.settings.ai_provider")}
          <select bind:value={ai.provider}>
            {#each PROVIDERS as p (p)}<option value={p}>{t(`ui.settings.provider.${p}`)}</option>{/each}
          </select>
        </label>
        {#if ai.provider === "home_assistant"}
          <!-- The AI task already set up in Home Assistant: no key, no models, no budget here. -->
          {#if settings.ai.aiTasks?.length}
            <label class="field">{t("ui.settings.ai_task_entity")}
              <select bind:value={ai.baseUrl}>
                <option value="" disabled>–</option>
                {#each settings.ai.aiTasks as x (x.id)}<option value={x.id}>{x.name}</option>{/each}
              </select>
            </label>
            {#if task}<p class="muted">{t(`ui.settings.ai_place.${task.place}`)}</p>{/if}
            {#if task && task.place !== "local"}<p class="muted">{t("ui.settings.ai_task_limit", { count: settings.ai.aiTaskLimit ?? 100 })}</p>{/if}
            {#if task?.place === "local"}
              <!-- Only with an AI in the house, and only after this yes (E-424). -->
              <label class="check"><input type="checkbox" bind:checked={ai.images} /> {t("ui.settings.ai_images")}</label>
              <p class="muted">{t("ui.settings.ai_images_hint")}</p>
            {/if}
          {:else}
            <p class="muted">{t("ui.settings.ai_task_none")}</p>
          {/if}
        {:else}
          {#if ai.provider !== "anthropic" && ai.provider !== "gemini"}
            <label class="field">{t("ui.settings.ai_address")}<input bind:value={ai.baseUrl} placeholder="http://…/v1" /></label>
          {/if}
          <label class="field">{t("ui.settings.ai_key")} {settings.ai.hasKey ? `(${t("ui.settings.ai_key_saved")})` : ""}
            <input type="password" bind:value={key} autocomplete="off" />
          </label>
          <label class="field">{t("ui.settings.ai_model_routine")}<input bind:value={ai.modelRoutine} placeholder={ai.provider === "anthropic" ? "claude-haiku-5-5" : ai.provider === "gemini" ? "gemini-flash-lite-latest" : ""} /></label>
          <label class="field">{t("ui.settings.ai_model_strong")}<input bind:value={ai.modelStrong} placeholder={ai.provider === "anthropic" ? "claude-sonnet-5-5" : ai.provider === "gemini" ? "gemini-flash-latest" : ""} /></label>
          <label class="field">{t("ui.settings.ai_budget")} ({settings.currency})<input type="number" min="0" max="1000" step="0.5" bind:value={ai.monthlyBudget} /></label>
          {#if !settings.ai.local}<p class="muted">{t("ui.settings.ai_spent", { spent: settings.ai.spent.toFixed(2), budget: settings.ai.budget.toFixed(2) })}</p>{/if}
        {/if}
        {#if level >= 4 && ai.provider !== "home_assistant"}
          <!-- A model per task (E-49): Vesta's sentences use the cheapest model and at most half
               the budget unless you choose otherwise here. -->
          <fieldset class="tasks">
            <legend>{t("ui.settings.ai_tasks")}</legend>
            {#each ["vesta", "ask", "rule", "analysis"] as task (task)}
              <label class="field">{t(`ui.settings.ai_task.${task}`)}
                {#if !settings.ai.local} · {settings.currency} {(settings.ai.spentByTask?.[task] ?? 0).toFixed(2)}{/if}
                <input value={ai.taskModels?.[task] ?? ""} placeholder={t("ui.settings.ai_task_default")}
                  oninput={(e) => { if (ai) ai.taskModels = { ...(ai.taskModels ?? {}), [task]: e.currentTarget.value }; }} />
              </label>
            {/each}
            <p class="muted">{t("ui.settings.ai_tasks_note")}</p>
          </fieldset>
        {/if}
      {/if}
      <button class="primary" type="submit">{t("ui.save")}</button>
    </form>
    {#if settings.ai.enabled && !settings.ai.consent}
      <div class="consent">
        <p>{t("ai.consent", { provider: t(`ui.settings.provider.${settings.ai.provider}`) })}</p>
        <!-- An informed choice (QA 2, M-7): the service's own terms, and a warning when an own
             AI address is outside the home network. -->
        <p class="muted">{t("ui.settings.ai_terms", { provider: t(`ui.settings.provider.${settings.ai.provider}`) })}</p>
        {#if settings.ai.remote}<p class="warn">{t("ui.settings.ai_remote")}</p>{/if}
        <details ontoggle={(e) => { if ((e.currentTarget as HTMLDetailsElement).open && !preview) get("ai_preview"); }}>
          <summary>{t("ui.settings.ai_show_example")}</summary>
          <pre>{preview ?? "…"}</pre>
        </details>
        <button class="primary" onclick={() => { send({ type: "ai_consent" }); setTimeout(() => get("settings"), 300); }}>{t("ui.settings.ai_agree")}</button>
      </div>
    {/if}
    {#if level >= 4 && settings.ai.consent}
      <!-- What went to an AI is in the Logboek, one place (QA 3, schermen 13): Soort › AI. -->
      <p class="muted">{t("ui.settings.ai_log_where")}</p>
    {/if}
  </section>
{/if}

<style>
  section { display: flex; flex-direction: column; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
  h3 { margin: 0; font-size: calc(14px * var(--text-scale)); color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  .muted { margin: 0; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  .field { display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  select, input:not([type="checkbox"]) { min-height: 44px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); padding: 0 12px; font: inherit; }
  details > summary { cursor: pointer; min-height: 40px; display: flex; align-items: center; gap: 6px; color: var(--accent); font-weight: 600; }
  .check { display: flex; gap: 10px; align-items: center; min-height: 44px; }
  .check input { width: 22px; height: 22px; accent-color: var(--accent); }
  form { display: flex; flex-direction: column; gap: 8px; }
  button { min-height: 44px; padding: 0 16px; border-radius: 22px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; }
  .primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 600; }
  .consent { display: flex; flex-direction: column; gap: 8px; padding: 12px; border-radius: 14px; border: 1px solid var(--learning); }
  .consent p { margin: 0; }
  .warn { color: var(--alert); font-weight: 600; }
  pre { margin: 0; max-height: 220px; overflow: auto; font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); background: var(--ink); padding: 10px; border-radius: 10px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .tasks { border: 1px solid var(--line); border-radius: 12px; padding: 8px 12px; display: flex; flex-direction: column; gap: 8px; }
</style>

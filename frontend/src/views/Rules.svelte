<script lang="ts">
  // House rules (E-87, E-103): as text ("never …") or with the builder; always read back first.
  import DeviceIcon from "../components/DeviceIcon.svelte";
  import AskFirst from "../components/AskFirst.svelte";
  import type { Room, RuleView } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    rules: RuleView[];
    rooms: Room[];
    t: T;
    send: (message: Record<string, unknown>) => void;
  }
  let { rules, rooms, t, send }: Props = $props();
  let mode = $state<"text" | "build">("build");
  let text = $state("");
  let category = $state("on");
  let scope = $state("house");
  let when = $state("always");
  let time = $state("07:00");
  let value = $state(21);

  function submitText(event: SubmitEvent): void {
    event.preventDefault();
    if (text.trim()) send({ type: "rule_text", text: text.trim() });
    text = "";
  }

  function submitBuild(event: SubmitEvent): void {
    event.preventDefault();
    const isRoom = scope !== "house";
    send({
      type: "rule_build",
      category,
      scopeType: isRoom ? "area" : "house",
      scope: isRoom ? scope : "*",
      value,
      timeFrom: when === "after" ? time : null,
      timeTo: when === "before" ? time : null,
    });
  }
</script>

<div class="rules">
  <h2>{t("tabs.house_rules")}</h2>
  {#if rules.length === 0}<p class="muted">{t("ui.rules.none")}</p>{/if}
  <ul>
    {#each rules as rule (rule.id)}
      <li>
        <!-- What the rule is about (E-511): a device, a room or the house. -->
        {#if rule.icon}<span class="rule-icon" aria-hidden="true"><DeviceIcon name={rule.icon} size={20} /></span>{/if}
        <span class="rule-text">{rule.text}</span>
        <AskFirst text={t("ui.rules.remove")} label="{t('ui.rules.remove')}: {rule.text}" question={t("ui.rules.remove_confirm")} {t}
          onyes={() => send({ type: "rule_remove", id: rule.id })} />
      </li>
    {/each}
  </ul>

  <div class="tabs" role="group">
    <button aria-pressed={mode === "build"} onclick={() => (mode = "build")}>{t("ui.rules.builder")}</button>
    <button aria-pressed={mode === "text"} onclick={() => (mode = "text")}>{t("ui.rules.as_text")}</button>
  </div>

  {#if mode === "text"}
    <form onsubmit={submitText}>
      <label for="rule-text">{t("ui.rules.text_label")}</label>
      <input id="rule-text" bind:value={text} maxlength="300" placeholder={t("ui.rules.text_example")} />
      <button class="primary" type="submit">{t("ui.rules.check")}</button>
    </form>
  {:else}
    <form onsubmit={submitBuild}>
      <label>{t("ui.rules.never_do")}
        <select bind:value={category}>
          {#each ["on", "off", "open", "close", "heat"] as c (c)}<option value={c}>{t(`ui.rules.category.${c}`)}</option>{/each}
        </select>
      </label>
      <label>{t("ui.rules.where")}
        <select bind:value={scope}>
          <option value="house">{t("rules.whole_house")}</option>
          {#each rooms as room (room.id)}<option value={room.id}>{room.name}</option>{/each}
        </select>
      </label>
      {#if category === "heat"}
        <label>{t("ui.rules.max_temperature")}
          <input type="number" min="5" max="35" step="0.5" bind:value />
        </label>
      {:else}
        <label>{t("ui.rules.when")}
          <select bind:value={when}>
            <option value="always">{t("ui.rules.when_always")}</option>
            <option value="before">{t("ui.rules.when_before")}</option>
            <option value="after">{t("ui.rules.when_after")}</option>
          </select>
        </label>
        {#if when !== "always"}<label>{t("ui.rules.time")}<input type="time" bind:value={time} /></label>{/if}
      {/if}
      <button class="primary" type="submit">{t("ui.rules.check")}</button>
    </form>
  {/if}
</div>

<style>
  .rule-icon { display: inline-flex; flex: none; color: var(--muted); }
  .rule-text { flex: 1; min-width: 0; }
  .rules { display: flex; flex-direction: column; gap: 10px; }
  h2 { margin: 0; font-family: var(--font-display); font-size: calc(17px * var(--text-scale)); }
  .muted { margin: 0; color: var(--muted); }
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  li { display: flex; gap: 10px; align-items: center; justify-content: space-between; padding: 10px 12px; border-radius: 14px; background: var(--surface); border: 1px solid var(--line); }
  .tabs { display: flex; gap: 6px; }
  .tabs button { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--line); background: var(--surface); color: var(--muted); cursor: pointer; }
  .tabs button[aria-pressed="true"] { color: var(--text); border-color: var(--accent); }
  form { display: flex; flex-direction: column; gap: 10px; padding: 12px; border-radius: 16px; background: var(--surface); border: 1px solid var(--line); }
  label { display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: calc(14px * var(--text-scale)); }
  input, select { min-height: 44px; border-radius: 12px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); padding: 0 12px; font: inherit; }
  .primary { min-height: 44px; border-radius: 22px; border: none; background: var(--accent); color: var(--on-accent); font-weight: 600; cursor: pointer; }
</style>

<script lang="ts">
  // HouseMind in Home Assistant (B-06, B-27, E-167 – E-169): place the small integration and
  // restart HA once (only after a yes) – part "link", under Upkeep – and who answers other
  // questions in Assist – part "assist", with Vesta. She has one name, also in Assist (E-265).
  import { INTEGRATIONS_PAGE, openInHA } from "../lib/openha";
  import type { IntegrationView } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    info: IntegrationView | undefined;
    part: "link" | "assist" | "pipeline";
    name: string; // hers, also in Assist
    t: T;
    send: (message: Record<string, unknown>) => void;
  }
  let { info, part, name, t, send }: Props = $props();
  let confirmRestart = $state(false);
  let confirmReplace = $state(false);
  const act = (message: Record<string, unknown>) => send({ type: "integration", ...message });
  // "Gekoppeld" only when HA really reaches HouseMind with the right key (20.23): in the real
  // house, after the factory settings, the old link still looked loaded while HA was refused.
  // "Koppelen" is one yes: HouseMind places the link, restarts HA if needed and finishes it in
  // HA itself; only when that fails does the screen show the two clicks in HA.
  let confirmLink = $state(false);
  const stage = $derived(
    !info ? ""
    : info.contact === "ok" ? "loaded"
    : info.contact === "refused" ? "refused"
    : info.linking ? "linking"
    : info.finish === "failed" && info.setup !== "configured" ? "manual"
    : info.installed && !info.foreign && info.setup === "configured" ? "waiting"
    : info.available ? "ready"
    : "unavailable",
  );
</script>

{#if part === "pipeline"}
  <!-- Vesta's own Assist pipeline (E-280): only after a yes; someone else's is never touched. -->
  <section data-section="pipeline">
    <h3>{t("ui.setup.pipeline.title")}</h3>
    {#if !info?.pipeline}
      <p class="muted">…</p>
    {:else if info.pipeline.state === "ours"}
      <p>{t("ui.setup.pipeline.ours", { name: info.pipeline.name ?? name })}</p>
    {:else if info.pipeline.state === "no_agent"}
      <p class="muted">{t("ui.setup.pipeline.no_agent")}</p>
    {:else if info.pipeline.state === "other"}
      <p>{t("ui.setup.pipeline.other", { other: info.pipeline.name ?? "" })}</p>
      <div class="row">
        <button class="primary" onclick={() => act({ do: "pipeline", adopt: true })}>{t("ui.setup.pipeline.adopt")}</button>
        <button onclick={() => act({ do: "pipeline", adopt: false })}>{t("ui.setup.pipeline.own")}</button>
      </div>
    {:else}
      <p class="muted">{t("ui.setup.pipeline.text", { name })}</p>
      <button class="primary" onclick={() => act({ do: "pipeline" })}>{t("ui.setup.pipeline.make")}</button>
    {/if}
  </section>
{:else if part === "assist"}
  <section data-section="assist">
    <h3>{t("ui.integration.assist_title")}</h3>
    {#if !info}
      <p class="muted">…</p>
    {:else}
      <label class="field">{t("ui.integration.forward")}
        <select value={info.forward} onchange={(e) => act({ do: "forward", value: e.currentTarget.value })}>
          <option value="off">{t("ui.integration.forward_off")}</option>
          <option value="ai">{t("ui.integration.forward_ai")}</option>
          {#each info.agents as agent (agent)}<option value={`agent:${agent}`}>{agent}</option>{/each}
        </select>
      </label>
      <p class="muted">{t("ui.integration.forward_hint")}</p>
    {/if}
    <p class="muted">{t("ui.integration.one_name", { name })}</p>
  </section>
{:else}
<section data-section="integration">
  <h3>{t("ui.integration.title")}</h3>
  {#if !info}
    <p class="muted">…</p>
  {:else}
    <p>{t(`ui.integration.state_${stage}`)}</p>
    {#if info.foreign && stage !== "loaded"}
      <p>{t("ui.integration.foreign", { version: info.installed ?? "" })}</p>
      {#if confirmReplace}
        <p>{t("ui.integration.link_confirm")}</p>
        <div class="row">
          <button class="primary" onclick={() => { act({ do: "link", yes: true, replace: true }); confirmReplace = false; }}>{t("ui.integration.replace")}</button>
          <button onclick={() => (confirmReplace = false)}>{t("ui.no")}</button>
        </div>
      {:else}
        <button onclick={() => (confirmReplace = true)}>{t("ui.integration.link")}</button>
      {/if}
    {:else if stage === "ready" || stage === "manual"}
      {#if stage === "manual"}
        <ol class="steps">
          <li>{t("ui.integration.manual_1")}</li>
          <li>{t("ui.integration.manual_2")}</li>
          <li>{t("ui.integration.manual_3")}</li>
        </ol>
        <div class="row">
          <button class="primary" onclick={() => openInHA(INTEGRATIONS_PAGE)}>{t("ui.integration.open_ha")}</button>
          <button onclick={() => act({ do: "link", yes: true })}>{t("ui.integration.try_again")}</button>
        </div>
      {:else if confirmLink}
        <p>{t("ui.integration.link_confirm")}</p>
        <div class="row">
          <button class="primary" onclick={() => { act({ do: "link", yes: true }); confirmLink = false; }}>{t("ui.integration.link_yes")}</button>
          <button onclick={() => (confirmLink = false)}>{t("ui.no")}</button>
        </div>
      {:else}
        <p class="muted">{t("ui.integration.place_text")}</p>
        <button class="primary" onclick={() => (confirmLink = true)}>{t("ui.integration.link")}</button>
      {/if}
    {:else if stage === "waiting" || stage === "refused"}
      {#if confirmRestart}
        <p>{t("ui.integration.restart_confirm")}</p>
        <div class="row">
          <button class="primary" onclick={() => { act({ do: "restart", yes: true }); confirmRestart = false; }}>{t("ui.yes")}</button>
          <button onclick={() => (confirmRestart = false)}>{t("ui.no")}</button>
        </div>
      {:else}
        <!-- Waiting a moment is usual; a restart only when it stays like this. -->
        <button onclick={() => (confirmRestart = true)}>{t("ui.integration.restart")}</button>
      {/if}
    {:else if stage === "loaded"}
      <p class="muted">{t("ui.integration.loaded_text")}</p>
    {/if}
    {#if info.installed && info.current && info.installed !== info.current}
      <p class="muted">{t("ui.integration.update", { version: info.current })}</p>
    {/if}
  {/if}
</section>
{/if}

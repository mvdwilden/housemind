<script lang="ts">
  // Brein (from level 3): how far HouseMind is, the house rules and the rhythm of the house; on
  // Expert also the devices (E-262, B-110). What it did, the proposals and the house report are
  // in Logboek (E-263, B-117): one place – also each decision's checks, on Expert. In tabs
  // (E-398, B-315): one part at a time. No meter, no decision trail and no stream of thoughts
  // (QA 3): HA shows processor and memory, the Logboek every decision.
  import Knowledge from "../components/Knowledge.svelte";
  import { roomIcons } from "../lib/deviceicons";
  import Readiness from "../components/Readiness.svelte";
  import RhythmCard from "../components/RhythmCard.svelte";
  import LaterCard from "../components/Later.svelte";
  import Devices from "./Devices.svelte";
  import Rules from "./Rules.svelte";
  import { brainTabs, nextTab, openTab, tabCount, type BrainTab } from "../lib/braintabs";
  import type { DeviceRow, HouseKnowledgeView, Later, Snapshot } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    snap: Snapshot;
    t: T;
    send: (message: Record<string, unknown>) => void;
    onlimit?: () => void; // an administrator: open "Hoe ver HouseMind mag gaan"
    devices: DeviceRow[] | undefined; // Expert
    why: Record<string, string>;
    onwhynot: (id: string) => void;
    onexclude: (ids: string[]) => void;
    later?: Later; // Straks, under Ritme
    ontest?: () => void; // an administrator on Expert: open Test het huis
    tab?: string | null; // the open tab: kept per person, or where a jump lands (B-316)
    ontab: (tab: BrainTab) => void;
    knowledge?: HouseKnowledgeView; // Wat ik weet, an administrator from Betrokken (E-524)
    get?: (what: string) => void;
    focus?: string | null; // where a jump lands inside a tab ("knowledge:area:music:role")
    onmore?: (id: string) => void; // Lees meer: the manual at that part (QA 3)
  }
  let { snap, t, send, onlimit, devices, why, onwhynot, onexclude, later, ontest, tab, ontab, knowledge, get, focus = null, onmore }: Props =
    $props();
  const tabs = $derived(brainTabs(snap.level, Boolean(snap.rhythm), snap.user.isAdmin));
  const current = $derived(openTab(tab, tabs));
  const LABEL: Record<BrainTab, string> = {
    readiness: "ui.brain.tab.readiness",
    rules: "ui.brain.tab.rules",
    rhythm: "ui.brain.tab.rhythm",
    knowledge: "ui.brain.tab.knowledge",
    devices: "ui.brain.tab.devices",
  };

  // Straks under Ritme: asked while that tab is open – again with a new picture of the house
  // (also after a jump in a demo house), at most every 15 s, and else once a minute.
  let laterAt = 0;
  function askLater(): void {
    laterAt = Date.now();
    send({ type: "get", what: "later" });
  }
  $effect(() => {
    if (current !== "rhythm") return;
    void snap;
    const timer = setTimeout(askLater, Math.max(0, laterAt + 15000 - Date.now()));
    return () => clearTimeout(timer);
  });
  $effect(() => {
    if (current !== "rhythm") return;
    const timer = setInterval(askLater, 60000);
    return () => clearInterval(timer);
  });
  let strip = $state<HTMLElement | null>(null);
  // The open tab is in view on a phone, where the row is wider than the screen – also when
  // Brein opens on the tab kept from last time.
  $effect(() => {
    const button = strip?.querySelector<HTMLElement>(`[data-tab="${current}"]`);
    if (!strip || !button) return;
    const { scrollLeft, clientWidth } = strip;
    if (button.offsetLeft < scrollLeft || button.offsetLeft + button.offsetWidth > scrollLeft + clientWidth) {
      strip.scrollTo({ left: Math.max(0, button.offsetLeft - 8) });
    }
  });
  function choose(next: BrainTab, focus = false): void {
    ontab(next);
    if (focus) queueMicrotask(() => strip?.querySelector<HTMLElement>(`[data-tab="${next}"]`)?.focus());
  }
  function key(event: KeyboardEvent): void {
    const next = nextTab(tabs, current, event.key);
    if (next === null) return;
    event.preventDefault();
    choose(next, true);
  }
</script>

<div class="brain">
  <div class="tabs" role="tablist" aria-label={t("ui.brain.tabs")} bind:this={strip} data-tour="brain-tabs">
    {#each tabs as one (one)}
      {@const count = tabCount(one, snap.readiness, snap.rules, snap.knowledgeWaiting ?? 0)}
      <button role="tab" id="brain-tab-{one}" data-tab={one} aria-selected={current === one}
        aria-controls="brain-panel" tabindex={current === one ? 0 : -1} onclick={() => choose(one)} onkeydown={key}>
        {t(LABEL[one])}{#if count}<span class="count">{count}</span>{/if}
      </button>
    {/each}
  </div>

  <div class="panel" role="tabpanel" id="brain-panel" aria-labelledby="brain-tab-{current}">
    {#if current === "readiness"}
      <!-- "Klaar voor autonomie?" (E-256): how far HouseMind is, per part. -->
      <div data-tour="readiness"><Readiness parts={snap.readiness ?? []} others={snap.readinessOther ?? []} lang={snap.language} {t} {onlimit} {send} onmore={onmore ? () => onmore("leren-klaar-voor-autonomie") : undefined} roomIcons={roomIcons(snap.rooms)} /></div>
    {:else if current === "rules"}
      <!-- House rules (E-87): part of Brein from level 3 (redesign step 3). -->
      <div data-tour="rules"><Rules rules={snap.rules ?? []} rooms={snap.rooms ?? []} {t} {send} /></div>
    {:else if current === "rhythm" && snap.rhythm}
      <RhythmCard rhythm={snap.rhythm} lang={snap.language} {t}>
        <LaterCard {later} {t} {ontest} />
      </RhythmCard>
    {:else if current === "knowledge" && get}
      <!-- Wat ik weet (E-524): what HouseMind knows of the house, everything to take back (E-525). -->
      <Knowledge view={knowledge} lang={snap.language} {t} {send} {get} {focus} roomIcons={roomIcons(snap.rooms)} />
    {:else if current === "devices"}
      <section class="col" aria-label={t("tabs.devices")}>
        <Devices {devices} isAdmin={snap.user.isAdmin} lang={snap.language} name={snap.vesta?.name} excluded={[]} {t} {onexclude} {onwhynot} {why}
          onlevels={(message) => send({ type: "levels", ...message })} />
      </section>
    {/if}
  </div>
</div>

<style>
  .brain { display: flex; flex-direction: column; gap: 14px; }
  .col { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .tabs {
    position: sticky; top: 0; z-index: 2; display: flex; gap: 4px; overflow-x: auto; scrollbar-width: none;
    padding: 6px 0; background: var(--ink); border-bottom: 1px solid var(--line);
  }
  .tabs::-webkit-scrollbar { display: none; }
  .tabs button {
    flex: none; display: inline-flex; align-items: center; gap: 6px; min-height: 40px; padding: 0 14px;
    border: 1px solid transparent; border-radius: 999px; background: none; color: var(--muted);
    font: inherit; font-size: calc(14px * var(--text-scale)); font-weight: 600; cursor: pointer; white-space: nowrap;
  }
  .tabs button:hover { color: var(--text); }
  .tabs button[aria-selected="true"] { background: var(--surface); border-color: var(--line); color: var(--text); }
  .count { font-family: var(--font-mono); font-size: calc(12px * var(--text-scale)); font-weight: 500; color: var(--muted); }
</style>

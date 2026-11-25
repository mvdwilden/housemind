<script lang="ts">
  // Huis stays calm (E-261): at most three cards that ask for attention – an emergency, a
  // proposal, a question, an important notification – then one sentence of what HouseMind
  // thought, and the rooms. Everything else is in Logboek (E-263): the rest of the
  // notifications, the whole of "Wat ik bedacht", today, the briefing and the house report.
  import HouseView from "../components/HouseView.svelte";
  import DayRibbon from "../components/DayRibbon.svelte";
  import Mind from "../components/Mind.svelte";
  import NoticeCard from "../components/NoticeCard.svelte";
  import ProposalCard from "../components/ProposalCard.svelte";
  import Readiness from "../components/Readiness.svelte";
  import { attention } from "../lib/logbook";
  import type { Notice, Room, Snapshot } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    snap: Snapshot;
    t: T;
    view: "cards" | "plan" | "network";
    onview: (view: "cards" | "plan" | "network") => void;
    onroom: (room: Room) => void;
    onanswer: (proposal: number, answer: string) => void;
    onnotice: (notice: Notice, action: string, extra?: Record<string, unknown>) => void;
    onlogbook: () => void;
    orb?: string; // the state Vesta shows now, held as in the header
  }
  let { snap, t, view, onview, onroom, onanswer, onnotice, onlogbook, orb = snap.orb }: Props = $props();
  const asks = $derived(attention(snap.level >= 3 ? snap.proposals : [], snap.notifications));
  // The newest open question waits as a dotted path in the network, with its quick answers.
  const waiting = $derived([...(snap.level >= 3 ? (snap.proposals ?? []) : [])].reverse().find((p) => p.areaId) ?? null);
</script>

{#if asks.cards.length}
  <section class="stack" data-tour="attention" aria-label={t("ui.attention")}>
    {#each asks.cards as card, i (card.kind === "proposal" ? `p${card.proposal.id}` : `n${card.notice.id}`)}
      {#if card.kind === "proposal"}
        <ProposalCard proposal={card.proposal} {t} onanswer={(a) => onanswer(card.proposal.id, a)}
          note={!asks.cards.slice(0, i).some((c) => c.kind === "proposal" && c.proposal.canRule)} />
      {:else}
        <NoticeCard notice={card.notice} {t} onaction={(a, extra) => onnotice(card.notice, a, extra)} />
      {/if}
    {/each}
    {#if asks.more}
      <button class="link" onclick={onlogbook}>{t("ui.attention_more", {}, asks.more)}</button>
    {/if}
  </section>
{/if}

{#if snap.mind}
  <Mind items={snap.mind} {t} onmore={onlogbook} rhythm={snap.rhythmLine} />
  {#if snap.dayRooms?.length}<DayRibbon rooms={snap.dayRooms} {t} />{/if}
{/if}

{#if snap.rooms}
  <div data-tour="rooms"><HouseView {view} rooms={snap.rooms} floors={snap.floors ?? [{ id: "_", name: "", rooms: snap.rooms.map((r) => r.id) }]} {t} onopen={onroom} {onview} live={snap.livePath ?? null}
    {waiting} {orb} level={snap.level} ai={snap.features.ai === "on"} {onanswer} /></div>
{/if}

{#if snap.level === 2 && snap.readiness?.length}
  <!-- Level 2 has no Brein: one sentence per part, like the card there (E-256). -->
  <Readiness parts={snap.readiness} lang={snap.language} {t} compact />
{/if}

<style>
  .stack { display: flex; flex-direction: column; gap: 10px; }
  .link { align-self: flex-start; border: none; background: none; color: var(--accent); font-weight: 600; cursor: pointer; min-height: 40px; padding: 0; }
</style>

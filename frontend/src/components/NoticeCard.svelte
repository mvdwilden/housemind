<script lang="ts">
  // One notification as a card: its kind as colour, shape and word (E-46), the text, and its
  // answers while it is open. On Huis when it asks for attention, and in Logboek (E-263).
  // "Later" asks how long – 1, 4 or 8 hours or tomorrow 08:00 – and "Niet meer tonen" switches
  // this kind of message off for this person, after a yes; for the cameras a choice per line.
  // In Logboek a message this person no longer wants says so, with "Weer tonen" (E-372).
  import DeviceIcon from "./DeviceIcon.svelte";
  import Glyph from "./Glyph.svelte";
  import { ORB_LOOKS } from "../lib/orb";
  import type { Notice } from "../lib/types";
  import type { T } from "../lib/ui";

  interface Props {
    notice: Pick<Notice, "kind" | "text" | "at" | "actions" | "labels" | "mute" | "hidden" | "laterUntil" | "unmute" | "icon" | "pct" | "level" | "closed">;
    t: T;
    onaction: (action: string, extra?: Record<string, unknown>) => void;
    handled?: boolean; // in Logboek: it was answered or put away
  }
  let { notice, t, onaction, handled = false }: Props = $props();
  // Kind → state look, so every kind has a colour, a shape and a word (E-46).
  const LOOK = { emergency: ORB_LOOKS.emergency, important: ORB_LOOKS.attention, info: ORB_LOOKS.calm, learning: ORB_LOOKS.learning };
  const look = $derived(LOOK[notice.kind]);
  const LATER = ["1h", "4h", "8h", "morning"];
  let open = $state<"later" | "mute" | null>(null);
  const mute = $derived(notice.mute ?? []);
  const what = (text: string): string => text.replace(/[.!?]+$/, "");

  function label(action: string): string {
    if (notice.labels?.[action]) return notice.labels[action];
    return action === "snooze" || action === "dismiss" ? t(`ui.notifications.${action}`) : t(`notify.answer.${action}`);
  }
  // Ik kijk ernaar · Later · Niet meer tonen, in that order everywhere (E-403).
  const shown = $derived([...notice.actions.filter((a) => a === "dismiss"), ...notice.actions.filter((a) => a !== "dismiss")]);
  function tap(action: string): void {
    if (action === "snooze") open = open === "later" ? null : "later";
    else onaction(action);
  }
  function later(choice: string): void {
    open = null;
    onaction("snooze", { for: choice });
  }
  function off(key: string): void {
    open = null;
    onaction("mute", { key });
  }
</script>

<article class="notice" class:handled={handled || !!notice.hidden} style:--kind={look.color}>
  <p class="meta">
    <Glyph shape={look.shape} color={look.color} deep={look.deep} /> {t(`ui.notifications.kind.${notice.kind}`)} · {notice.at}{handled ? ` · ${t("ui.logbook.handled")}` : ""}
    {#if notice.hidden === "muted"}<span class="pill">{t("ui.notice.not_shown")}</span>{/if}
    {#if notice.hidden === "later" && notice.laterUntil}<span class="pill">{t("ui.notice.later_until", { at: notice.laterUntil })}</span>{/if}
  </p>
  <!-- The device it is about, beside the words (E-511); Vesta's shape stays in the line above. -->
  <p class="text">{#if notice.icon}<span class="dev-icon" class:alarm={notice.kind === "emergency"} aria-hidden="true"><DeviceIcon name={notice.icon} size={18} pct={notice.pct} level={notice.level} closed={notice.closed} /></span>{/if}{notice.text}</p>
  {#if notice.actions.length || mute.length}
    <div class="actions">
      {#each shown as action (action)}
        <button class:primary={action === "do" || action === "yes"} aria-expanded={action === "snooze" ? open === "later" : undefined} onclick={() => tap(action)}>
          {label(action)}
        </button>
      {/each}
      {#if mute.length}
        <button aria-expanded={open === "mute"} onclick={() => (open = open === "mute" ? null : "mute")}>{t("ui.notice.mute")}</button>
      {/if}
    </div>
  {/if}
  {#if open === "later"}
    <div class="choose" role="group" aria-label={t("ui.notice.later_title")}>
      <p>{t("ui.notice.later_title")}</p>
      <div class="actions">
        {#each LATER as choice (choice)}<button onclick={() => later(choice)}>{t(`ui.notice.later.${choice}`)}</button>{/each}
      </div>
    </div>
  {:else if open === "mute"}
    <!-- First a yes or no; for the cameras the line it is about is the yes. -->
    <div class="choose" role="group" aria-label={t("ui.notice.mute")}>
      {#if mute.length === 1}
        <p>{t("ui.notice.mute_confirm", { what: what(mute[0].text) })}</p>
        <div class="actions">
          <button class="primary" onclick={() => off(mute[0].key)}>{t("ui.yes")}</button>
          <button onclick={() => (open = null)}>{t("ui.no")}</button>
        </div>
      {:else}
        <p>{t("ui.notice.mute_choose")}</p>
        <div class="actions">
          {#each mute as choice (choice.key)}<button onclick={() => off(choice.key)}>{what(choice.text)}</button>{/each}
          <button onclick={() => (open = null)}>{t("ui.no")}</button>
        </div>
      {/if}
    </div>
  {/if}
  {#if notice.unmute?.length}
    <div class="actions">
      {#each notice.unmute as again (again.key)}
        <button onclick={() => onaction("unmute", { key: again.key })}>
          {again.text === notice.text ? t("ui.notice.show_again") : t("ui.notice.show_again_what", { what: what(again.text) })}
        </button>
      {/each}
    </div>
  {/if}
</article>

<style>
  .dev-icon { display: inline-flex; vertical-align: -3px; margin-right: 6px; color: var(--muted); }
  .dev-icon.alarm { color: var(--emergency); }
  .notice { background: var(--surface); border: 1px solid var(--line); border-left: 3px solid var(--kind); border-radius: 16px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
  .notice.handled { border-left-color: var(--line); }
  .meta { margin: 0; color: var(--muted); font-size: calc(13px * var(--text-scale)); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .pill { padding: 0 8px; border-radius: 10px; border: 1px solid var(--edge); font-size: calc(12px * var(--text-scale)); }
  .text { margin: 0; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; }
  button { min-height: 40px; padding: 0 14px; border-radius: 20px; border: 1px solid var(--line); background: var(--raised); cursor: pointer; }
  .primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
  .choose { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 12px; }
  .choose p { margin: 0; }
</style>

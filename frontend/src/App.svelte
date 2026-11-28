<script lang="ts">
  import Glyph from "./components/Glyph.svelte";
  import Icon from "./components/Icon.svelte";
  import DayRing from "./components/DayRing.svelte";
  import Orb from "./lib/Orb.svelte";
  import Sheet from "./components/Sheet.svelte";
  import Notices from "./components/Notices.svelte";
  import WhatsNew from "./components/WhatsNew.svelte";
  import Brief from "./components/Brief.svelte";
  import VestaWindow from "./components/VestaWindow.svelte";
  import Tour from "./components/Tour.svelte";
  import Readiness from "./components/Readiness.svelte";
  import NoticeCard from "./components/NoticeCard.svelte";
  import About from "./sheets/About.svelte";
  import OrbLegend from "./components/OrbLegend.svelte";
  import Help, { type HelpFocus } from "./sheets/Help.svelte";
  import HouseSettingsSheet from "./sheets/HouseSettings.svelte";
  import Onboarding from "./sheets/Onboarding.svelte";
  import PauseSheet from "./sheets/PauseSheet.svelte";
  import Play from "./sheets/Play.svelte";
  import TestPanel from "./sheets/TestPanel.svelte";
  import Preferences from "./sheets/Preferences.svelte";
  import RoomSheet from "./sheets/RoomSheet.svelte";
  import Setup from "./sheets/Setup.svelte";
  import SafeMode from "./components/SafeMode.svelte";
  import Brain from "./views/Brain.svelte";
  import Energy from "./views/Energy.svelte";
  import Home from "./views/Home.svelte";
  import Logbook from "./views/Logbook.svelte";
  import Tablet from "./views/Tablet.svelte";
  import Tidy from "./views/Tidy.svelte";
  import TidyGroup from "./components/TidyGroup.svelte";
  import { setTabletArea } from "./lib/tablet";
  import { aloud, setAloud, unlockVoice } from "./lib/voice";
  import { tourDue, tourFor, toured, type TourStep } from "./lib/tour";
  import { tabFor, type BrainTab } from "./lib/braintabs";
  import { noticePlace, topLayer, tourMayStart, TOUR_QUIET_MS, type Layers } from "./lib/layers";
  import { pickScheme, rememberScheme, restoreScheme, schemeVars, startScheme, VARIABLES } from "./lib/schemes";
  import { device } from "./lib/device.svelte";
  import { canListen } from "./lib/listen";
  import { Connection } from "./lib/connection.svelte";
  import { orbLook, shownOrb } from "./lib/orb";
  import { HeldOrb } from "./lib/orbhold.svelte";
  import { defaultView, menuTabs, tabsFor, translator } from "./lib/ui";
  import { demoNow, demoTime, percent } from "./lib/play";
  import { detectBrowser } from "./lib/michelp";
  import { othersWaiting, type Logbook as LogbookData } from "./lib/logbook";
  import type { DeviceRow, EnergySettingsView, HouseKnowledgeView, HouseSettings, IntegrationView, Later, MyData, Notice, PlayView, ReadyPart, Room, TestView, TidyView, Why } from "./lib/types";

  const conn = new Connection();
  conn.connect();
  // A browser plays sound only after a touch: the first touch anywhere lets her speak later.
  if (typeof window !== "undefined") window.addEventListener("pointerdown", unlockVoice, { once: true });

  type SheetName = "none" | "preferences" | "pause" | "help" | "settings" | "about" | "room" | "readiness" | "orb" | "tidy";
  let sheet = $state<SheetName>("none");
  let room = $state<Room | null>(null);
  let activeTab = $state("home");
  let chosenView = $state<"cards" | "plan" | "network" | null>(null);

  const snap = $derived(conn.snapshot);
  const level = $derived(snap?.level ?? 1);
  // Not set up yet (E-277): only the start screen and the steps, on every level.
  const setupPending = $derived(Boolean(snap?.setup && snap.setup.state !== "done"));
  const t = $derived(translator(snap));
  // Each state stays a moment, so a tap shows violet long enough to be seen (E-217).
  const held = new HeldOrb();
  $effect(() => held.want(shownOrb(snap?.orb ?? "calm", conn.waiting)));
  const orb = $derived(held.state);
  const look = $derived(orbLook(orb, level));
  // Asleep while the alarm is on (E-539): "Welterusten · beveiligd".
  const secured = $derived(Boolean(snap?.secured) && look.state === "sleeping" && !conn.listening);
  const word = $derived(snap ? t(conn.listening ? "orb.listening" : look.textKey) + (secured ? ` · ${t("orb.secured_short")}` : "") : "");
  const tabs = $derived(tabsFor(level, snap?.features.energy ?? false, snap?.user.isAdmin ?? false));
  const view = $derived(chosenView ?? defaultView(level));
  const openNotices = $derived(snap?.notifications?.length ?? 0);
  const liveRoom = $derived(room ? (snap?.rooms?.find((r) => r.id === room?.id) ?? room) : null);

  // A demo house (E-321): the bar with the house's own time, running on between pictures.
  const demo = $derived(snap?.demo ?? null);
  let demoAt = $state(Date.now());
  let demoTick = $state(Date.now());
  $effect(() => {
    void demo?.now;
    demoAt = Date.now();
  });
  $effect(() => {
    if (!demo) return;
    const timer = setInterval(() => (demoTick = Date.now()), 1000);
    return () => clearInterval(timer);
  });
  const demoClock = $derived(demo ? demoTime(demoNow(demo, demoAt, demoTick), snap?.language ?? "en") : "");
  // 'Speel het huis' only in a browser on a wide screen, as a column right of the main screen –
  // not in the Home Assistant app, not on a wall tablet (the administrator's wish, B-187).
  let wide = $state(typeof matchMedia !== "undefined" && matchMedia("(min-width: 1200px)").matches);
  if (typeof matchMedia !== "undefined") matchMedia("(min-width: 1200px)").addEventListener("change", (e) => (wide = e.matches));
  const inBrowser = typeof navigator === "undefined" || !detectBrowser(navigator.userAgent).startsWith("ha_");
  const playHere = $derived(Boolean(demo) && wide && inBrowser);
  const demoState = $derived(
    !demo ? "" : !demo.running ? t("play.still")
      : demo.speed > 1 ? t("play.faster", { speed: demo.speed })
      : demo.speed < 1 ? t("play.slower", { speed: Math.round(1 / demo.speed) }) : "",
  );
  // What happened, again when the house changed – at most every 1.5 s.
  let playAt = 0;
  $effect(() => {
    if (!playHere || !snap) return;
    const timer = setTimeout(() => {
      playAt = Date.now();
      conn.get("play");
    }, Math.max(0, playAt + 1500 - Date.now()));
    return () => clearTimeout(timer);
  });
  function playHelp(): void {
    helpFocus = { kind: "manual", id: "speel-het-huis" };
    open("help");
  }

  // Test het huis (E-474, B-388): for an administrator on Expert. On a wide screen in a browser
  // it is a column right of the main screen, like Speel het huis (it takes that column's place
  // while open), opened with a small round button at the top right (the administrator's wish,
  // 14-11-2026); elsewhere – a phone, a narrower window, the Home Assistant app – it is a page of
  // its own in the menu.
  const testAside = $derived(wide && inBrowser);
  const testButton = $derived(testAside && tabs.includes("test"));
  const menu = $derived(menuTabs(tabs, testAside));
  let testOpen = $state(false);
  const testShown = $derived((testOpen && testAside) || activeTab === "test");
  // The window grew wide while the page was open: the column takes it over.
  $effect(() => {
    if (testAside && activeTab === "test") {
      activeTab = "home";
      testOpen = true;
    }
  });
  function pickTab(tab: string): void {
    if (tab === "test" && testAside) testOpen = !testOpen;
    else activeTab = tab;
  }
  function testHelp(): void {
    helpFocus = { kind: "manual", id: "test-het-huis" };
    open("help");
  }
  // The panel follows the house: again when the house changed – at most every 1.5 s.
  let testAt = 0;
  $effect(() => {
    if (!testShown || !snap) return;
    const timer = setTimeout(() => {
      testAt = Date.now();
      conn.get("test");
    }, Math.max(0, testAt + 1500 - Date.now()));
    return () => clearTimeout(timer);
  });

  // What this device shows (E-46, E-266): its colour scheme from the files that passed the
  // check (or the device's own light or dark) and text size. A device that never chose starts
  // from the person's earlier choice. Less motion follows only the device itself (E-319).
  if (typeof document !== "undefined") restoreScheme(document.documentElement);
  let prefersDark = $state(typeof matchMedia === "undefined" || matchMedia("(prefers-color-scheme: dark)").matches);
  if (typeof matchMedia !== "undefined") {
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => (prefersDark = e.matches));
  }
  $effect(() => {
    const prefs = snap?.prefs;
    const root = document.documentElement;
    if (!prefs) return;
    const scheme = pickScheme(snap?.themes ?? [], device.choices.scheme ?? startScheme(prefs.theme), prefersDark);
    for (const variable of [...Object.values(VARIABLES), "--vesta-glow"]) root.style.removeProperty(variable);
    if (scheme) {
      const vars = schemeVars(scheme);
      for (const [variable, value] of Object.entries(vars)) root.style.setProperty(variable, value);
      root.setAttribute("data-theme", scheme.base);
      root.style.colorScheme = scheme.base;
      rememberScheme(vars, scheme.base);
    } else {
      root.removeAttribute("data-theme");
      root.style.removeProperty("color-scheme");
    }
    largeText = (device.choices.textSize ?? prefs.textSize) === "large";
    root.classList.toggle("large-text", largeText);
    shownTheme = scheme ? scheme.base : null; // the manual follows it (E-324)
    root.lang = snap?.language ?? "en";
  });

  $effect(() => {
    if (!tabs.includes(activeTab) && activeTab !== "home") activeTab = "home";
  });
  // Another tab starts at its top, not where the last one was scrolled to.
  let scroller = $state<HTMLElement | null>(null);
  $effect(() => {
    void activeTab;
    scroller?.scrollTo({ top: 0 });
  });

  // "Ja, open het": the screen where a setting is, at that part, lit up for a moment (E-244).
  // Settings are in groups (E-265): the sheet opens the group of that part first.
  let focusSection = $state<string | null>(null);
  conn.onopen = (offer) => {
    if (offer.sheet === "readiness") return showReadiness(offer.section);
    if (offer.sheet === "settings" && !snap?.user.isAdmin) return;
    if (offer.section === "tidy") return showTidy();
    open(offer.sheet === "settings" ? "settings" : "preferences");
    focusSection = offer.section;
    spot(offer.section);
  };

  function spot(section: string, tries = 12): void {
    // The part may need a moment: its group opens, or the House settings are still coming in.
    setTimeout(() => {
      const part = document.querySelector<HTMLElement>(`[data-section="${section}"]`);
      if (!part) {
        if (tries > 0) spot(section, tries - 1);
        return;
      }
      const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
      part.scrollIntoView({ block: "center", behavior: still ? "auto" : "smooth" });
      part.classList.add("spot");
      setTimeout(() => part.classList.remove("spot"), 2500);
    }, tries === 12 ? 350 : 150);
  }

  // "Klaar voor autonomie?" (E-256): in Brein from level 3, else as a sheet – lit up for a moment.
  function showReadiness(section = "readiness"): void {
    if (tabs.includes("brain")) {
      sheet = "none";
      activeTab = "brain";
      brainTab = tabFor(section) ?? brainTab; // its own tab in Brein (E-398, B-316)
      brainFocus = section; // a line inside the tab, such as a room in Wat ik weet (E-524)
    } else open("readiness");
    spot(section.split(":")[0]);
  }

  // Home Assistant netjes (E-449, B-363): a page of its own in the menu for an administrator on
  // Expert; on the other levels 'Bekijken' on one of its messages opens it as a sheet.
  function showTidy(): void {
    if (tabs.includes("tidy")) {
      sheet = "none";
      activeTab = "tidy";
    } else open("tidy");
  }

  // Brein in tabs (E-398, B-316): the tab this person had open last, also on another device.
  let brainTab = $state<string | null>(null);
  let brainFocus = $state<string | null>(null);
  $effect(() => {
    if (brainTab === null && snap) brainTab = snap.prefs.brainTab ?? null;
  });
  function pickBrainTab(tab: BrainTab): void {
    brainTab = tab;
    conn.send({ type: "set_pref", key: "brain_tab", value: tab });
  }

  // Lees meer under Vesta's answer: the manual at that part (E-246); Help is the manual (E-506).
  let helpFocus = $state<HelpFocus | null>(null);
  let big = $state(false); // the manual on the whole screen (E-324)
  let shownTheme = $state<string | null>(null);
  let largeText = $state(false);
  function closeHelp(): void {
    sheet = "none";
    helpFocus = null;
    big = false;
  }
  // Pauze… or Help among Vesta's choices (E-349).
  conn.onchoose = (what) => open(what === "help" ? "help" : "pause");
  conn.onread = (more) => {
    helpFocus = { kind: more.kind, id: more.id };
    open("help");
  };

  // The tour (E-249): once per person and level, after the introduction, when nothing else is
  // open; never on a wall tablet. Its own button beside Help starts it again in full (E-506).
  let touring = $state<TourStep[] | null>(null);

  // One layer at a time (E-259, B-107): an emergency, then Vesta's window, then a sheet, then the
  // tour, then a short message. A message waits for the window, the introduction or the tour,
  // and sits at the foot of an open sheet.
  const notices = $derived(level === 1 ? conn.notices.filter((n) => n.kind !== "said") : conn.notices);
  const layers: Layers = $derived({
    emergency: snap?.orb === "emergency",
    vesta: conn.vesta.open,
    sheet: sheet !== "none",
    introduction: Boolean(snap?.onboarding.person),
    tour: touring !== null,
    notices: notices.length,
  });
  const noticesAt = $derived(noticePlace(layers));
  // When the last message came or went: the tour starts only after 10 seconds of quiet.
  let lastNoticeAt = $state<number | null>(null);
  let noticeCount = 0;
  $effect(() => {
    if (notices.length !== noticeCount) {
      noticeCount = notices.length;
      lastNoticeAt = Date.now();
    }
  });
  // A short confirmation ("Bewaard") is plain text in the header, next to the buttons (E-174,
  // wish from the real house): 2 seconds on Normaal, 4 on Langer, until tapped on Lang. Its time
  // starts once it can be seen – it waits for Vesta's window, the introduction or the tour like
  // every short message (E-259); with a sheet open it is in the sheet's head.
  let header = $state<HTMLElement | null>(null);
  let headerSeen = $state(true);
  $effect(() => {
    if (!header || !scroller || typeof IntersectionObserver === "undefined") return;
    const watch = new IntersectionObserver(([entry]) => (headerSeen = entry.isIntersecting), { root: scroller });
    watch.observe(header);
    return () => watch.disconnect();
  });
  const briefShown = $derived(conn.brief !== null && noticesAt !== "wait");
  $effect(() => {
    const brief = conn.brief;
    const ms = conn.timing.briefMs;
    if (!brief || noticesAt === "wait" || ms === null) return;
    const timer = setTimeout(() => {
      if (conn.brief?.round === brief.round) conn.brief = null;
    }, ms);
    return () => clearTimeout(timer);
  });

  // An emergency goes before the tour: it stops, and comes back later when it is still due.
  $effect(() => {
    if (layers.emergency && touring) touring = null;
  });

  $effect(() => {
    if (!snap || touring || snap.tablet) return;
    const seen = snap.prefs.tour ?? [];
    const due = tourDue({ level, seen, tablet: false, introducing: Boolean(snap.onboarding.person) || setupPending, busy: topLayer(layers) !== null });
    if (!due) return;
    const steps = tourFor(level, snap.user.isAdmin, seen);
    const quiet = lastNoticeAt === null ? 0 : lastNoticeAt + TOUR_QUIET_MS - Date.now();
    const timer = setTimeout(() => {
      if (!tourMayStart(layers, lastNoticeAt, Date.now())) return;
      if (steps.length) touring = steps;
      else conn.send({ type: "set_pref", key: "tour", value: toured(level, seen) });
    }, Math.max(1500, quiet));
    return () => clearTimeout(timer);
  });
  function endTour(): void {
    touring = null;
    conn.send({ type: "set_pref", key: "tour", value: toured(level, snap?.prefs.tour ?? []) });
  }
  function startTour(): void {
    sheet = "none";
    touring = tourFor(level, Boolean(snap?.user.isAdmin), [], true);
  }
  // Her voice for a text of the tour or the introduction, when it is on for this device.
  function readAloud(key: string): void {
    if (aloud()) conn.send({ type: "vesta_read", key });
  }
  let voiceOn = $state(true);
  $effect(() => {
    voiceOn = aloud();
  });

  // Vesta waits with a question while another screen is open (E-252).
  $effect(() => {
    conn.setBusy(sheet !== "none" || Boolean(snap?.onboarding?.person) || touring !== null || setupPending);
  });

  $effect(() => {
    if (activeTab === "brain" && level >= 4 && brainTab === "devices") conn.get("devices"); // when its tab opens; then it follows by itself (E-472)
  });
  $effect(() => {
    if (activeTab === "tidy") conn.get("tidy"); // Home Assistant netjes, when its page opens (E-449)
  });

  // Logboek (E-263): the lines when it opens, and again when the house changed – at most once
  // every few seconds, however busy the house is.
  let logAt = 0;
  let logPeriod = $state({ period: "recent", day: "" }); // the period chosen in Logboek (E-323)

  // Why Vesta has her colour (E-389): what else waits for you, as Huis counts it, and a tap
  // that opens Logboek at it – or, without Logboek (Ontzorgd), her window, where she says it.
  const waiting = $derived(
    snap ? othersWaiting(snap.level >= 3 ? snap.proposals ?? [] : [], snap.notifications ?? [], snap.why) : 0,
  );
  let logFocus = $state<Why | null>(null);
  function showWhy(why: Why | null): void {
    if (!tabs.includes("logbook")) {
      conn.send({ type: "orb_tap" });
      return;
    }
    sheet = "none";
    activeTab = "logbook";
    logFocus = why;
  }
  function getLog(): void {
    logAt = Date.now();
    const { period, day } = logPeriod;
    conn.get("logbook", period === "recent" ? {} : { period, day });
  }
  $effect(() => {
    if (activeTab !== "logbook" || !snap) return;
    const timer = setTimeout(getLog, Math.max(0, logAt + 3000 - Date.now()));
    return () => clearTimeout(timer);
  });
  // A conversation with Vesta ends up in Logboek: fetch it when her window closes.
  let vestaWasOpen = false;
  $effect(() => {
    const open = conn.vesta.open;
    if (vestaWasOpen && !open && activeTab === "logbook") setTimeout(getLog, 400);
    vestaWasOpen = open;
  });

  function open(name: SheetName): void {
    sheet = name;
    focusSection = null;
    conn.said = null;
    if (name === "readiness") conn.get("readiness");
    if (name === "settings") {
      conn.get("settings");
      conn.get("energy_settings");
      conn.get("integration");
    }
    if (name === "tidy") conn.get("tidy");
  }

  function talk(text: string): void {
    conn.send({ type: "talk", text });
  }

  function answer(proposal: number, code: string): void {
    conn.send({ type: "answer", proposal, answer: code });
  }

  function noticeAction(notice: Pick<Notice, "id" | "proposal">, action: string, extra: Record<string, unknown> = {}): void {
    const own = ["snooze", "dismiss", "mute", "unmute"]; // Later, Klaar, Niet meer tonen, Weer tonen (E-372)
    if (notice.proposal !== null && !own.includes(action)) answer(notice.proposal, action);
    else conn.send({ type: "notification", id: notice.id, action, ...extra });
    if (activeTab === "logbook") setTimeout(getLog, 400);
  }

  function pause(minutes: number | null, domain: string | null): void {
    conn.send({ type: "pause", minutes, domain });
    sheet = "none";
  }

  function whyNot(entity: string): void {
    conn.send({ type: "why_not", entity });
  }
</script>

{#if snap?.safeMode}
  <!-- After a failed update (E-301): only this screen; the emergency reactions keep working. -->
  <SafeMode reason={snap.safeMode} admin={snap.user.isAdmin} {t} />
{:else if snap && setupPending}
  <!-- Not set up yet (E-277): only the start screen and the steps, on every level and on a
       wall tablet; Help stays. -->
  <Setup {snap} settings={conn.data.settings as HouseSettings | undefined} integration={conn.data.integration as IntegrationView | undefined}
    preview={conn.data.ai_preview as string | undefined}
    {t} send={(m) => conn.send(m)} get={(w) => conn.get(w)} onhelp={() => open("help")}
    onread={readAloud} onspeak={canListen() ? (step) => void conn.listenFor(step) : undefined}
    heard={conn.introHeard} listening={conn.introListening} />
  {#if sheet === "help"}
    <Sheet title={t("ui.manual.title")} closeLabel={t("ui.close")} wide full whole={big} onclose={closeHelp}>
      <Help {t} manual={snap.manual} lang={snap.language} theme={shownTheme} large={largeText}
        name={snap.vesta?.name ?? "Vesta"} focus={helpFocus} bind:big onclose={closeHelp} />
    </Sheet>
  {/if}
{:else if snap?.tablet}
  <Tablet {snap} {orb} {t} speaking={conn.speaking} listening={conn.listening} said={conn.said?.text ?? null} send={(m) => conn.send(m)} onpause={() => open("pause")}
    onanswer={answer} onexit={() => { setTabletArea(null); location.reload(); }} />
  {#if sheet === "pause"}
    <Sheet title={t("ui.sheet.pause")} closeLabel={t("ui.close")} onclose={() => (sheet = "none")}>
      <PauseSheet {snap} {t} onpause={pause} />
    </Sheet>
  {/if}
{:else}
<div class="app" class:carefree={level === 1} class:wide-nav={level >= 2}>
  {#if !snap}
    <main class="loading" aria-busy="true"><p>HouseMind…</p></main>
  {:else}
    {#if level >= 2}
      <!-- On a computer the tabs are on the left: icon and word, like the tab bar (E-260). -->
      <nav class="side" data-tour="tabs" aria-label={t("ui.menu")}>
        <span class="brand">{t("app.name")}</span>
        {#each menu as tab (tab)}
          <button data-tour="tab-{tab}" aria-current={activeTab === tab ? "page" : undefined} onclick={() => pickTab(tab)}
            aria-label={tab === "logbook" && openNotices ? `${t(`tabs.${tab}`)}, ${openNotices}` : undefined}>
            <Icon name={tab} /><span class="label">{t(`tabs.${tab}`)}</span>
            {#if tab === "logbook" && openNotices}<span class="count">{openNotices}</span>{/if}
          </button>
        {/each}
      </nav>
    {/if}

    <div class="column">
      <!-- The page scrolls in its own area above Vraag Vesta and the tabs: nothing ever lies
           half under them (redesign step 3; WCAG 2.5.8 and 2.4.11). -->
      <!-- Scrolled down, the header is out of sight: the confirmation stays at the top right. -->
      {#if briefShown && noticesAt === "screen" && !headerSeen}{@render brief(true)}{/if}
      <div class="scroller" bind:this={scroller}>
      <!-- The same header on every screen and device: Vesta, Help, the tour, House settings,
           My preferences (E-260, B-108, E-506). -->
      <header bind:this={header} class:stacked={level >= 2} class:with-demo={Boolean(demo)}>
        {#if level === 1}
          <span class="name">{t("app.name")}</span>
        {:else}
          <div class="status" data-tour="vesta">
            <Orb state={orb} {level} size={40} speaking={conn.speaking} listening={conn.listening} label="{word}. {t('orb.tap_hint')}"
              ontap={() => conn.send({ type: "orb_tap" })} onhold={() => open("pause")} />
            <div class="words">
              <!-- A tap on her word explains her colours (E-404). -->
              <h1><button class="word" aria-label="{word}. {t('ui.orb_legend.open')}" onclick={() => open("orb")}><Glyph shape={look.shape} color={look.color} deep={look.deep} /> {word}</button></h1>
              {@render line()}
            </div>
          </div>
        {/if}
        {#if demo}
          <!-- Only in a demo house, next to the name (E-321, B-187; the administrator's wish):
               'Demohuis', its own time, and whether it stands still or runs slower or faster. The
               real house shows only the name. Under Vesta's status on levels 2–4, and on a phone
               in a row of its own. -->
          <div class="demo">
            <span class="demo-tag">{t("play.bar")}</span>
            <span class="demo-time">{[demoClock, demoState, demo.busy ? `${percent(demo.busy.done)}%` : ""].filter(Boolean).join(" · ")}</span>
          </div>
        {/if}
        {#if snap.tests?.length}
          <!-- A testkwartier runs (E-477): every screen of an administrator on Expert says so,
               with Stop, which ends every test and puts everything back. -->
          <div class="testing" role="status">
            <span class="testing-tag">{t("test.bar", { count: snap.tests.length, at: snap.tests[snap.tests.length - 1].until }, snap.tests.length)}</span>
            <button onclick={() => conn.send({ type: "test", do: "stop_all" })}>{t("test.bar.stop")}</button>
          </div>
        {/if}
        <div class="corner">
          {#if briefShown && noticesAt === "screen" && headerSeen}{@render brief()}{/if}
          <!-- Help opens the manual; beside it the tour, lit up together in the tour (E-506). -->
          <span class="pair" data-tour="help">
            <button class="round" aria-label={t("ui.help")} title={t("ui.help")} onclick={() => open("help")}><Icon name="help" size={20} /></button>
            <button class="round" aria-label={t("ui.tour.start")} title={t("ui.tour.start")} onclick={startTour}><Icon name="tour" size={20} /></button>
          </span>
          {#if testButton}
            <!-- Test het huis on a wide screen in a browser: the column on the right opens and
                 closes here; lit while it is open (E-474, the administrator's wish). -->
            <button class="round" aria-label={t("tabs.test")} title={t("tabs.test")} aria-pressed={testOpen}
              onclick={() => pickTab("test")}><Icon name="test" size={20} /></button>
          {/if}
          {#if snap.user.isAdmin}
            <button class="round" data-tour="settings" aria-label={t("ui.house_settings")} title={t("ui.house_settings")} onclick={() => open("settings")}><Icon name="settings" size={20} /></button>
          {/if}
          <button class="round" data-tour="prefs" aria-label={t("ui.my_preferences")} title={t("ui.my_preferences")} onclick={() => open("preferences")}><Icon name="person" size={20} /></button>
        </div>
      </header>

      {#if level === 1}
        <main class="calm">
          <div class="ringed" data-tour="vesta">
            <DayRing dots={snap.dayRing ?? []} size={250} {t} />
            <Orb state={orb} {level} size={180} speaking={conn.speaking} listening={conn.listening} label="{word}. {t('orb.tap_hint')}"
              ontap={() => conn.send({ type: "orb_tap" })} onhold={() => open("pause")} />
          </div>
          <div class="calm-words">
            <h1><button class="word" aria-label="{word}. {t('ui.orb_legend.open')}" onclick={() => open("orb")}><Glyph shape={look.shape} color={look.color} deep={look.deep} size={18} /> {word}</button></h1>
            {@render line()}
            {#if snap.mind?.length}
              <!-- What it did last, one sentence (E-218) -->
              <p class="mind">{snap.mind[0].at} · {snap.mind[0].text}</p>
            {/if}
            {#if snap.totalAreas}
              <p class="dots" role="img" aria-label={t("status.active_rooms", {}, snap.activeAreas)}>
                {#each Array(snap.totalAreas) as _, i (i)}<span class:on={i < snap.activeAreas}></span>{/each}
              </p>
            {/if}
            <p class="hint" role="status">{conn.said?.text ?? t("orb.tap_hint")}</p>
          </div>
          {#each snap.notifications ?? [] as notice (notice.id)}
            <!-- Only an emergency card can come with the calm screen (E-283). -->
            <div class="emergency-card"><NoticeCard {notice} {t} onaction={(a, extra) => noticeAction(notice, a, extra)} /></div>
          {/each}
          <div class="actions">
            <!-- The same "Vraag Vesta" as on the other levels (E-283, E-264). -->
            <button class="pill ask-vesta" data-tour="ask" onclick={() => conn.send({ type: "orb_tap" })}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
              {t("ui.ask_vesta")}
            </button>
            <button class="pill" data-tour="pause" onclick={() => open("pause")}>{t("ui.pause")}</button>
          </div>
        </main>
      {:else}
        <main class="levelled">
          {#if snap.news}
            <WhatsNew news={snap.news} {t} onread={() => conn.send({ type: "set_pref", key: "seen_version", value: snap?.version })} />
          {/if}
          {#if activeTab === "home"}
            <Home {snap} {t} {view} onview={(v) => (chosenView = v)} onroom={(r) => { room = r; open("room"); }} onanswer={answer}
              onnotice={noticeAction} onlogbook={() => (activeTab = "logbook")} {orb} />
          {:else if activeTab === "logbook"}
            <Logbook {snap} log={conn.data.logbook as LogbookData | undefined} {t} send={(m) => conn.send(m)}
              onnotice={noticeAction} onchanged={getLog}
              period={logPeriod.period} day={logPeriod.day} onperiod={(period, day) => { logPeriod = { period, day }; getLog(); }}
              focus={logFocus} onfocused={() => (logFocus = null)} />
          {:else if activeTab === "energy" && snap.energy}
            <Energy energy={snap.energy} {level} {t} onmore={(id) => { helpFocus = { kind: "manual", id }; open("help"); }} />
          {:else if activeTab === "brain"}
            <Brain {snap} {t} send={(m) => conn.send(m)} onmore={(id) => { helpFocus = { kind: "manual", id }; open("help"); }}
              onlimit={snap.user.isAdmin ? () => conn.onopen?.({ sheet: "settings", section: "max_step" }) : undefined}
              devices={conn.data.devices as DeviceRow[] | undefined} why={conn.why} onwhynot={whyNot}
              later={conn.data.later as Later | undefined}
              ontest={tabs.includes("test") ? () => { if (testAside) testOpen = true; else activeTab = "test"; } : undefined}
              tab={brainTab} ontab={pickBrainTab}
              knowledge={conn.data.house_knowledge as HouseKnowledgeView | undefined} get={(w) => conn.get(w)} focus={brainFocus}
              onexclude={(ids) => { conn.send({ type: "house_setting", key: "excluded", value: ids }); setTimeout(() => conn.get("devices"), 300); }} />
          {:else if activeTab === "tidy"}
            <Tidy tidy={conn.data.tidy as TidyView | undefined} lang={snap.language} {t} send={(m) => conn.send(m)} />
          {:else if activeTab === "test"}
            <TestPanel view={conn.data.test as TestView | undefined} demo={Boolean(demo)} {t} send={(m) => conn.send(m)} onhelp={testHelp} />
          {/if}
        </main>
      {/if}
      </div>
      {#if level >= 2}
        <!-- Talking goes through Vesta (E-264, B-111): one button opens her window, where you
             talk or type; earlier conversations are in Logboek. -->
        <div class="dock">
          <button class="ask" data-tour="talk" onclick={() => conn.send({ type: "orb_tap" })}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
            {t("ui.ask_vesta")}
          </button>
          <!-- Pause with one tap on every level, also with a keyboard (QA 2, H-13; E-83). -->
          <button class="dock-pause" data-tour="pause" aria-pressed={snap.paused.active} onclick={() => open("pause")}>{t("ui.pause")}</button>
        </div>
        <nav class="tabs" data-tour="tabs" aria-label={t("ui.menu")} style:--cols={menu.length}>
          {#each menu as tab (tab)}
            <button data-tour="tab-{tab}" aria-current={activeTab === tab ? "page" : undefined} onclick={() => pickTab(tab)}
              aria-label={tab === "logbook" && openNotices ? `${t(`tabs_short.${tab}`)}, ${openNotices}` : undefined}>
              <span class="icon"><Icon name={tab} />{#if tab === "logbook" && openNotices}<span class="count">{openNotices}</span>{/if}</span>
              <span class="label">{t(`tabs_short.${tab}`)}</span>
            </button>
          {/each}
        </nav>
      {/if}
    </div>

    {#if testOpen && testAside}
      <!-- Test het huis right of the main screen (E-474): you see the house while you test. -->
      <aside class="play-column" aria-label={t("test.open")}>
        <TestPanel view={conn.data.test as TestView | undefined} demo={Boolean(demo)} {t} send={(m) => conn.send(m)}
          onhelp={testHelp} onclose={() => (testOpen = false)} />
      </aside>
    {:else if playHere && demo}
      <!-- 'Speel het huis' right of the main screen (E-321, B-187): you see the house and the
           panel at the same time. -->
      <aside class="play-column" aria-label={t("play.open")}>
        <Play view={conn.data.play as PlayView | undefined} live={demo} language={snap.language} {t} send={(m) => conn.send(m)}
          onhelp={playHelp} onreadiness={() => showReadiness()}
          onlogbook={tabs.includes("logbook") ? () => (activeTab = "logbook") : undefined} />
      </aside>
    {/if}

    {#if !conn.online}<p class="offline" role="status">{t("ui.offline")}</p>{/if}

    {#if snap.onboarding.person}
      <Sheet title={t("ui.intro.title")} closeLabel={t("ui.intro.later")} onclose={() => conn.send({ type: "set_pref", key: "onboarded", value: true })}>
        <Onboarding {snap} settings={conn.data.settings as HouseSettings | undefined} {t} send={(m) => conn.send(m)} get={(w) => conn.get(w)}
          onread={readAloud} onspeak={canListen() ? (step) => void conn.listenFor(step) : undefined}
          heard={conn.introHeard} listening={conn.introListening} />
      </Sheet>
    {:else if sheet !== "none"}
      <Sheet title={sheet === "room" && liveRoom ? liveRoom.name : sheet === "help" ? t("ui.manual.title") : t(`ui.sheet.${sheet}`)} closeLabel={t("ui.close")} wide={sheet === "settings" || sheet === "preferences" || sheet === "tidy" || sheet === "help"}
        full={sheet === "help"} whole={sheet === "help" && big} onclose={() => { conn.said = null; closeHelp(); }}
        foot={noticesAt === "sheet" && notices.length ? noticeStack : undefined}
        brief={briefShown && noticesAt === "sheet" ? brief : undefined}>
        {#if sheet === "preferences"}
          <Preferences {snap} {t} send={(m) => conn.send(m)} onabout={() => open("about")} focus={focusSection}
            myData={conn.data.my_data as MyData | undefined} />
        {:else if sheet === "pause"}
          <PauseSheet {snap} {t} onpause={pause} />
        {:else if sheet === "help"}
          <Help {t} manual={snap.manual} lang={snap.language} theme={shownTheme} large={largeText}
            name={snap.vesta?.name ?? "Vesta"} focus={helpFocus} bind:big onclose={closeHelp}
            onask={(text) => talk(text)} onlisten={canListen() ? () => conn.send({ type: "orb_tap" }) : undefined} />
        {:else if sheet === "settings"}
          <HouseSettingsSheet settings={conn.data.settings as HouseSettings | undefined} energy={conn.data.energy_settings as EnergySettingsView | null | undefined}
            preview={conn.data.ai_preview as string | undefined}
           
            integration={conn.data.integration as IntegrationView | undefined}
            muted={conn.data.muted as { key: string; text: string }[] | undefined}
            {level} lang={snap.language} {t} send={(m) => conn.send(m)} get={(w) => conn.get(w)} onreadiness={() => showReadiness()}
            onknowledge={() => showReadiness("knowledge")} focus={focusSection} />
        {:else if sheet === "tidy"}
          <TidyGroup tidy={conn.data.tidy as TidyView | undefined} lang={snap.language} {t} send={(m) => conn.send(m)} />
        {:else if sheet === "readiness"}
          <Readiness parts={(conn.data.readiness as ReadyPart[] | undefined) ?? snap.readiness ?? []} lang={snap.language} {t} compact={!tabs.includes("brain")}
            onlimit={snap.user.isAdmin ? () => conn.onopen?.({ sheet: "settings", section: "max_step" }) : undefined}
            send={(m) => { conn.send(m); setTimeout(() => conn.get("readiness"), 400); }} />
        {:else if sheet === "orb"}
          <OrbLegend {t} now={orb} />
        {:else if sheet === "about"}
          <About version={snap.version} {t} speech={snap.speech} languages={snap.languageNames} haOldest={snap.haOldest ?? ""} name={snap.vesta?.name ?? "Vesta"} reaction={snap.reaction} />
        {:else if sheet === "room" && liveRoom}
          <RoomSheet room={liveRoom} {t} {level} onwhynot={whyNot} why={conn.why} send={(m) => conn.send(m)} />
        {/if}
      </Sheet>
    {/if}
  {/if}
  <!-- One stack for every short message (they go away by themselves): on the screen, at the
       foot of an open sheet, or waiting while Vesta's window, the introduction or the tour is
       open – never over something else (E-259). -->
  {#if noticesAt === "screen"}
    <div class="notices" class:low={level === 1}>{@render noticeStack()}</div>
  {/if}
  <!-- What the short confirmation says, for a screen reader – wherever it shows (E-174). -->
  <p class="sr" role="status">{briefShown ? conn.brief?.text : ""}</p>
</div>
{/if}
{#snippet brief(floating = false)}
  {#if conn.brief}
    <Brief text={conn.brief.text} {floating} tappable={conn.timing.briefMs === null} onclose={() => (conn.brief = null)} />
  {/if}
{/snippet}
{#snippet line()}
  <!-- Why she has her colour, instead of the calm line (E-389): a tap opens it in Logboek; on
       Ontzorgd, without Logboek, she says it. "+2": what else waits, as on Huis. -->
  {#if snap?.why}
    <p class="line why">
      <button class="reason" onclick={() => showWhy(snap?.why ?? null)}>{snap.why.text}</button>{#if waiting}<span aria-hidden="true"> · </span><button class="more" aria-label={t("ui.attention_more", {}, waiting)} onclick={() => showWhy(null)}>+{waiting}</button>{/if}
    </p>
  {:else}
    <p class="line">{snap?.line}</p>
  {/if}
{/snippet}

{#snippet noticeStack()}
  <Notices {notices} {t}
    onclose={(id) => conn.dismiss(id)}
    onconfirm={(n, yes) => { conn.send({ type: "confirm_rule", token: n.confirm, yes }); conn.dismiss(n.id); }}
    onwhy={(n) => conn.send({ type: "why", id: n.action })}
    onundo={(n) => { conn.send({ type: "undo", id: n.action }); conn.dismiss(n.id); }} />
{/snippet}
{#if conn.vesta.open && snap}
  <VestaWindow vesta={conn.vesta} {orb} level={snap.level} name={snap.vesta?.name ?? "Vesta"} speaking={conn.speaking} {t}
    onclose={() => conn.closeVesta()} onfinish={() => conn.finishListening()} oncancel={() => conn.cancelListening()}
    onretry={() => conn.retryMic()} ontype={talk} ontyping={(at) => conn.typingToVesta(at)} onanswer={(yes, word) => conn.answerVesta(yes, word)} onask={(action, word) => conn.answerAsk(action, word)}
    onopen={() => conn.openSetting()} ondecline={(word) => conn.declineOffer(word)}
    onmore={(more) => conn.readMore(more)} aloud={voiceOn} onaloud={(on) => { setAloud(on); voiceOn = on; }}
    houseMode={snap.houseMode} onmode={(mode) => conn.send({ type: "house_mode", mode })}
    listenMs={conn.timing.listenMs} onmoretime={() => conn.moreTime()}
    onchoice={(choice) => conn.choose(choice)} ai={snap.features?.ai === "on"}
    ontalk={() => conn.talkAgain()} canlisten={conn.canListenNow} />
{/if}
{#if touring && snap}
  <Tour steps={touring} {t} onread={readAloud} onend={endTour} />
{/if}

<style>
  :global(section.spot) { outline: 2px solid var(--accent); outline-offset: 6px; border-radius: 12px; }
  .notices { position: fixed; left: 50%; transform: translateX(-50%); bottom: calc(env(safe-area-inset-bottom, 0px) + 150px); width: min(560px, calc(100% - 32px)); z-index: 30; pointer-events: none; }
  .notices :global(.notice) { pointer-events: auto; }
  .notices.low { bottom: calc(env(safe-area-inset-bottom, 0px) + 16px); }
  .corner, .pair { display: flex; gap: 8px; flex: none; }
  .count { min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; background: var(--accent); color: var(--on-accent); font-size: calc(11px * var(--text-scale)); font-weight: 700; line-height: 18px; text-align: center; }
  /* The app and its scroll area are the frame for what is placed absolutely inside them
     (the hidden screen-reader words): otherwise those count against the whole page, which
     then becomes taller than the screen and slides up into black (found in the real house). */
  .app { position: relative; height: 100vh; height: 100dvh; display: flex; overflow: hidden; }
  .column { position: relative; flex: 1; min-width: 0; display: flex; flex-direction: column; min-height: 0; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .scroller { position: relative; flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
  header { position: relative; display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 14px 16px 6px; max-width: 1100px; width: 100%; margin: 0 auto; }
  .name { font-family: var(--font-display); font-weight: 600; font-size: calc(17px * var(--text-scale)); padding: 6px 4px; }
  .status { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .words { min-width: 0; }
  h1 { font-family: var(--font-display); font-weight: 600; font-size: calc(20px * var(--text-scale)); margin: 0; display: flex; align-items: center; gap: 8px; letter-spacing: -0.01em; }
  h1 .word { display: inline-flex; align-items: center; gap: 8px; padding: 0; border: none; background: none; font: inherit; color: inherit; letter-spacing: inherit; cursor: pointer; min-height: 32px; }
  .line { margin: 0; color: var(--muted); font-size: calc(15px * var(--text-scale)); }
  .round { flex: none; width: 44px; height: 44px; border-radius: 22px; border: 1px solid var(--edge); background: var(--surface); color: var(--muted); display: grid; place-items: center; cursor: pointer; }
  .round[aria-pressed="true"] { color: var(--accent); border-color: var(--accent); background: var(--raised); box-shadow: inset 0 0 0 1px var(--accent); }
  .loading { flex: 1; display: grid; place-items: center; color: var(--muted); }
  .calm { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 22px; padding: 20px; text-align: center; }
  .calm h1 { font-size: calc(30px * var(--text-scale)); justify-content: center; }
  .calm .line { color: var(--text); opacity: 0.85; font-size: calc(16px * var(--text-scale)); }
  /* The reason under her word (E-389): the same line, but a button to it; one line, with "…". */
  /* At most two lines, then "…": on a phone the header is narrow. */
  .line.why { display: flex; align-items: flex-end; gap: 0; min-width: 0; color: var(--text); }
  .line.why button { all: unset; cursor: pointer; text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 3px; }
  .line.why button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
  .line.why .reason { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden; min-width: 0; }
  .line.why .more { flex: none; white-space: nowrap; }
  .ringed { position: relative; display: flex; align-items: center; justify-content: center; width: 250px; height: 250px; }
  .calm-words { display: flex; flex-direction: column; gap: 8px; align-items: center; }
  .dots { display: flex; gap: 6px; margin: 4px 0 0; }
  /* Empty rooms a ring, rooms with someone a full dot: not colour alone (M-23, WCAG 1.4.1). */
  .dots { align-items: center; }
  .dots span { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--edge); }
  .dots span.on { width: 10px; height: 10px; border-color: var(--accent); background: var(--accent); box-shadow: 0 0 8px var(--accent); }
  .hint { margin: 0; color: var(--muted); max-width: 420px; }
  .mind { margin: 0; color: var(--muted); max-width: 420px; font-size: calc(14px * var(--text-scale)); line-height: 1.4; }
  .actions { display: flex; gap: 14px; }
  .pill { height: 52px; padding: 0 24px; border-radius: 26px; border: 1px solid var(--line); background: var(--surface); cursor: pointer; font-weight: 500; }
  .pill.ask-vesta { display: inline-flex; align-items: center; gap: 8px; }
  .emergency-card { width: min(100%, 420px); }
  .levelled { flex: 1; padding: 10px 16px 16px; display: flex; flex-direction: column; gap: 16px; max-width: 1100px; width: 100%; margin: 0 auto; }
  .dock { flex: none; padding: 8px 16px 10px; display: flex; justify-content: center; gap: 10px; border-top: 1px solid var(--line); }
  .dock-pause { min-height: 52px; padding: 0 20px; border-radius: 26px; border: 1px solid var(--edge); background: var(--surface); color: var(--text); font: inherit; font-weight: 500; cursor: pointer; }
  .dock-pause[aria-pressed="true"] { border-color: var(--text); font-weight: 600; }
  .ask { display: inline-flex; align-items: center; gap: 10px; min-height: 52px; padding: 0 24px; border-radius: 26px; border: 1px solid var(--accent); background: var(--accent); color: var(--on-accent); font: inherit; font-weight: 600; cursor: pointer; }
  .tabs { flex: none; display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 1fr)); border-top: 1px solid var(--line); background: var(--surface); padding-bottom: env(safe-area-inset-bottom); }
  .tabs button { min-height: 60px; border: none; border-radius: 0; background: none; color: var(--muted); cursor: pointer; font-size: calc(12px * var(--text-scale)); padding: 6px 2px 4px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; min-width: 0; }
  .tabs .icon { position: relative; display: grid; place-items: center; }
  .tabs .icon .count { position: absolute; top: -6px; left: 14px; }
  .tabs .label { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tabs button[aria-current="page"] { color: var(--accent); font-weight: 600; }
  .side { display: none; }
  @media (min-width: 900px) {
    .wide-nav .side { display: flex; flex-direction: column; gap: 2px; width: 230px; flex: none; padding: 18px 12px; border-right: 1px solid var(--line); background: var(--surface); overflow-y: auto; }
    .wide-nav .tabs { display: none; }
    .side .brand { font-family: var(--font-display); font-weight: 600; font-size: calc(18px * var(--text-scale)); padding: 4px 12px 14px; }
    .side button { text-align: left; min-height: 44px; padding: 0 12px; border-radius: 12px; border: none; background: none; color: var(--muted); cursor: pointer; display: flex; align-items: center; gap: 12px; }
    .side .label { flex: 1; min-width: 0; }
    .side button[aria-current="page"] { background: var(--raised); color: var(--text); font-weight: 600; }
    .side button[aria-current="page"] :global(svg) { color: var(--accent); }
  }
  header.with-demo { flex-wrap: wrap; row-gap: 8px; }
  .demo { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 10px; font-size: calc(14px * var(--text-scale)); margin-right: auto; }
  header.stacked .demo { order: 3; flex-basis: 100%; }
  @media (max-width: 599px) { .demo { order: 3; flex-basis: 100%; } }
  .testing { display: flex; align-items: center; gap: 8px; font-size: calc(14px * var(--text-scale)); }
  header.stacked .testing { order: 3; }
  .testing-tag { padding: 2px 10px; border-radius: 11px; border: 1px solid var(--accent); color: var(--accent); font-weight: 600; line-height: 18px; }
  .testing button { min-height: 32px; padding: 0 12px; border-radius: 16px; border: 1px solid var(--edge); background: var(--raised); color: var(--text); cursor: pointer; }
  .demo-tag { padding: 2px 10px; border-radius: 11px; border: 1px solid var(--learning); color: var(--learning); font-weight: 600; line-height: 18px; }
  .demo-time { color: var(--muted); font-variant-numeric: tabular-nums; }
  .play-column { flex: none; width: 420px; min-height: 0; overflow-y: auto; padding: 18px 16px 28px; border-left: 1px solid var(--line); background: var(--surface); }
  .offline { position: fixed; top: 8px; left: 50%; transform: translateX(-50%); margin: 0; padding: 6px 12px; border-radius: 10px; background: var(--alert); color: var(--on-alert); font-size: calc(14px * var(--text-scale)); z-index: 30; }
</style>

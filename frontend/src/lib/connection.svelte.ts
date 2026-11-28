// Live connection to the HouseMind backend over a websocket (through Ingress, E-86).

import { addNotice, type Notice } from "./notices";
import { afterListening, canListen, listen, type Listening } from "./listen";
import { timing, type Timing } from "./timing";
import { detectBrowser, micPermission, micProblem, type MicProblem } from "./michelp";
import { playVoice, unlockVoice } from "./voice";
import { tabletArea } from "./tablet";
import type { Snapshot, Toast } from "./types";
import { askedTo, CLOSED, closeDelay, mayAsk, partialTo, saidTo, typedTo, type VestaAsk, type VestaChoice, type VestaMore, type VestaOffer, type VestaWindow } from "./vesta";

export type { VestaWindow };

export interface Said {
  text: string;
  confirm?: string;
}

export type { Notice };

const LANGUAGE_CHECK_MS = 5000;
const READ_MS = 2500; // her words after your answer, before her next question (E-464)

/** The page around HouseMind: Home Assistant itself, when HouseMind is in its Ingress frame. */
export type Around = { document: { querySelector(selector: string): unknown } };

function aLanguage(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 && value.length <= 16 ? value : undefined;
}

/** The language Home Assistant shows on the page around HouseMind (E-25): Ingress shares HA's
 * origin, so its own element tells – also a profile language chosen on another device, which
 * HA keeps on the server. Nothing outside HA, or when the browser does not let us look. */
export function shownLanguage(around: Around | null | undefined): string | undefined {
  try {
    const ha = around?.document.querySelector("home-assistant") as
      | { hass?: { language?: unknown; locale?: { language?: unknown } } }
      | null
      | undefined;
    return aLanguage(ha?.hass?.language) ?? aLanguage(ha?.hass?.locale?.language);
  } catch {
    return undefined;
  }
}

/** The language of the Home Assistant profile (E-25): what HA shows around HouseMind, otherwise
 * the language last picked in HA on this browser. */
export function haLanguage(around: Around | null | undefined, storage: Pick<Storage, "getItem"> | undefined): string | undefined {
  const shown = shownLanguage(around);
  if (shown) return shown;
  try {
    const raw = storage?.getItem("selectedLanguage");
    if (!raw) return undefined;
    return aLanguage(JSON.parse(raw));
  } catch {
    return undefined;
  }
}

function pageAround(): Around | null {
  try {
    return window.parent !== window ? (window.parent as unknown as Around) : null;
  } catch {
    return null;
  }
}

function localStore(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

export class Connection {
  snapshot = $state<Snapshot | null>(null);
  said = $state<Said | null>(null);
  why = $state<Record<string, string>>({}); // what HouseMind does with a device, per device
  toast = $state<Toast | null>(null);
  brief = $state<{ text: string; round: number } | null>(null); // "Bewaard" in the header (E-174)
  notices = $state<Notice[]>([]);
  private seq = 0;
  online = $state(false);
  waiting = $state(false); // a question or Orb tap is on its way: the Orb thinks (E-159)
  speaking = $state(false); // Vesta's voice is playing on this device
  listening = $state(false); // the microphone is open: Vesta listens (E-225)
  vesta = $state<VestaWindow>({ ...CLOSED });
  private insecureShown = false;
  private pendingAsk: VestaAsk | null = null; // a question waiting until the person is ready (E-252)
  introHeard = $state<{ step: string; value: number | boolean | null; heard: string | null } | null>(null);
  introListening = $state(false); // listening for an answer in the introduction (E-253)
  private askTimer: ReturnType<typeof setTimeout> | null = null;
  private askFrom = 0; // her next question not before this moment: her last words first (E-464)
  private busy = false; // another screen (a sheet) is open
  private atField = false; // in the typing field of her window (E-264)
  private watching = false;
  private micOk = canListen();
  private listenNext = false;
  private listenTimer: ReturnType<typeof setTimeout> | null = null;
  private closeTimer: ReturnType<typeof setTimeout> | null = null;
  private current: Listening | null = null;
  private waitTimer: ReturnType<typeof setTimeout> | null = null;
  data = $state<Record<string, unknown>>({});
  private ws: WebSocket | null = null;
  private retry = 1000;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private returning = false; // listening for the person coming back (E-456)
  private helloLanguage: string | undefined; // HA's language as last told to the server
  private languageTimer: ReturnType<typeof setInterval> | null = null;

  connect(): void {
    this.listenForReturn();
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    const url = new URL("api/ws", window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      this.online = true;
      this.retry = 1000;
      this.hello();
      this.followLanguage();
    };
    ws.onmessage = (event) => this.receive(JSON.parse(event.data as string));
    ws.onclose = () => {
      if (this.ws !== ws) return; // an older try, already followed by a new one
      this.online = false;
      this.retryTimer = setTimeout(() => this.connect(), this.retry);
      // Each try waits twice as long: on the screen at most ten seconds, in the background
      // at most half a minute (E-456).
      this.retry = Math.min(this.retry * 2, document.hidden ? 30000 : 10000);
    };
  }

  /** Back on the screen, or the network is back: try at once, not after a wait that may have
   * grown to half a minute in the background (E-456). */
  private reconnectNow(): void {
    if (this.online || document.hidden || this.ws?.readyState === WebSocket.CONNECTING) return;
    this.retry = 1000;
    this.connect();
  }

  private listenForReturn(): void {
    if (this.returning || typeof window === "undefined") return;
    this.returning = true;
    document.addEventListener("visibilitychange", () => this.reconnectNow());
    window.addEventListener("online", () => this.reconnectNow());
    window.addEventListener("pageshow", () => this.reconnectNow());
    window.addEventListener("focus", () => this.reconnectNow());
  }

  private hello(): void {
    this.helloLanguage = haLanguage(pageAround(), localStore());
    this.send({
      type: "hello",
      browserLanguage: navigator.language,
      haLanguage: this.helloLanguage,
      tablet: tabletArea(),
      mic: this.micOk,
      partial: true, // only what changed after the first picture (S4)
    });
  }

  /** HA may still switch to the profile language just after it opened, or the person picks
   * another one in their profile: HouseMind follows within a few seconds. */
  private followLanguage(): void {
    if (this.languageTimer) return;
    this.languageTimer = setInterval(() => {
      if (this.online && haLanguage(pageAround(), localStore()) !== this.helloLanguage) this.hello();
    }, LANGUAGE_CHECK_MS);
  }

  receive(message: Record<string, unknown>): void {
    switch (message.type) {
      case "snapshot": {
        // After the first picture only the parts that changed (S4): merge them in.
        const snap = message.snapshot as Record<string, unknown>;
        if (message.partial && this.snapshot) {
          const next: Record<string, unknown> = { ...this.snapshot, ...snap };
          for (const key of (message.gone as string[] | undefined) ?? []) delete next[key];
          this.snapshot = next as unknown as Snapshot;
        } else {
          this.snapshot = snap as unknown as Snapshot;
        }
        break;
      }
      case "say": {
        this.stopWaiting();
        const confirm = message.confirm as string | undefined;
        this.said = { text: message.text as string, confirm };
        const voice = typeof message.voice === "string" ? message.voice : undefined;
        const heard = typeof message.heard === "string" ? message.heard : undefined;
        const ask = (message.ask as VestaAsk | undefined) ?? null; // her open question (E-349)
        const choices = (message.choices as VestaChoice[] | undefined) ?? null;
        const own = (voice && !confirm && tabletArea()) || confirm || message.window === true || !!ask || !!choices;
        if (own && !this.vesta.open) {
          // On a wall tablet she always speaks in the window; a yes or no question and her "why"
          // are hers on every screen (E-252).
          if (this.closeTimer) clearTimeout(this.closeTimer);
          this.vesta = { ...CLOSED, open: true };
        }
        if (this.vesta.open) {
          // In the window: your words, then hers – not again as a message below (B-85).
          this.vesta = saidTo(
            this.vesta, message.text as string, heard, confirm, (message.offer as VestaOffer | undefined) ?? null,
            message.more as VestaMore | undefined, message.ai === true, ask, choices,
          );
          this.closeLater();
          this.askAfter(voice ? 6000 : READ_MS); // a waiting question after her answer (E-252)
        } else {
          this.notify({ kind: "said", text: message.text as string, confirm, voice, heard, ms: confirm ? null : this.timing.noticeMs });
        }
        if (message.listen === true) {
          // She asked "Yes?": listen once she has finished speaking, or soon without sound.
          this.listenNext = true;
          if (this.listenTimer) clearTimeout(this.listenTimer);
          this.listenTimer = setTimeout(() => this.startListening(), 2500);
        }
        break;
      }
      case "partial":
        // An AI's answer as it is written: it grows in her window, her answer replaces it (E-159).
        this.vesta = partialTo(this.vesta, message.text as string);
        break;
      case "vesta_ask":
        this.pendingAsk = message as unknown as VestaAsk;
        this.tryAsk();
        break;
      case "open":
        // "Ja" aloud to "Zal ik het voor je openen?" (E-244).
        this.openSetting({ sheet: message.sheet as string, section: message.section as string });
        break;
      case "choice":
        // "Pauze" or "Help" said aloud after a tap without an AI (E-349): the app opens it.
        if (message.choice === "pause" || message.choice === "help") {
          this.closeVesta();
          this.onchoose?.(message.choice);
        }
        break;
      case "intro_heard":
        // A spoken answer in the introduction: a level, or yes or no (E-253).
        this.introHeard = {
          step: message.step as string,
          value: (message.value as number | boolean | null) ?? null,
          heard: (message.heard as string | null) ?? null,
        };
        break;
      case "vesta_ask_gone":
        // Answered somewhere else (the notification list, another phone).
        if (this.pendingAsk?.key === message.key) this.pendingAsk = null;
        if (this.vesta.ask?.key === message.key) {
          this.vesta = { ...this.vesta, ask: null };
          this.closeLater();
        }
        break;
      case "voice":
        if (this.listenTimer) clearTimeout(this.listenTimer);
        playVoice(
          message.audio as string,
          (on) => {
            this.speaking = on;
            if (this.vesta.open && this.vesta.phase !== "listening") {
              this.vesta = { ...this.vesta, phase: on ? "speaking" : "idle" };
              if (!on) this.closeLater();
            }
            if (!on && this.listenNext) this.listenTimer = setTimeout(() => this.startListening(), 200);
            if (!on) this.askAfter(800); // the next question after she finished speaking
          },
          message.byItself === true,
        );
        this.listenTimer = setTimeout(() => !this.speaking && this.startListening(), 4000);
        break;
      case "toast":
        this.toast = { id: message.id as number, text: message.text as string, undo: Boolean(message.undo) };
        this.notify({ kind: "action", text: message.text as string, action: message.id as number, undo: Boolean(message.undo), ms: this.timing.noticeMs });
        break;
      case "saved":
        // A short confirmation ("Bewaard") is plain text in the header, not a notice (E-174);
        // the same one again starts its time again. Longer messages stay a notice with Oké.
        if (message.brief === true) this.brief = { text: message.text as string, round: (this.brief?.round ?? 0) + 1 };
        else this.notify({ kind: "saved", text: message.text as string, ms: this.timing.savedMs });
        break;
      case "data":
        if (message.what === "why") {
          const why = message.data as { entity: string; text: string };
          this.why = { ...this.why, [why.entity]: why.text };
        } else if (message.what === "device_states") {
          // Brein › Apparaten follows the house: only the states that changed (E-472).
          const now = message.data as Record<string, string>;
          const rows = this.data.devices as { id: string; state: string }[] | undefined;
          if (rows) this.data = { ...this.data, devices: rows.map((r) => (r.id in now ? { ...r, state: now[r.id] } : r)) };
        } else {
          this.data = { ...this.data, [message.what as string]: message.data };
        }
        break;
    }
  }

  send(message: Record<string, unknown>): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(message));
    if (message.type === "orb_tap") {
      // A tap opens the Vesta window: a new conversation (E-236).
      if (this.closeTimer) clearTimeout(this.closeTimer);
      // Without a secure connection she cannot listen here: the first time she says why and
      // how to fix it (also in the HA app at home), later a short line (E-237).
      const insecure = typeof window !== "undefined" && !window.isSecureContext;
      const first = insecure && !this.insecureShown;
      if (insecure) this.insecureShown = true;
      const micHelp = first ? { problem: "insecure" as MicProblem, browser: detectBrowser(navigator.userAgent) } : null;
      this.atField = false;
      this.vesta = { ...this.vesta, open: true, phase: "thinking", turns: [], micHelp, noMic: insecure, confirm: null, typed: false };
    }
    if (message.type === "talk") {
      if (this.listening || this.listenNext) this.cancelListening(); // typing instead of talking
      // Typed to her – in the talk bar or in her window – is the same conversation as spoken,
      // in the same window, on every level (E-241).
      if (this.closeTimer) clearTimeout(this.closeTimer);
      this.vesta = typedTo(this.vesta, message.text as string);
    }
    if (message.type === "talk" || message.type === "orb_tap") {
      unlockVoice(); // still inside the tap: her voice may play from now on
      this.waiting = true;
      if (this.waitTimer) clearTimeout(this.waitTimer);
      this.waitTimer = setTimeout(() => this.stopWaiting(), 20000); // E-159: at most 20 s
    }
  }

  private async startListening(): Promise<void> {
    if (!this.listenNext || this.listening || this.speaking) return;
    this.listenNext = false;
    if (this.listenTimer) clearTimeout(this.listenTimer);
    if (this.closeTimer) clearTimeout(this.closeTimer);
    try {
      this.current = await listen(
        (chunk) => {
          if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(chunk);
        },
        (level) => {
          if (this.vesta.open) this.vesta.level = level;
        },
        this.timing.listenMs,
      );
    } catch (error) {
      // No permission, or this frame may not use the microphone: from now on she does not
      // invite on this device, and the window says why and how to switch it on (E-237).
      this.micOk = false;
      this.send({ type: "mic", ok: false });
      const problem = micProblem(error, window.isSecureContext, await micPermission());
      const micHelp = { problem, browser: detectBrowser(navigator.userAgent) };
      if (this.vesta.open) this.vesta = { ...this.vesta, phase: "idle", noMic: true, micHelp };
      return; // the window stays open until it is closed: there is something to read
    }
    this.listening = true;
    this.send({ type: "listen_start" });
    if (this.vesta.open) this.vesta = { ...this.vesta, phase: "listening", listenStart: performance.now(), level: 0 };
    const heard = await this.current.done;
    this.current = null;
    this.listening = false;
    const next = afterListening(heard);
    this.send({ type: next });
    if (this.vesta.open) {
      // Nobody spoke, or Stop: the conversation ends; otherwise she hears it and answers. For
      // the next thing you tap on her again (E-464).
      const ended = next === "listen_cancel";
      this.vesta = { ...this.vesta, phase: ended ? "idle" : "hearing", level: 0 };
      if (ended) this.closeLater();
    }
  }

  /** A tap on Vesta in her open window (E-464): she listens now, or once she finished speaking,
   * and the conversation and her open question stay; a tap while she listens: done. */
  talkAgain(): void {
    if (this.listening) {
      this.finishListening();
      return;
    }
    if (this.vesta.phase === "thinking" || this.vesta.phase === "hearing") return;
    unlockVoice();
    this.atField = false;
    this.vesta = { ...this.vesta, typed: false };
    this.listenNext = true;
    void this.startListening();
  }

  /** "Zeg het" in the introduction: listen once, the server says what the answer means. */
  async listenFor(step: string): Promise<void> {
    if (this.listening || this.speaking) return;
    try {
      this.current = await listen(
        (chunk) => {
          if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(chunk);
        },
        () => undefined,
        this.timing.listenMs,
      );
    } catch {
      this.micOk = false;
      this.send({ type: "mic", ok: false });
      return;
    }
    this.listening = true;
    this.introListening = true;
    this.send({ type: "listen_start" });
    const heard = await this.current.done;
    this.current = null;
    this.listening = false;
    this.introListening = false;
    if (afterListening(heard) === "listen_cancel") this.send({ type: "listen_cancel" });
    else this.send({ type: "listen_end", intro: step });
  }

  /** Whether this device may listen (a microphone that worked, a safe connection). */
  get canListenNow(): boolean {
    return this.micOk;
  }

  /** After switching the microphone on: try again. */
  retryMic(): void {
    this.micOk = true;
    this.send({ type: "mic", ok: true });
    this.vesta = { ...this.vesta, noMic: false, micHelp: null };
    this.listenNext = true;
    void this.startListening();
  }

  /** Klaar: stop listening and send what was said. */
  finishListening(): void {
    this.current?.finish();
  }

  /** Stop: stop listening and throw it away. */
  cancelListening(): void {
    this.listenNext = false;
    this.current?.cancel();
  }

  /** Lees meer: the manual or Help opens there (set by the app). */
  onread: ((more: VestaMore) => void) | null = null;

  /** Lees meer under her answer: her window closes and the manual opens at that part (E-246). */
  readMore(more: VestaMore): void {
    this.closeVesta();
    this.onread?.(more);
  }

  /** Where a setting is opens the screen there (set by the app). */
  onopen: ((offer: VestaOffer) => void) | null = null;

  /** Pauze… or Help among her choices: the app opens it (set by the app, E-349). */
  onchoose: ((what: "pause" | "help") => void) | null = null;

  /** One of her choices (E-349): after a tap without an AI she tells or the app opens it; a
   * choice of the AI goes back to the AI as your words. */
  choose(choice: VestaChoice): void {
    this.cancelListening();
    if (choice.kind === "ai") {
      this.vesta = { ...this.vesta, choices: null };
      this.send({ type: "talk", text: choice.text });
      return;
    }
    if (choice.id === "pause" || choice.id === "help") {
      this.closeVesta();
      this.onchoose?.(choice.id);
      return;
    }
    this.vesta = { ...this.vesta, choices: null, phase: "thinking", turns: [...this.vesta.turns, { who: "you", text: choice.text }] };
    this.send({ type: "choice", choice: choice.id });
  }

  /** "Ja, open het": her window closes and that part of the screen opens (E-244). */
  openSetting(offer?: VestaOffer): void {
    const wanted = offer ?? this.vesta.offer;
    if (!wanted) return;
    this.vesta = { ...this.vesta, offer: null };
    this.closeVesta();
    this.onopen?.(wanted);
  }

  /** "Nee" to her offer: it stays closed. */
  declineOffer(word: string): void {
    if (!this.vesta.offer) return;
    this.vesta = { ...this.vesta, offer: null, turns: [...this.vesta.turns, { who: "you", text: word }] };
    this.send({ type: "offer_closed" });
    this.closeLater();
  }

  /** Another screen opens or closes: she waits with her question until it is closed. */
  setBusy(busy: boolean): void {
    this.busy = busy;
    if (!busy) this.askSoon(600);
  }

  private askAfter(ms: number): void {
    this.askFrom = Date.now() + ms;
    this.askSoon(ms);
  }

  private askSoon(ms: number): void {
    if (!this.pendingAsk) return;
    if (this.askTimer) clearTimeout(this.askTimer);
    this.askTimer = setTimeout(() => this.tryAsk(), ms);
  }

  /** Her question, when the person is ready for it (E-252): the app in view, nothing else
   * open, not typing, her window closed or quiet. Otherwise she tries again a bit later. */
  tryAsk(): void {
    const ask = this.pendingAsk;
    if (!ask || typeof document === "undefined") return;
    this.watch();
    const wait = this.askFrom - Date.now();
    if (wait > 0) {
      this.askSoon(wait);
      return;
    }
    const active = document.activeElement;
    const typing = !!active && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName);
    const visible = document.visibilityState === "visible";
    if (this.speaking || this.listening || !mayAsk({ visible, busy: this.busy, typing, window: this.vesta, follow: ask.follow })) {
      this.askSoon(3000);
      return;
    }
    this.pendingAsk = null;
    if (this.closeTimer) clearTimeout(this.closeTimer);
    this.vesta = askedTo(this.vesta, ask);
    this.send({ type: "ask_shown", key: ask.key });
  }

  private watch(): void {
    if (this.watching || typeof document === "undefined") return;
    this.watching = true;
    document.addEventListener("visibilitychange", () => this.askSoon(500));
    document.addEventListener("focusout", () => this.askSoon(1500));
  }

  /** A button under her question: the answer goes where the notification list would send it. */
  answerAsk(action: string, word: string): void {
    const ask = this.vesta.ask;
    if (!ask) return;
    this.cancelListening();
    if (action === "why") {
      // Waarom? (E-349): she says why she asks; her question and its buttons stay.
      this.vesta = { ...this.vesta, phase: "thinking", turns: [...this.vesta.turns, { who: "you", text: word }] };
      this.send({ type: "ask_why", key: ask.key });
      return;
    }
    this.vesta = { ...this.vesta, ask: null, phase: "thinking", turns: [...this.vesta.turns, { who: "you", text: word }] };
    if (ask.kind === "proposal") this.send({ type: "answer", proposal: ask.proposal, answer: action, via: "vesta" });
    else this.send({ type: "notification", id: ask.notification, action, via: "vesta" });
  }

  /** In the typing field of her window (E-264): she stops listening – typing instead of
   * talking – and the window stays open a minute after the last key, instead of a few seconds. */
  typingToVesta(at: boolean): void {
    this.atField = at;
    if (at && (this.listening || this.listenNext)) this.cancelListening();
    this.closeLater();
  }

  closeVesta(): void {
    this.atField = false;
    if (this.vesta.ask) this.send({ type: "ask_closed", key: this.vesta.ask.key }); // stays in Notifications
    if (this.vesta.offer) this.send({ type: "offer_closed" });
    if (this.vesta.open) this.send({ type: "vesta_closed" }); // the conversation is over (E-349)
    this.cancelListening();
    if (this.closeTimer) clearTimeout(this.closeTimer);
    this.vesta = { ...CLOSED };
  }

  /** Ja or Nee on her question in the window (a house rule); `word` is the button's word. */
  answerVesta(yes: boolean, word: string): void {
    const token = this.vesta.confirm;
    if (!token) return;
    this.vesta = { ...this.vesta, confirm: null, phase: "thinking", turns: [...this.vesta.turns, { who: "you", text: word }] };
    this.send({ type: "confirm_rule", token, yes });
  }

  private closeLater(): void {
    if (this.closeTimer) clearTimeout(this.closeTimer);
    const ms = closeDelay(this.vesta, this.timing.typedCloseMs, this.timing.answerCloseMs);
    if (ms === null) return; // something to read or answer: it stays until closed
    this.closeTimer = setTimeout(() => {
      if (!this.listening && !this.speaking && !this.listenNext && this.vesta.phase === "idle") this.closeVesta();
    }, this.atField ? Math.max(ms, this.timing.typedCloseMs) : ms);
  }

  /** The person's own time to talk and to read (E-299). */
  get timing(): Timing {
    return timing(this.snapshot?.prefs?.timing);
  }

  /** "Nog even" (E-299): the time starts again – listening, or her window staying open. */
  moreTime(): void {
    if (this.current && this.vesta.phase === "listening") {
      this.current.more();
      this.vesta = { ...this.vesta, listenStart: performance.now() };
      return;
    }
    this.closeLater();
  }

  private stopWaiting(): void {
    this.waiting = false;
    if (this.waitTimer) clearTimeout(this.waitTimer);
    this.waitTimer = null;
  }

  notify(notice: Omit<Notice, "id" | "round">): void {
    this.notices = addNotice(this.notices, notice, ++this.seq);
  }

  dismiss(id: number): void {
    this.notices = this.notices.filter((n) => n.id !== id);
  }

  get(what: string, extra: Record<string, unknown> = {}): void {
    this.send({ type: "get", what, ...extra }); // extra: the period of Logboek (E-323)
  }
}

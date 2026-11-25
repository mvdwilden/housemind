// Vesta's voice in the app (E-223, E-231, B-82). HA turns her line into sound; the app plays
// it. A browser only plays sound after a tap, so a tap on the Orb (or Ask) unlocks it. Per
// device you can switch her voice off: then you only read her words.

const KEY = "housemind.voice";
// A very short silent sound, played during the first tap so later lines may play.
const SILENT =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=";

let player: HTMLAudioElement | null = null;
// A second player whose sound goes through an analyser, so Vesta moves with her voice, like
// the meter while she listens (wish of the administrator). Only for sound from this page's
// own address: other sound would be silenced by the browser, so it plays the plain way.
let routed: HTMLAudioElement | null = null;
let analyser: AnalyserNode | null = null;
let samples: Uint8Array<ArrayBuffer> | null = null;
let current: HTMLAudioElement | null = null;
let talking = false;

/** Whether Vesta speaks aloud on this device (her words always show as text). */
export function voiceWanted(storage: Pick<Storage, "getItem"> | null): boolean {
  try {
    return storage?.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setVoiceWanted(storage: Pick<Storage, "setItem"> | null, on: boolean): void {
  try {
    storage?.setItem(KEY, on ? "on" : "off");
  } catch {
    // private window or blocked storage: it stays on for this visit
  }
}

function store(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Called inside the tap itself: from now on this page may play her voice. */
export function unlockVoice(): void {
  if (player || typeof Audio === "undefined" || !voiceWanted(store())) return;
  player = new Audio();
  player.src = SILENT;
  player.play().catch(() => undefined);
  try {
    const Context = window.AudioContext;
    if (!Context) return;
    routed = new Audio();
    routed.src = SILENT;
    routed.play().catch(() => undefined);
    const context = new Context();
    const source = context.createMediaElementSource(routed);
    analyser = context.createAnalyser();
    analyser.fftSize = 256;
    samples = new Uint8Array(new ArrayBuffer(analyser.fftSize));
    source.connect(analyser);
    analyser.connect(context.destination);
    context.resume().catch(() => undefined);
  } catch {
    routed = null;
    analyser = null;
  }
}

/** How loud her voice is now, 0 – 1, for the Orb to move with. Measured when the sound
 * comes through the analyser; otherwise a calm, voice-like wave while she speaks. */
export function voiceLevel(at: number): number {
  if (!talking) return 0;
  if (current !== null && current === routed && analyser && samples) {
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) sum += ((sample - 128) / 128) ** 2;
    return Math.min(1, Math.sqrt(sum / samples.length) * 4);
  }
  return speechWave(at);
}

/** A wave that moves like speech: syllables on a slower phrase. */
export function speechWave(at: number): number {
  const syllables = 0.5 + 0.5 * Math.sin(at / 95);
  const phrase = 0.6 + 0.4 * Math.sin(at / 410);
  return Math.max(0, Math.min(1, 0.15 + 0.7 * syllables * phrase));
}

function sameOrigin(url: string): boolean {
  try {
    return typeof location !== "undefined" && new URL(url, location.href).origin === location.origin;
  } catch {
    return false;
  }
}

/** Play one line; ``speaking`` follows it, for the Orb. Without a tap first, or with the voice
 * off on this device, nothing plays – the words are on the screen anyway. */
export function playVoice(url: string, speaking: (on: boolean) => void, byItself = false): void {
  if (!player || !voiceWanted(store())) return;
  // By herself she only speaks on a phone where HouseMind is open and in view (E-223).
  if (byItself && typeof document !== "undefined" && document.visibilityState !== "visible") return;
  const element = routed && sameOrigin(url) ? routed : player;
  current = element;
  element.onplaying = () => {
    talking = true;
    speaking(true);
  };
  element.onended = element.onerror = element.onpause = () => {
    talking = false;
    speaking(false);
  };
  element.src = url;
  element.play().catch(() => {
    talking = false;
    speaking(false);
  });
}

/** Whether her voice is on for this device. */
export function aloud(): boolean {
  return voiceWanted(store());
}

/** The speaker button in her window (E-253): her voice off or on for this device; off also
 * stops what she is saying now. The same setting as in My preferences. */
export function setAloud(on: boolean): void {
  setVoiceWanted(store(), on);
  if (!on) {
    player?.pause();
    routed?.pause();
  }
}

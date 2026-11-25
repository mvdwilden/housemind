// Listening after a tap (E-225, E-226, B-83). The microphone is open only while Vesta listens:
// at most 6 seconds, and it stops sooner once you are done talking. The sound goes as 16 kHz
// 16-bit mono to HouseMind and straight on to HA's speech recognition; nothing is kept.

export const LISTEN_MS = 6000;
const QUIET_MS = 1200; // this long quiet after speaking: done
const SPEAKING_LEVEL = 0.02;

/** Whether this screen can listen: a microphone and a safe connection (https or the HA app). */
export function canListen(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext === true &&
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function"
  );
}

/** The microphone's sound at its own rate → 16 kHz 16-bit, as speech recognition wants it. */
export function to16k(input: Float32Array, rate: number): Int16Array {
  const ratio = rate / 16000;
  const out = new Int16Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio)) || start + 1;
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    const value = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    out[i] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  return out;
}

/** How loud this piece is (0–1). */
export function loudness(input: Float32Array): number {
  let sum = 0;
  for (const v of input) sum += v * v;
  return Math.sqrt(sum / Math.max(1, input.length));
}

/** One time listening: ``finish`` (Klaar) stops and keeps what was said, ``cancel`` (Stop)
 * throws it away; ``done`` says whether it was cancelled and whether anyone spoke. */
export interface Heard {
  cancelled: boolean;
  spoke: boolean;
}
export interface Listening {
  finish(): void;
  cancel(): void;
  more(): void; // "Nog even": the time starts again (E-299)
  done: Promise<Heard>;
}

/** After listening (E-240): send what was said, or – when nobody spoke, or Stop – nothing:
 * then the conversation ends, and silence never goes to speech recognition. */
export function afterListening(heard: Heard): "listen_end" | "listen_cancel" {
  return heard.cancelled || !heard.spoke ? "listen_cancel" : "listen_end";
}

/** Five bars for the meter in the Vesta window (0–1 each), from how loud you speak. */
export function meter(level: number, at: number): number[] {
  const loud = Math.min(1, level * 8);
  return [0.55, 0.8, 1, 0.8, 0.55].map((shape, i) =>
    Math.max(0.08, loud * shape * (0.75 + 0.25 * Math.sin(at / 90 + i * 1.3))),
  );
}

/** Open the microphone (asks permission the first time; throws when it is not allowed) and
 * listen: every piece of sound goes to ``send``, how loud it is to ``onLevel``. */
export async function listen(
  send: (chunk: ArrayBuffer) => void,
  onLevel: (level: number) => void = () => undefined,
  maxMs = LISTEN_MS,
): Promise<Listening> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  let began = performance.now();
  let spoke = false;
  let lastSound = began;
  let stopped = false;
  let end: (heard: Heard) => void = () => undefined;
  const done = new Promise<Heard>((resolve) => (end = resolve));
  const stop = (cancelled: boolean) => {
    if (stopped) return;
    stopped = true;
    processor.onaudioprocess = null;
    processor.disconnect();
    source.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    void context.close();
    onLevel(0);
    end({ cancelled, spoke });
  };
  processor.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    send(to16k(input, context.sampleRate).buffer as ArrayBuffer);
    const level = loudness(input);
    onLevel(level);
    const now = performance.now();
    if (level > SPEAKING_LEVEL) {
      spoke = true;
      lastSound = now;
    }
    if (now - began > maxMs || (spoke && now - lastSound > QUIET_MS)) stop(false);
  };
  source.connect(processor);
  processor.connect(context.destination);
  const more = () => {
    began = performance.now();
    lastSound = began;
  };
  return { finish: () => stop(false), cancel: () => stop(true), more, done };
}

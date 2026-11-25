// The Orb on the screen holds each state a moment (E-217), so a change is seen.
import { untrack } from "svelte";
import { holdOrb, type OrbShown } from "./orb";

export class HeldOrb {
  shown = $state<OrbShown | null>(null);
  #timer: ReturnType<typeof setTimeout> | null = null;
  #wanted = "calm";

  get state(): string {
    return this.shown?.state ?? this.#wanted;
  }

  /** Call with the state the house wants to show; a change may wait a moment. */
  want(wanted: string): void {
    this.#wanted = wanted;
    this.#step();
  }

  #step = (): void => {
    const current = untrack(() => this.shown);
    const next = holdOrb(current, this.#wanted, Date.now());
    if (next.shown !== current) this.shown = next.shown;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = next.recheck === null ? null : setTimeout(this.#step, next.recheck);
  };
}

// Which groups someone opened or closed themselves (found in the real house: in Brein a group
// of devices opened and closed again within a second). A <details open={…}> is set again every
// time its list is drawn anew – and in a busy house that is all the time – so what was chosen
// by hand is kept here and wins over the usual state. A new search starts over.

import { untrack } from "svelte";

export class Opened {
  chosen = $state<Record<string, boolean>>({});

  /** Open or not: what was chosen by hand, else the usual state. */
  is(key: string, usual: boolean): boolean {
    return this.chosen[key] ?? usual;
  }

  /** A tap on the summary (or Enter/Space on it): the group goes the other way, and that is
   * kept. The browser's own toggle is stopped, so this state alone decides – else the two undo
   * each other. */
  flip(key: string, event: Event): void {
    const details = (event.currentTarget as HTMLElement).closest("details");
    if (!details) return;
    event.preventDefault();
    this.chosen = { ...this.chosen, [key]: !details.open };
  }

  /** A new search or filter: the usual state again. Not tracked: an effect that calls this
   * must react to the search only, never to a choice made by hand. */
  reset(): void {
    untrack(() => {
      if (Object.keys(this.chosen).length) this.chosen = {};
    });
  }
}

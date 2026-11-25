/** One short message in the stack: said, done (with Why? and Undo), or saved. */
export interface Notice {
  id: number;
  kind: "said" | "action" | "saved";
  text: string;
  confirm?: string;
  voice?: string; // who speaks: Vesta (E-231)
  heard?: string; // what she heard you say
  action?: number;
  undo?: boolean;
  ms: number | null; // how long it stays; null: until answered
  round: number; // goes up when the notice is used again: its bar starts over
}

export const MAX_NOTICES = 3;

/** The stack after a new message. "Saved." while a "Saved." is still showing – or the same
 * sentence again – does not stack up: that notice is used again and its bar starts over. Only
 * when it is gone does a new one appear. */
export function addNotice(notices: Notice[], notice: Omit<Notice, "id" | "round">, id: number): Notice[] {
  const same = notices.find(
    (n) =>
      !n.confirm &&
      !notice.confirm &&
      n.kind === notice.kind &&
      (notice.kind === "saved" || (notice.kind === "said" && n.text === notice.text)),
  );
  if (same) return notices.map((n) => (n === same ? { ...n, ...notice, round: n.round + 1 } : n));
  const next = [...notices, { ...notice, id, round: 0 }];
  // Too many: the oldest message that is not a question goes (found in a retest: an open
  // question fell out of the stack). Questions stay until answered.
  while (next.length > MAX_NOTICES) {
    const drop = next.findIndex((n) => !n.confirm);
    if (drop < 0) break;
    next.splice(drop, 1);
  }
  return next;
}

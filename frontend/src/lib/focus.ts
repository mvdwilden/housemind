// A window holds the keyboard (QA 2, H-14; WCAG 2.1.1, 2.4.3): when it opens the focus goes in,
// Tab and Shift+Tab stay inside, and when it closes the focus goes back to what opened it. Only
// the window opened last does this, so a question on top of a sheet keeps its own Tab.

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const open: HTMLElement[] = [];

function inside(node: HTMLElement): HTMLElement[] {
  return [...node.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.getClientRects().length > 0);
}

function onkey(event: KeyboardEvent): void {
  const node = open[open.length - 1];
  if (event.key !== "Tab" || !node) return;
  const items = inside(node);
  const active = document.activeElement;
  if (!items.length) {
    event.preventDefault();
    node.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  if (!node.contains(active)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  } else if (event.shiftKey && (active === first || active === node)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

/** `use:trap` on a window's own element (it needs tabindex="-1"). */
export function trap(node: HTMLElement): { destroy: () => void } {
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  if (!open.length) document.addEventListener("keydown", onkey, true);
  open.push(node);
  if (!node.contains(document.activeElement)) node.focus();
  return {
    destroy() {
      open.splice(open.indexOf(node), 1);
      if (!open.length) document.removeEventListener("keydown", onkey, true);
      // Back to the button that opened it, if it is still there; else to the window below.
      if (opener?.isConnected && opener !== document.body) opener.focus();
      else open[open.length - 1]?.focus();
    },
  };
}

// What this device chose (E-266, B-113): its colour scheme and text size (less motion follows
// the device itself, E-319). Kept in this
// browser only, so a wall tablet in the bedroom can be Nacht while a phone is Licht. A device
// that never chose starts from the person's earlier choice (the server's preferences).

const KEY = "housemind.device";

export interface ListChoice {
  rows?: number; // per page; 0 is all (E-325)
  columns?: string[]; // the details a row shows; none chosen: the list's own
  compact?: boolean;
}

export interface DeviceChoices {
  scheme?: string; // "auto" or the id of a colour scheme
  textSize?: "normal" | "large";
  lists?: Record<string, ListChoice>; // per list: a wall tablet can be compact, a phone not
}

function read(): DeviceChoices {
  try {
    const raw = JSON.parse(window.localStorage.getItem(KEY) ?? "{}") as unknown;
    return raw && typeof raw === "object" ? (raw as DeviceChoices) : {};
  } catch {
    return {}; // no storage (or blocked): the person's choice holds
  }
}

class Device {
  choices = $state<DeviceChoices>(read());

  set<K extends keyof DeviceChoices>(key: K, value: DeviceChoices[K]): void {
    this.choices = { ...this.choices, [key]: value };
    try {
      window.localStorage.setItem(KEY, JSON.stringify(this.choices));
    } catch {
      // Storage blocked: only for this visit.
    }
  }
}

export const device = new Device();

/** What this device chose for one list (E-325, B-196). */
export function listChoice(key: string): ListChoice {
  return device.choices.lists?.[key] ?? {};
}

export function setListChoice(key: string, part: ListChoice): void {
  const lists = device.choices.lists ?? {};
  device.set("lists", { ...lists, [key]: { ...lists[key], ...part } });
}

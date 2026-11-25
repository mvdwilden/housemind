// The set of device and room icons (E-508, B-434, design 79): a 24 grid, line 1.8, round ends,
// currentColor – the same style as Icon.svelte. The keys are the ones the server chooses
// (src/housemind/brain/icons.py ICONS); the drawings are those of the mockup.

export const DEVICE_PATHS: Record<string, string> = {
  lamp: "M8.6 15a6.4 6.4 0 1 1 6.8 0c-.7.5-1.1 1.2-1.1 2H9.7c0-.8-.4-1.5-1.1-2z M10 20.5h4",
  plug: "M9 3v4 M15 3v4 M7 7h10v4a5 5 0 0 1-10 0z M12 16v5",
  switch: "M8 7h8a5 5 0 0 1 0 10H8A5 5 0 0 1 8 7z M8 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
  button: "M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z M10 8h4v8h-4z",
  battery: "M5 7h12a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z M21.5 10.5v3",
  solar: "M3.5 20.5 5.5 13h13l2 7.5z M4.4 16.8h15.2 M10 13l-.6 7.5 M14 13l.6 7.5 M9 10a3 3 0 0 1 6 0 M12 3.5V5 M7.4 5.4l1 1 M16.6 5.4l-1 1",
  grid: "M10 3h4l3.2 18 M10 3 6.8 21 M8 8h8 M7.1 13.5h9.8 M7.4 13.5l9 7.5 M16.6 13.5l-9 7.5",
  energy: "M13 2 4 14h7l-1 8 9-12h-7z",
  charger: "M6.5 3h6a1.5 1.5 0 0 1 1.5 1.5V21H5V4.5A1.5 1.5 0 0 1 6.5 3z M3.5 21h12 M14 9h1.5a1.5 1.5 0 0 1 1.5 1.5v5a1.5 1.5 0 0 0 3 0V8.5L18 6.5 M10.3 7l-2 3.5h3l-2 3.5",
  car: "M5 17v2.5 M19 17v2.5 M3.5 17h17v-4.5L18.6 7.4a1.5 1.5 0 0 0-1.4-.9H6.8a1.5 1.5 0 0 0-1.4.9L3.5 12.5z M3.5 12.5h17 M7 14.8h.01 M17 14.8h.01",
  oven: "M6 3.5h12a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2z M4 8.5h16 M7.5 6h.01 M10.5 6h.01 M8 12h8v5H8z",
  fridge: "M7 2.5h10a2 2 0 0 1 2 2v15a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2z M5 10h14 M8.5 5.8v1.6 M8.5 13v3",
  washer: "M6 3h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z M4 7.5h16 M7 5.3h.01 M10 5.3h.01 M12 10a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  dishwasher: "M6 3h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z M4 7.5h16 M7 5.3h3 M7.5 12h9 M7.5 16h9 M9.5 12v4 M14.5 12v4",
  vacuum: "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z M6.6 8.2a7 7 0 0 1 10.8 0 M12 12.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
  heat_pump: "M3.5 6h17v12h-17z M10 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z M10 12h.01 M16 9.5h2 M16 12h2 M16 14.5h2 M6 18v2 M18 18v2",
  radiator: "M5 7a1.5 1.5 0 0 1 3 0v10a1.5 1.5 0 0 1-3 0z M10.5 7a1.5 1.5 0 0 1 3 0v10a1.5 1.5 0 0 1-3 0z M16 7a1.5 1.5 0 0 1 3 0v10a1.5 1.5 0 0 1-3 0z M8 9.5h2.5 M13.5 9.5H16 M8 14.5h2.5 M13.5 14.5H16 M3 9.5h2 M19 9.5h2",
  thermostat: "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z M7.8 15.5a5 5 0 1 1 8.4 0 M12 12.5l2.5-2.5",
  thermometer: "M10 5a2 2 0 0 1 4 0v8.8a4 4 0 1 1-4 0z M12 9.5v6",
  boiler: "M6.5 6.5C6.5 4.6 9 3 12 3s5.5 1.6 5.5 3.5v11c0 1.9-2.5 3.5-5.5 3.5s-5.5-1.6-5.5-3.5z M12 9.5c1.6 1.6 2.2 2.7 2.2 3.8a2.2 2.2 0 0 1-4.4 0c0-1.1.6-2.2 2.2-3.8z",
  water: "M12 3.5c3 4 5.5 7 5.5 10a5.5 5.5 0 0 1-11 0c0-3 2.5-6 5.5-10z",
  fan: "M12 10.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z M11.2 10.7C9.6 7.6 10.6 3.5 13.6 3.5c2.6 0 2.8 3.2.3 4.8L12.9 10.8 M13.6 12.6c3.4.4 6.2 3.5 4.7 6-1.3 2.3-4.1.8-4.3-2.2L13.2 13.6 M10.5 12.4c-2 2.8-6 4-7.3 1.4-1.1-2.3 1.7-3.9 4.3-2.6l2.6 1",
  air: "M3 8.5h10.5a2.5 2.5 0 1 0-2.5-2.5 M3 12.5h15a2.5 2.5 0 1 1-2.5 2.5 M3 16.5h7",
  alarm: "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z M9.2 12l2 2 3.8-4",
  siren: "M7 17v-5a5 5 0 0 1 10 0v5 M5 17h14v3.5H5z M12 3v1.5 M4.6 5.6l1 1 M19.4 5.6l-1 1 M10 12a2 2 0 0 1 2-2",
  smoke: "M3 4h18 M5.5 4v1a6.5 3 0 0 0 13 0V4 M7.5 20.5a2.6 2.6 0 0 1-.3-5.2 3.6 3.6 0 0 1 6.9-1.3 3 3 0 1 1 2.4 6.5z",
  water_leak: "M12 3c2.4 3.2 4.4 5.6 4.4 8a4.4 4.4 0 0 1-8.8 0c0-2.4 2-4.8 4.4-8z M3 19c1.5 1.2 3 1.2 4.5 0s3-1.2 4.5 0 3 1.2 4.5 0 3-1.2 4.5 0",
  doorbell: "M8 2.5h8a2 2 0 0 1 2 2v15a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2z M12 6.5h.01 M12 12a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  camera: "M4.5 7.5h9a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 3 15V9a1.5 1.5 0 0 1 1.5-1.5z M15 11l6-3.5v9L15 13",
  lock: "M6 10.5h12a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-8.5a1 1 0 0 1 1-1z M8 10.5v-3a4 4 0 0 1 8 0v3 M12 14.5v2.5",
  unlock: "M6 10.5h12a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-8.5a1 1 0 0 1 1-1z M8 10.5v-3a4 4 0 0 1 7.7-1.5 M12 14.5v2.5",
  person: "M12 4a4 4 0 1 0 0 8a4 4 0 1 0 0-8z M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6",
  pet: "M12 12c-2.6 0-5 3.1-5 5.2 0 1.6 1.2 2.5 2.5 2.5 1 0 1.6-.6 2.5-.6s1.5.6 2.5.6c1.3 0 2.5-.9 2.5-2.5 0-2.1-2.4-5.2-5-5.2z M5.5 8.5a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2z M9.2 4.5a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2z M14.8 4.5a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2z M18.5 8.5a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2z",
  motion: "M14.5 3a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2z M13.5 8.5l-2.2 6 M8.5 11l2.6-2.6h3.2l1.8 2.8 2.4.8 M11.3 14.5 8.2 21 M11.3 14.5l3.4 2.4-.5 4.1 M3 10.5h3 M2.5 14h3.5",
  presence: "M12 3.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z M7.5 20c.8-3.6 2.4-6 4.5-6s3.7 2.4 4.5 6 M5 7a8.5 8.5 0 0 0 0 9.5 M19 7a8.5 8.5 0 0 1 0 9.5",
  phone: "M8 2.5h8a1.5 1.5 0 0 1 1.5 1.5v16a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 20V4A1.5 1.5 0 0 1 8 2.5z M11 18.5h2",
  door: "M6 21V4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21 M4 21h16 M14.5 12h.01",
  door_open: "M4 21h16 M6 21V4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21 M6 3.6l6 2.4V21 M10 12.5h.01",
  window: "M5 3.5h14a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z M12 3.5v17 M4 12h16",
  curtains: "M3 4h18 M5 4v16.5h4.5C7.6 15 7.6 9 9.5 4 M19 4v16.5h-4.5C16.4 15 16.4 9 14.5 4",
  curtains_closed: "M3 4h18 M5 4h14v16.5H5z M9.7 4v16.5 M14.3 4v16.5",
  shutter: "M4.5 3.5h15a.5.5 0 0 1 .5.5v16.5H4V4a.5.5 0 0 1 .5-.5z M4 7h16 M4 10.5h16 M4 14h16",
  garage_door: "M3 10.5 12 4l9 6.5v10 M3 10.5v10 M6.5 20.5v-8h11v8 M6.5 15.2h11 M6.5 17.9h11",
  tv: "M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z M8.5 21h7 M12 17v4",
  speaker: "M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z M12 11a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z M12 6.5h.01",
  radio: "M4 9h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z M7 9l10-5 M8.5 12a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z M14.5 12.5h3 M14.5 16.5h3",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 2.5v2 M12 19.5v2 M2.5 12h2 M19.5 12h2 M5.3 5.3l1.4 1.4 M17.3 17.3l1.4 1.4 M5.3 18.7l1.4-1.4 M17.3 6.7l1.4-1.4",
  moon: "M19.5 14.5A8 8 0 1 1 9.5 4.5a6.5 6.5 0 0 0 10 10z",
  sunrise: "M3 19h18 M7 19a5 5 0 0 1 10 0 M12 9.5V4 M9.5 6.5 12 4l2.5 2.5 M4.9 12.9l1.4 1.4 M19.1 12.9l-1.4 1.4",
  away: "M10 4H6.5A1.5 1.5 0 0 0 5 5.5v13A1.5 1.5 0 0 0 6.5 20H10 M14.5 8l4 4-4 4 M18.5 12H9",
  alarm_clock: "M12 6a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M12 9.5V13l2.5 1.5 M4.5 5.5 7 3.5 M19.5 5.5 17 3.5",
  home: "M3 11 12 4l9 7v9H3z",
  bed: "M3 5v15 M21 20v-4 M3 16h18 M3 12.5h5.5V16 M8.5 10H19a2 2 0 0 1 2 2v4",
  child: "M12 7.5a6 6 0 1 0 0 12 6 6 0 0 0 0-12z M7.7 9.3A2 2 0 1 1 9.5 6 M16.3 9.3A2 2 0 1 0 14.5 6 M10 12.5h.01 M14 12.5h.01 M10.8 15.5h2.4",
  bath: "M3 12h18v2.5a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5z M6 12V5.5a2 2 0 0 1 3.7-1.1 M7 19.5 6 21 M17 19.5l1 1.5",
  toilet: "M5 3.5h5v7.5H5z M5 11h14c0 3.3-2.4 5.5-5.5 5.5H11 M8.5 16.3 8 21h6l-.4-4.5",
  kitchen: "M3 11h13v3a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4z M16 12.5h4.5 M7 8c0-1 1-1.5 1-2.5 M11 8c0-1 1-1.5 1-2.5",
  dining: "M7 3v18 M4.5 3v5a2.5 2.5 0 0 0 5 0V3 M17 21V3c-2.2 1.2-3.5 4.5-3.5 9H17",
  sofa: "M5 11V8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3 M3.5 11A1.5 1.5 0 0 1 5 12.5V14h14v-1.5a1.5 1.5 0 0 1 3 0V18H2v-5.5A1.5 1.5 0 0 1 3.5 11z M4.5 18v2 M19.5 18v2",
  hall: "M12 8.2a1.8 1.8 0 1 1 1.8-1.8c0 1.1-1.8 1.3-1.8 2.6 M12 9 3 16h18z",
  office: "M5 5.5h14a1 1 0 0 1 1 1V16H4V6.5a1 1 0 0 1 1-1z M2 19h20",
  utility: "M3.5 7.8 12 3.8l8.5 4v9L12 20.8l-8.5-4z M3.5 7.8 12 11.8l8.5-4 M12 11.8v9",
  outside: "M8 15.5h8a4 4 0 0 0 1.2-7.8 5.2 5.2 0 0 0-10.4 0A4 4 0 0 0 8 15.5z M12 15.5V21 M9 21h6",
  room: "M4.5 4h15a.5.5 0 0 1 .5.5v15a.5.5 0 0 1-.5.5h-15a.5.5 0 0 1-.5-.5v-15a.5.5 0 0 1 .5-.5z M4 12h5 M13 12h7 M12 4v4.5",
  sensor: "M9 9h6v6H9z M6.2 6.2a8.2 8.2 0 0 0 0 11.6 M17.8 6.2a8.2 8.2 0 0 1 0 11.6",
  device: "M7 4h10a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3z M12 12h.01",
};
DEVICE_PATHS.garage = DEVICE_PATHS.car;

/** The glass of a lamp, for its fill. */
export const LAMP_GLASS = "M8.6 15a6.4 6.4 0 1 1 6.8 0c-.7.5-1.1 1.2-1.1 2H9.7c0-.8-.4-1.5-1.1-2z";

/** How far the glass of a lamp is filled (its top edge in the 24 grid) at a brightness. */
export function lampFillTop(pct: number): number {
  return 15.4 - (12.6 * Math.max(0, Math.min(100, pct))) / 100;
}

/** The streaks of a battery for its level: 0 to 4 of them. */
export function batteryBars(level: number): number[] {
  const n = Math.round((Math.max(0, Math.min(100, level)) / 100) * 4);
  return [6.4, 9.4, 12.6, 15.6].slice(0, n);
}

/** A shutter by how far it is closed: the frame, the box and 0 to 3 slats. */
export function shutterPath(closed: number): string {
  const n = Math.round((Math.max(0, Math.min(100, closed)) / 100) * 3);
  let d = "M4.5 3.5h15a.5.5 0 0 1 .5.5v16.5H4V4a.5.5 0 0 1 .5-.5z M4 7h16";
  for (let i = 0; i < n; i++) d += ` M4 ${10.5 + i * 3.5}h16`;
  return d;
}

/** The number in a lamp: only from 28 px, smaller the number goes beside it. */
export const NUMBER_FROM = 28;

/** The colour of an icon (E-508): a lamp that is on is warm, something else that is on green,
 * an alarm red, the rest muted. */
export function tone(icon: string, on: boolean, alarm = false): "lamp" | "on" | "alarm" | "" {
  if (alarm) return "alarm";
  if (!on) return "";
  return icon === "lamp" ? "lamp" : "on";
}

/** The icon of a kind of device (its HA domain) for overviews per kind. */
export const DOMAIN_ICONS: Record<string, string> = {
  light: "lamp", switch: "switch", climate: "thermostat", water_heater: "boiler", cover: "curtains",
  fan: "fan", media_player: "speaker", vacuum: "vacuum", camera: "camera", lock: "lock",
  alarm_control_panel: "alarm", siren: "siren", person: "person", device_tracker: "phone",
  button: "button", humidifier: "water", valve: "water", event: "button", binary_sensor: "sensor",
  sensor: "sensor", weather: "sun", sun: "sun",
};

/** The icon of a part on "Klaar voor autonomie?" and of its rules and automations (E-511). */
export const PART_ICONS: Record<string, string> = {
  lighting: "lamp", covers: "curtains", climate: "thermostat", air: "air", media: "tv",
  cleaning: "vacuum", garden: "outside", water: "water", security: "alarm", messages: "phone",
  energy: "energy", other: "device",
};

/** The icons of the rooms by their id, from the rooms the snapshot has (E-511). */
export function roomIcons(rooms: { id: string; icon?: string }[] | undefined): Record<string, string> {
  return Object.fromEntries((rooms ?? []).map((r) => [r.id, r.icon ?? "room"]));
}

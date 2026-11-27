import type { HistoryView } from "./history";
// What the server sends (src/housemind/server/views.py). Level decides which parts exist.

import type { Texts } from "./i18n";

/** An icon of the set and what it shows of the state now (E-508 – E-510): a lamp's brightness,
 * a battery's level, how far a shutter is closed. */
export interface IconLook {
  icon?: string;
  pct?: number;
  level?: number;
  closed?: number;
}

export interface Device extends IconLook {
  id: string;
  name: string;
  domain: string;
  state: string;
  on: boolean;
  /** A device's own light or one of its settings (E-426): no lamp, nothing HouseMind switches. */
  kind?: "" | "status_led" | "setting";
  /** What it is, in a word (E-427): "lamp", "instelling", "meet de lucht" …; and only AI said so. */
  function?: string;
  fromAi?: boolean;
  device?: string;
  /** A group (E-312): its members, and whether it lives in the radio network (E-515). */
  members?: string[];
  radio?: boolean;
}

/** A room's entities per device (E-426), its settings last. */
export interface DeviceGroup {
  id: string;
  name: string;
  model: string;
  items: Device[];
}
/** A device group as the server sends it: ids into the room's devices (code QA 3, punt 16). */
export interface DeviceGroupRef {
  id: string;
  name: string;
  model: string;
  items: string[];
}

export interface Room {
  id: string;
  name: string;
  floor: string | null;
  role: string;
  icon?: string; // the icon of its role, or the one chosen in HA (E-510)
  active?: (IconLook & { name: string })[]; // what is on: lamps first (E-510)
  occupied: boolean;
  dark: boolean;
  lightsOn: number;
  temperature: number | null;
  summary: string;
  devices: Device[];
  groups?: DeviceGroupRef[];
  scenes?: { id: string; name: string; source: "start" | "person" }[];
  zones?: RoomZone[];
}

/** One zone of a presence sensor (E-215): someone there or not, and what follows it. */
export interface RoomZone {
  id: string;
  name: string;
  on: boolean;
  state: string;
  note: string;
}

export interface Floor {
  id: string;
  name: string;
  rooms: string[];
}

/** Why Vesta is red, orange or amber, for the line under her word (E-389). */
export interface Why {
  text: string;
  notification: number | null;
  proposal: number | null;
}

export interface Notice extends IconLook {
  id: number;
  kind: "emergency" | "important" | "info" | "learning";
  text: string;
  at: string;
  actions: string[];
  proposal: number | null;
  labels?: Record<string, string>; // a button's own words, such as an alarm's name (E-311)
  // Per person (E-372): what "Niet meer tonen" offers, whether this person put it off or
  // switched it off, until when, and what of it can be shown again.
  mute?: { key: string; text: string }[];
  hidden?: "muted" | "later";
  laterUntil?: string;
  unmute?: { key: string; text: string }[];
}

export interface ProposalView extends IconLook {
  id: number;
  question: string;
  reason: string;
  domain: string;
  area: string;
  canRule: boolean;
  areaId: string | null; // its path in the network while it waits (E-439)
  entity: string;
  at: string;
  lamps?: ProposalLamp[]; // a stand for a room: what each lamp gets (E-499)
}

export interface ProposalLamp {
  name: string;
  pct?: number;
  kelvin?: number;
  colour?: string; // a colour name of the stand
  text?: string; // "alleen helderheid", "blijft zoals hij is", "gaat uit"
  off?: boolean;
  stays?: boolean;
}


export interface ReadyCounter {
  key: string;
  label: string;
  value: number;
  target: number;
  text: string; // "96 situaties gezien · 20 nodig"
  met: boolean; // enough of this (never for the corrections: those count against)
  history?: number; // how much of it came from the history (E-257)
}

/** "Wat ik nu zou doen" (E-295, 20.14): per room, what HouseMind would do now. */
/** Straks, in Brein › Ritme: the next moments of the day and what HouseMind will then do. */
export interface LaterLine {
  text: string;
  chip: string; // Doe ik, Vraag ik eerst, Zou ik, or Niet: and why
  outcome: string; // executed, proposed, observed or denied
  icon?: string;
  pct?: number;
}

export interface LaterMoment {
  key: string; // dark, light, bed or wake
  at: string;
  title: string;
  how: string;
  lines: LaterLine[];
  nothing: string | null;
}

export interface Later {
  at: string;
  moments: LaterMoment[];
  unknown: string[];
  blocked: string | null;
}

/** "Zelf een stap zetten" (20.10): the three steps, with what may not and why. */
export interface OwnStep {
  key: string;
  step: string | null; // the step an administrator set here, if any
  choices: { step: string; name: string; allowed: boolean; why: string | null }[];
}

/** One skill in one room on "Klaar voor autonomie?" (E-256). */
export interface ReadySkill {
  skill: string;
  area: string;
  what: string;
  skillName: string; // "licht bij binnenkomen", for the list with filters
  room: string; // "Keuken"
  step: string;
  stepName: string;
  heldBack: boolean;
  ready: boolean;
  progress: number;
  bar: { value: number; target: number; against?: boolean; history?: number };
  good?: number | null; // how often it went well (E-460); null: nothing judged yet
  watching?: boolean; // an automation or another system steers it: HouseMind only watches
  proven?: boolean; // right often enough: one question, in Wacht op jou
  unseen?: boolean; // a rule of a new device that has not come up yet (E-485): its own flag (E-501)
  byDoor?: string; // a small room with one door: its door says who is in (E-505)
  counters: ReadyCounter[];
  text: string;
  when?: string; // when HouseMind does it, in the words of its step, from Betrokken (E-385, E-386)
  whenDetail?: string; // on Expert: sensors, thresholds, wait, source and how sure (E-387)
  numbers?: { score: number; confidence: number; hits: number; misses: number; corrections: number; since: string | null };
  own?: OwnStep; // an administrator on Expert
  forget?: boolean; // "Vergeet dit" for an administrator (E-94)
  steer?: { ids: string[]; text: string }; // the HA automation that steers it (E-317)
  question?: SteerQuestion; // for an administrator: switch it off, or on again (E-317)
}

/** The question in a row (E-317): "off" on Autonomous while an automation still steers it,
 * "on" after a step back while HouseMind switched one off. After a no only the button stays. */
export interface SteerQuestion {
  kind: "off" | "on";
  id: string;
  text: string;
  declined: boolean;
  button: string;
}

export interface ReadyAutomation {
  id: string;
  name: string;
  state: string;
  ready: number;
  total: number;
  rooms?: string[]; // the rooms it teaches, for the room filter
  text: string;
  history?: string | null; // what the history says about it (E-257)
  // The one list of automations (E-523): what it teaches, what it does (AI), and for an
  // administrator Uitzetten or Weer aanzetten (E-42).
  teaches?: string[];
  does?: DoesView | null;
  canTurnOff?: boolean;
  canTurnOn?: boolean;
  causes?: string[]; // what starts it, for the filter (E-258)
  labels?: string[]; // in Home Assistant (E-345)
  categories?: string[];
}

/** A part (lighting, covers, climate …) on "Klaar voor autonomie?": skills from level 3. */
export interface ReadyPart {
  domain: string;
  name: string;
  off?: boolean; // the part is off: asleep, what it learned kept (E-400)
  offSince?: string | null;
  counts: Record<string, number>;
  cap: string | null;
  capName: string | null;
  text: string;
  limit: boolean;
  thumbs?: { up: number; down: number; text: string }; // this week, from level 3 (E-298)
  corrections?: { weeks: number[]; text: string }; // per week, oldest first, from level 3 (S-01)
  own?: OwnStep; // an administrator from level 3 (20.10)
  skills?: ReadySkill[];
  automations?: ReadyAutomation[];
}

export interface PartView {
  part: string;
  state: string; // off, observe, propose, autonomous
  experimental: boolean;
  offSince?: string | null;
}

export interface RuleView extends IconLook {
  id: number;
  text: string;
}


/** The house in motion (E-439): the newest thing done or asked, from its room to the device. */
export interface LivePath {
  area: string;
  entity: string;
  outcome: string;
  at: string;
  text: string;
  id: number | null; // the decision, for 'Waarom?' beside the line
  flowFor: number; // ms the line still flows after it happened; the line under it stays
}




export interface Prefs {
  brainTab?: string; // the tab open in Brein last (E-398, B-316)
  amount: "less" | "normal" | "more";
  kinds: Record<"important" | "info" | "learning", boolean>;
  maxPerDay: number;
  questionsPerDay: number; // the question budget on level 4 (E-294)
  language: string;
  textSize: "normal" | "large";
  timing?: "normal" | "longer" | "long"; // time to talk and to read (E-299)
  theme: "auto" | "dark" | "light";
  emergencyPush: boolean | null; // messages in danger: yes, no, or not chosen (E-20)
  bundleAt: string; // the day's bundle, "HH:MM" (B-30)
  quietFrom: string; // own quiet hours on level 4, "" = learned (E-290)
  quietUntil: string;
  briefing: Record<"weather" | "agenda" | "energy", boolean>;
  bedtimeCheck: boolean;
  reportDay: number;
  spokenPerDay: number;
  address: "je" | "u";
  tour?: number[]; // the levels this person saw the tour for (E-249)
}

export interface EnergyPoint {
  at: string;
  price: number | null;
  solar: number;
  house: number;
  battery: number; // measured, before now only: + charging, − discharging (E-363, B-253)
  car: number; // measured, before now only
  boiler: number;
  grid: number; // + from the grid, − to the grid
  past: boolean; // before now: what was measured today
  sell?: number | null; // Expert: the price for giving back (B-225)
  solarLow?: number; // Expert: the margin of the sun, when HouseMind measured enough (B-225)
  solarHigh?: number;
}

// The day clock (E-346, B-223): the coming 24 hours per quarter, with what HouseMind does.
export interface EnergyClock {
  start: number; // minutes after midnight of the first quarter
  now: string;
  soc: number | null; // how full the battery is now, measured
  doing: string | null;
  summary: string; // the clock in words, for who cannot see the colours
  points: { at: string; price: number | null; solar: number; doing: string | null }[];
}

// A stretch in the timeline under the plan (B-224).
export interface EnergyBlock {
  row: string; // boiler, heat_pump, export, appliance
  action: string;
  from: number; // index of the first point
  to: number; // index after the last point
  label: string;
  start: string;
  end: string;
  why: string;
  price: string | null;
  expected?: string | null; // what it should bring (E-379)
}

export interface EnergyView {
  sentence: string;
  more?: string; // Lees meer: the manual's part that explains the sentence (E-388)
  has: Record<string, boolean | number>;
  // Now and today (E-364, B-252): the battery and the car are only shown (E-363).
  live?: {
    solar: number | null;
    grid: number | null;
    house: number | null;
    batteries: { name: string; pct: number | null; watts: number | null }[];
    car: { plugged: boolean | null; watts: number | null; pct: number | null } | null;
  };
  today?: Partial<Record<"import_kwh" | "export_kwh" | "solar_kwh" | "battery_in_kwh" | "battery_out_kwh" | "car_kwh", number>>;
  chart?: EnergyPoint[];
  clock?: EnergyClock | null;
  blocks?: EnergyBlock[];
  managed?: string[];
  currency?: string;
  steps?: { device: string; what: string; start: string; end: string; why: string; price?: string | null; expected?: string | null; calc?: string }[];
  // What each part brought in the last four weeks; a part that did not pay rests (E-378, E-379).
  parts?: { name: string; text: string; resting: boolean; amount: number; icon?: string }[];
  // The prices in numbers, and what is in them – only what the integration states (E-304).
  priceRange?: string;
  priceBasis?: string;
  tips?: string[]; // without steering, never a notification (B-254)
  measuringDay?: { day: number; of: number } | null; // the first weeks (E-408)
  costs?: { today: string; month: string; exportKwh: number }; // B-255
  savings?: {
    measuring: boolean;
    daysLeft: number;
    total: number;
    totalText: string;
    compared: number;
    days: { day: string; label: string; cost: number; expected: number; saving: number }[];
  };
  baseLoad?: string;
  usersToday?: ({ name: string; kwh: number } & IconLook)[];
  table?: (EnergyPoint & { steps: string })[];
  users?: ({ name: string; watts: number } & IconLook)[];
}

export interface EnergySettingsView {
  goal: string;
  peakLimitKw: number | null;
  priceMode: string;
  has: Record<string, boolean | number>;
  country: string;
}

/** One thing the brain thought (E-218): saw → thought → did; more per level. */
export interface MindItem extends IconLook {
  at: string;
  area: string;
  outcome: string;
  outcomeName: string;
  text: string;
  saw?: string[];
  thought?: string;
  did?: string;
  details?: string[];
  checks?: { name: string; state: "ok" | "stop" | "none" }[]; // the gate's checks, Expert (QA 3)
  cause?: string; // from level 2: what it saw and what it did, for the flow (E-436)
  effect?: string;
}

export interface Snapshot {
  orb: string;
  secured?: boolean; // the alarm is on (E-539)
  vesta?: { name: string; formal: boolean };
  themes?: SchemeView[]; // the colour schemes that passed the check (E-266, E-267)
  manual?: { available: boolean; links: Record<string, string> }; // the manual in the app (E-238)
  line: string;
  why?: Why | null; // why Vesta has her colour, in that line instead (E-389)
  mind?: MindItem[];
  level: number;
  language: string;
  texts: Texts;
  fallback: Texts;
  levels: { level: number; key: string }[];
  user: { name: string | null; isAdmin: boolean };
  paused: { active: boolean; open: boolean; until: string | null; domains: Record<string, string | null>; hour?: string | null; morning?: string | null };
  tests?: { id: number; room: string; part: string; until: string }[]; // a testkwartier that runs (E-477)
  features: { energy: boolean; ai: string };
  version: string;
  news?: News | null; // what is new, by level, once (E-150)
  languageNames: Record<string, string>;
  haOldest?: string; // the lowest Home Assistant version (Over HouseMind, E-413)
  prefs: Prefs;
  onboarding: { person: boolean; house: boolean; again?: boolean }; // again: with the answers of now (E-272)
  houseMode?: string; // home, away, sleep, vacation or guests (E-286)
  safeMode?: string | null; // after a failed update: failed or newer (E-301)
  speech?: { cloud: string[]; local: boolean } | null; // where Vesta's voice goes (20.19)
  // Not set up yet (E-277): "pending", the step the administrator is at, or "done".
  setup?: { state: string; language: string; readWait?: number; labels?: number }; // readWait: seconds Klaar still waits (E-396); labels used as a hint (E-345)
  activeAreas: number;
  totalAreas: number;
  day?: string;
  dayNote?: { kind: string; source: string; text: string } | null;
  areas: { id: string; name: string }[];
  tablet: TabletView | null;
  rooms?: Room[];
  notifications?: Notice[];
  proposals?: ProposalView[];
  floors?: Floor[];
  rules?: RuleView[];
  readiness?: ReadyPart[];
  readinessOther?: ReadyAutomation[]; // automations no part lists, in the same list (E-523)
  knowledgeWaiting?: number; // what waits in Wat ik weet, an administrator from Involved (E-524)
  youGet?: string[]; // what this person gets (E-407)
  keepTerms?: string[]; // how long HouseMind keeps what (E-409)
  history?: HistoryView; // reading the history (E-257)
  rhythm?: Rhythm; // the rhythm of the house, from Involved (E-366 – E-368)
  rhythmLine?: string; // Informed: the rhythm in one sentence on Huis (E-368)
  dayRing?: number[]; // Ontzorgd: a dot per thing done today (E-435)
  livePath?: LivePath | null; // from Betrokken: the newest decision as a path (E-439)
  dayRooms?: { room: string; marks: { at: number; outcome: string }[] }[]; // levels 2 and 3 (E-436)
  reaction?: { ms: number | null; maxMs: number | null }; // Expert: in Over HouseMind (QA 3)
  energy?: EnergyView | null;
  demo?: DemoLive; // only in a demo house: the bar with the house's own time (E-321)
}

// 'Speel het huis' (E-321, chapter 25): only in a demo house.
export interface DemoLive {
  now: string; // the demo house's time when the server sent it
  speed: number; // 0.05 (twenty times slower) … 1 … 20 (twenty times faster)
  running: boolean; // Play; false: Stop – the time stands still (B-188)
  busy: { what: string; kind: string; done: number } | null; // a jump or a story going on
  held: boolean; // 'Lang weg': the residents stay away
}

export interface PlayLine {
  at: string;
  day: string; // the house's own date (yyyy-mm-dd)
  text: string;
  type: "done" | "notice";
  kind: string | null;
}

export interface PlayView {
  house: string;
  speeds: number[];
  jumps: string[];
  stories: string[];
  rooms: { id: string; area: string; name: string; on: boolean; kind: "presence" | "motion" }[];
  residents: { id: string; name: string; home: boolean }[];
  switches: { id: string; name: string; room: string; kind: string; on: boolean }[];
  openings: { id: string; name: string; kind: string; open: boolean }[];
  weather: { condition: string; temperature: number | null; set: string | null; outdoorSet: number | null; outdoor: number[]; choices: string[] } | null;
  temperatures: { area: string; name: string; value: number | null }[];
  emergencies: { key: string; id: string; room: string; on: boolean }[];
  energy: { prices: boolean; price: string | null; solar: boolean; sky: string | null } | null;
  held: boolean;
  learning: boolean;
  live: DemoLive;
  steps: { observe: number; propose: number; autonomous: number };
  last: { kind: string; key: string; since: string } | null;
  happened: PlayLine[];
}

/** Test het huis (E-474 – E-481, design 70): what the panel shows for one room. */
/** How a line of Speel het na goes for real (E-518): on Autonoom, Voorstellen or Observeren, held
 * by a hand, steered by something else, not at all, or an emergency. */
export type TestKind = "auto" | "ask" | "watch" | "hand" | "other" | "not" | "emergency";

export interface TestView {
  rooms: { id: string; name: string; icon?: string }[];
  area: string | null;
  now: string; // what the room measures now, in one line
  // Speel het na: only what the room has (E-475); a part of the day brings its light (E-519).
  situations: { key: string; on: boolean; implied: boolean }[];
  // Per line how it goes for real, and whether Doe het echt does it – else why not (E-518).
  lines: ({ text: string; real: string; kind: TestKind; outcome: string; will: boolean; skip: string | null } & IconLook)[];
  // Doe het echt in this room (E-518): until when, and per line done, back, kept, left or not.
  plays: { id: number; until: string; running: boolean; lines: ({ text: string; state: "done" | "back" | "kept" | "left" | "skipped" | "note"; say: string } & IconLook)[] }[];
  // Loop zelf rond, the testkwartier (E-477): the parts this room can test for real, and the one running.
  quarter: { parts: { key: string; name: string; running: { id: number; until: string } | null }[]; minutes: number[]; standard: number };
  anyRunning: boolean;
  nothing: string | null;
  happened: { id: number; at: string; text: string; outcome: string; check: string | null; ms: number | null; test: boolean }[];
  blocked: string | null; // an emergency now: no testing (E-480)
}

export interface DeviceRow extends IconLook {
  id: string;
  name: string;
  area: string;
  device: string; // the device it belongs to, for "Per ruimte" (E-258)
  deviceId: string;
  typeKey: string;
  part: string;
  step: string; // autonomous, propose, observe, measure, left_alone
  domain: string;
  kind: string | null;
  safety: string | null;
  maxStep: string | null;
  controls: boolean;
  excluded: boolean;
  state: string;
  detects?: string | null; // a camera: which detections HouseMind follows (E-190)
  vesta?: string; // how Vesta says it (E-411)
  labels?: string[]; // its labels in Home Assistant, and its device's (E-345)
  labelHint?: string | null; // the label that gave its part
  // A group (E-312): what kind, its members, and whether it switches them in one go.
  group?: { source: string; sourceName: string; radio: boolean; members: string[]; steers: boolean; why?: string };
  partOf?: { id: string; name: string }; // the group that switches this device
  areaId?: string;
  levels?: LevelsView; // a lamp's standen (E-490 – E-496)
}

/** A lamp's standen in Brein › Apparaten (B-413). */
export interface LevelsView {
  can: "onoff" | "dim" | "white" | "colour";
  pct: number[];
  colour: { rhythm?: boolean; kelvin?: number; name?: string }[];
  dim: "keep" | "off";
  source: "own" | "group" | "type" | "start";
  sourceName: string;
  kelvin: [number, number];
  colours: { key: string; name: string }[];
  edit: boolean;
  rhythm: { by: "ha"; name: string | null } | { by: "own"; on: boolean } | null;
  today?: { events: LampEventView[]; since: string | null }; // vandaag met deze lamp (E-500)
  offWait?: OffWaitView | null; // uit na … zonder beweging (E-575); null: never by itself (outside)
}

/** How long a lamp waits once its room is empty (E-575). */
export interface OffWaitView {
  minutes: number | null; // its own wait; null: what the room learned counts
  room: number; // the room's learned or start wait, in minutes
  roomText: string; // "5 min", "1,5 min"
  learned: boolean;
  line: string; // the line in the lamp's row
  sensed: boolean; // a sensor can tell nobody is there (E-462)
  motionOnly: boolean; // only motion: someone sitting still is not seen
  max: number;
}

export interface LampEventView {
  time: string;
  what: string; // "Aan", "Zachter en warmer", "Uit"
  who: string; // "Het dagritme", "Jij, met de hand", "Rustige avond voor het slapen: Gedimd"
  on: boolean;
  pct: number | null;
  kelvin: number | null;
}

export interface AutomationView {
  id: string;
  name: string;
  state: "taken_over" | "still_off" | "off" | "unreadable" | "hand" | "signal" | "along" | "stays" | "kept" | "ready" | "asked" | "learning";
  text: string;
  teaches: string[];
  canTurnOff: boolean;
  canTurnOn: boolean;
  history?: string | null; // what the history says about it (E-257)
  rooms: string[]; // what the buttons above the list filter on (E-258)
  parts: string[];
  causes: string[];
  scene: boolean;
  labels?: string[]; // in Home Assistant (E-345)
  categories?: string[];
  labelHint?: string | null; // the category or label that gave its part
  does?: DoesView | null; // what it does in plain words, from AI (B-346)
}

/** What an automation or script does, in plain words from AI (B-346): only to read. */
export interface DoesView {
  text: string;
  part: string;
  takeOver: "yes" | "partly" | "no";
}

/** A scene or script in Home Assistant, only to look at (E-03, E-258). */
export interface SceneScriptView {
  id: string;
  name: string;
  kind: "scene" | "script";
  rooms: string[];
  parts: string[];
  text: string;
  labels?: string[]; // in Home Assistant (E-345)
  categories?: string[];
  labelHint?: string | null;
  does?: DoesView | null; // a script, in plain words from AI (B-346)
}

/** One thing HouseMind knows of the house (E-205): a room's or a switch's role, what a sensor
 * measures (from behaviour, E-203), where the rules doubt (B-359), what an entity is (E-427),
 * with where it stands (E-524) – so that every answer can be taken back (E-525). */
export interface KnowledgeRow {
  id: string; // "area:hobby:role"
  kind: "area" | "entity";
  field: "role" | "measures" | "temperature" | "opening" | "status_led" | "function" | "one_door";
  label: string; // what it is about: "Ruimte", "Schakelaar", "Deur of raam" …
  name: string;
  what: string; // what HouseMind thinks: "Werkkamer", "een lamp", "meet licht"
  role: string;
  source: "ai" | "behaviour" | "rules" | "person";
  fromAI: boolean; // still only AI's: watched and learned, not proposed or done (50.8)
  note: string; // what Klopt means for it
  state: "open" | "yes" | "no"; // Te bevestigen, Bevestigd, Klopt niet (E-524)
  since: string; // when it got this state
  room: string; // its room's name, or "" (E-524)
  roomId: string;
  device?: string; // the device an entity belongs to, to fold per device (E-524)
  deviceId?: string;
  deviceModel?: string; // its type, beside the device's name (E-432)
  was?: string; // what HouseMind thought itself, under a person's own answer (E-525)
  why?: string;
  certainty?: number; // percent
  options?: { id: string; name: string }[]; // another sensor of the room (B-359)
  unknown?: boolean; // a room HouseMind does not know (E-486): only the list, no Klopt
}

/** Wat ik weet in Brein (E-524), for administrators on Betrokken and Expert: Herkend (a count),
 * every finding with its state, and the scenes and scripts to read. */
export interface HouseKnowledgeView {
  ai: boolean;
  busy: boolean;
  recognised: number;
  rows: KnowledgeRow[];
  functions: { id: string; name: string }[];
  described: number;
  roles: { id: string; name: string }[];
  scenes: SceneScriptView[];
}

export interface VestaVoice {
  engine: string;
  language: string | null;
  voice: string | null;
}

export interface VestaSettings {
  name: string;
  defaultName: string;
  language: string;
  houseLanguage: string; // the language of the house, to change in House settings (E-320)
  languages: Record<string, string>; // each language in its own words
  chosen: VestaVoice | null;
  recommended: VestaVoice | null;
  engines: { id: string; name: string; voices: { id: string; name: string }[] }[];
}

/** A colour scheme from a file (E-266, E-267): the colours by their design-system role. */
export interface SchemeView {
  id: string;
  name: string;
  hint: string;
  base: "dark" | "light";
  level: "AA" | "AAA";
  colours: Record<string, string>;
  glow: number;
  own: boolean;
}

/** Why a colour scheme file is left out (E-267). */
export interface SchemeProblem {
  file: string;
  code: "unreadable" | "id" | "base" | "level" | "missing" | "not_a_colour" | "contrast";
  role: string | null;
  on: string | null;
  ratio: number | null;
  need: number | null;
  value: string | null;
}

export interface HouseSettings {
  vesta?: VestaSettings;
  themes?: { ok: { id: string; name: string; own: boolean; file: string; level: string }[]; problems: SchemeProblem[]; example: string };
  knowledgeUpdates: boolean;
  knowledgeVersion: string | null;
  updatesPublished: boolean;
  care: { person: string; contact: string; consent: boolean; ownAccount?: boolean; agreed?: boolean } | null; // M-8
  persons: { id: string; name: string }[];
  contacts: { id: string; name: string }[];
  experimental: string[];
  currency: string;
  givenBack: boolean;
  consentLearning: boolean;
  historyLearning?: boolean;
  history?: HistoryView; // learning from the history (E-257)
  maxStep: Record<string, string>;
  parts?: PartView[]; // one list, Uit to Autonoom (E-399)
  petAreas: string[];
  newPersonLevel: number; // where a new resident begins (E-287)
  residentTopLevel?: number; // the highest level for a non-administrator (QA 3)
  phonelessResidents: boolean; // residents without a phone of their own (E-293)
  departed?: { id: string; name: string }[]; // no longer in HA: to forget (QA 2, M-9)
  privateNames?: string[]; // their first names, only to leave them out at an AI (B-174)
  namesInRooms?: string[]; // names found in room names, also left out
  livedIn?: boolean; // looking lived-in on vacation (E-285)
  vestaWindow?: boolean; // Vesta opens her window with a question by herself, for the whole house (E-450)
  // Which alarm is "the alarm" (B-67) and the sounds that only count after a choice (16.2).
  security?: {
    alarms: { id: string; name: string; inUse: boolean }[];
    alarm: string | null;
    auto: string | null; // the only alarm in use: taken by itself (E-311)
    cameras: boolean;
    sounds: string[];
    chosen: string[];
    hasAlarm: boolean;
    coverings: { id: string; name: string; area: string }[]; // may open at an alarm (B-170)
    open: string[]; // chosen by an administrator; nothing by default
    selfArm?: { away: boolean; night: boolean }; // on by itself, the standing yes (E-540)
    selfCan?: { away: boolean; night: boolean }; // possible: that mode, no code
    // The alarm reaction (E-543, E-546): lamps that flash red, what goes up at a fire.
    signalLamps?: { id: string; name: string; area: string; suggested: boolean }[];
    signal?: string[];
    fireCovers?: { id: string; name: string; area: string }[];
    fireOpen?: string[];
  };
  calendar?: boolean; // one yes to read the calendar (20.19)
  calendarAsked?: boolean; // answered, yes or no
  hasCalendar?: boolean; // the house has a calendar in HA
  areas: { id: string; name: string }[];
  automations: AutomationView[];
  scenesScripts?: SceneScriptView[];
  snapshots?: string[]; // days to go back to (20.13)
  ai: {
    enabled: boolean;
    provider: string;
    baseUrl: string;
    modelRoutine: string;
    modelStrong: string;
    monthlyBudget: number;
    hasKey: boolean;
    status: string;
    consent: boolean;
    local: boolean;
    remote?: boolean; // an own AI address outside the home network (QA 2, M-7)
    spent: number;
    budgetState: string;
    budget: number;
    taskModels?: Record<string, string>; // Expert: a model per task (E-49)
    spentByTask?: Record<string, number>;
    // The AI tasks of Home Assistant to choose from, and where each works (E-422).
    aiTasks?: { id: string; name: string; place: "local" | "cloud" | "unknown" }[];
    aiTaskLimit?: number; // questions a day to one in the cloud
    images?: boolean; // camera images described by the AI in the house (E-424)
  };
}

export interface Toast {
  id: number;
  text: string;
  undo: boolean;
}

export interface TabletView {
  area: string;
  name: string;
  awake: boolean;
  night: boolean;
  weather: string | null;
  summary: string;
  scenes: { id: string; name: string; source: "start" | "person" }[];
  // The doorbell (E-187): its camera straight from Home Assistant, for 30 seconds.
  doorbell?: { at: string; seconds: number; left: number; where: string; url: string | null } | null;
  // The alarm or a fire, while it goes on (E-544): red, what, where and when; a break-in with a
  // camera's image and the button to the alarm, a fire without anything that distracts.
  alarm?: { kind: string; title: string; where: string; at: string; advice: string; url?: string | null; alarm?: string | null } | null;
}

export interface IntegrationView {
  available: boolean;
  installed: string | null;
  foreign: boolean;
  current: string | null;
  loaded: boolean;
  setup: "configured" | "discovered" | null;
  forward: string;
  name: string;
  agents: string[];
  // Vesta's own Assist pipeline (E-280): hers, someone's with HouseMind's agent, none yet, or
  // no agent yet (the link comes first).
  pipeline?: { state: "ours" | "other" | "none" | "no_agent"; name?: string; language?: string; voice?: string };
  // Whether HA really reaches HouseMind now (20.23): ok, an old key, or nothing heard.
  contact?: "ok" | "refused" | "none";
  linking?: boolean; // "Koppelen" under way: restarting HA, finishing the link (20.23)
  finish?: "done" | "restart" | "failed" | null; // how finishing it in HA last went
  speech?: { cloud: string[]; local: boolean } | null; // where her voice goes (20.19); null while HA restarts
}


/** "Mijn gegevens" (E-300): what HouseMind keeps about one person. */
export interface MyData {
  known: boolean;
  level: number;
  preferences: Record<string, unknown>;
  consents: Record<string, unknown>;
  conversations: number;
  answers: { at: string; device: string; answer: string }[];
  rules: string[];
}

/** What is new in this version, as much as fits the level (E-150). */
export interface News {
  version: string;
  one?: string;
  list?: string[];
}

/** One thing in Home Assistant netjes (E-352, B-245). */
export interface TidyItem {
  key: string;
  kind:
    | "integration_down"
    | "device_down"
    | "unavailable"
    | "not_provided"
    | "duplicate"
    | "missing_ref"
    | "not_provided_group"
    | "unavailable_group"
    | "meter_still"
    | "meter_still_group"
    | "integration_silent"
    | "energy_no_grid"
    | "energy_issues"
    | "unused_on"
    | "unused_off";
  name: string;
  target: string;
  since: string;
  heavy: boolean;
  followsCount: number;
  follows: { entityId: string; name: string; room: string | null }[];
  depends: string[];
  dependsCount: number;
  used: number;
  open: string;
  entity?: { entityId: string; name: string; room: string | null; icon?: string };
  state?: string;
  missing?: string[];
  suggest?: { entityId: string; name: string; room: string | null };
  source?: { id: string; name: string | null }; // the integration (B-258)
  rooms: string[];
  meter?: { role: string; value: string; unit: string | null; expected_wh: number | null }; // B-265
  candidates?: { entityId: string; name: string; room: string | null }[]; // B-267
  issues?: { type: string; entityId: string; name: string; room: string | null }[]; // B-267
  can: ("off" | "off_all" | "not_duplicate" | "leave" | "mark" | "review" | "keep_on" | "keep")[]; // B-239, B-243, B-244
  markTarget: string | null; // what 'Bewust uit' goes on
  suggestMark?: { how: "hours" | "dark"; start?: string; end?: string }; // 'Meestal uit … – bewust?'
  review?: string; // the mark that asks 'Klopt dit nog?'
  days?: number; // B-240: not run for this many days
  never?: boolean; // never ran: counted from when HouseMind first saw it
  season?: boolean; // an automation for a season, which waited a year
}

type TidyEntity = { entityId: string; name: string; room: string | null; icon?: string };

/** The three tiles of Home Assistant netjes (B-241). */
export interface TidySystem {
  at: string;
  backup: { state: "ok" | "old" | "none" | "own" | "unknown"; at?: string; days?: number; heavy?: boolean; keep?: TidyKeep };
  database: {
    state: "ok" | "grows" | "unknown";
    bytes?: number;
    engine?: string | null;
    growth?: number | null;
    busiest?: (TidyEntity & { every: number })[];
  };
  disk: {
    state: "ok" | "filling" | "full" | "unknown";
    used?: number;
    total?: number;
    usedText?: string; // as Home Assistant says it ("40.7 GB")
    totalText?: string;
    share?: number;
    heavy?: boolean;
    free?: number;
    backup?: number; // the size of the newest automatic backup (E-397)
    room?: number; // how many more backups fit, rounded down
    looseBytes?: number; // what the old loose backups take, when the disk fills
    backups?: number; // what all backups on this device take, for the ring (E-451)
  };
  keepAll: boolean; // Home Assistant keeps every automatic backup (E-397)
  loose: { id: string; name: string; date: string; size: number }[]; // old loose backups, oldest first
}

/** What Home Assistant does with its automatic backups (E-397): how often and how many. */
export interface TidyKeep {
  recurrence: "daily" | "custom_days" | "never";
  days: string[]; // "mon" … "sun"
  copies: number | null;
  keepDays: number | null;
}

/** The last update and what changed since (B-242). */
export interface TidyUpdate {
  from: Record<string, string>;
  to: Record<string, string>;
  at: string;
  checks: string[];
  next: string | null;
  lost: { source: string; name: string; down: boolean; count: number; entities: TidyEntity[]; open: string }[];
  back: TidyEntity[];
  backCount: number;
  known: boolean;
}

/** The last round of Home Assistant netjes (E-351, B-245). */
export interface TidyView {
  checkedAt: string | null;
  startsAt: string | null;
  count: number;
  broken: TidyItem[];
  unused: TidyItem[]; // B-240
  system: TidySystem | null; // B-241
  update: TidyUpdate | null; // B-242
  off: { target: string; name: string; kind: "entity" | "automation"; reason: string; at: string }[]; // B-243
  decided: { key: string; choice: string; name: string; at: string; count?: number }[]; // B-244
  marks: { target: string; name: string; how: string; start: string | null; end: string | null; until: string | null; reviewAt: string; at: string }[]; // B-239
}

/** The rhythm of the house (E-366 – E-368, chapter 35). */
export interface RhythmMoment {
  key: string;
  label: string;
  value?: string;
  learning?: string;
  detail?: string; // Expert: how many days the time held
}

export interface RhythmDay {
  key: string;
  label: string;
  bar: { kind: "sleep" | "morning" | "away" | "home"; from: number; to: number }[];
  unsure?: { from: number; to: number }[]; // not sure yet, striped over the bar (E-507)
  learning?: boolean; // the bar comes from the nights so far, not from settled times
  moments: RhythmMoment[];
}

export interface RhythmOther {
  key: string;
  label: string;
  value?: string;
  from?: string;
  to?: string | null;
}

/** What HouseMind learned in one room (E-415): its own sentences, and the AI's story. */
export interface RhythmHabit {
  area: string;
  icon?: string; // the room's icon (E-511)
  room: string;
  lines: string[];
  text: string | null; // the AI's story, only when written for exactly these sentences
  ai: boolean;
}

export interface Rhythm {
  now?: string | null; // the part of the day now, from the house's rhythm (E-455)
  today?: string; // today's type of day and the minute now: the line "nu" on its bar (E-507)
  minute?: number;
  days: RhythmDay[];
  other: RhythmOther[];
  note: string | null;
  habits?: RhythmHabit[];
}

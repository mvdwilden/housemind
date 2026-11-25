# HouseMind – Device catalogue

*HouseMind 0.2.37, commit `dd9ed48`, 2026-11-07. 46 kinds of device. Generated from HouseMind's own knowledge files, so it says what this version really does.*

HouseMind recognises devices by Home Assistant's own terms – the domain, the device class, the colour modes and the supported features – and never by brand or model. For every kind of device, the catalogue says what HouseMind may do with it, how far it may go on its own, and what it learns. Every action, whatever its source, passes the same safety gate, and a person always wins: switch something yourself, and HouseMind leaves it alone for a while.

## How to read the catalogue

**Safety class** – how strict HouseMind is with a kind of device:

| Class | Meaning |
|---|---|
| Free | HouseMind may switch it within the allowed values. |
| Careful | Allowed, with limits on how often and how far it may change. |
| Safe direction only | Only towards safety: a lock only locks, a valve only closes, a window only closes. |
| Propose only | HouseMind only asks; a person decides. |
| Never | HouseMind only reads it; it never switches it. |

**Highest step** – how far HouseMind may go once it has learned enough: *Observe* (it only watches and learns), *Propose* (it asks first) or *Autonomous* (it acts and tells). Every device starts at Observe; HouseMind earns each step per skill and per room, and an administrator can always set a lower limit.

**Who decides** – *House*: HouseMind may take the initiative; *Person*: HouseMind only follows what people start; *Shared*: HouseMind helps with what people use.

**A person wins** – after someone switches a device by hand, HouseMind leaves it alone for that long, at most the hours shown.

## Hard limits

These are in the code itself. No file, update or setting can loosen them:

- Never turn an alarm off.
- Never open a lock.
- Never open a garage door, a gate or a door.
- Never make, change or delete your automations, scripts or scenes in Home Assistant. It only switches an automation off after your yes, when it takes over that job, and on again whenever you want.
- Never touch a device an administrator protected.
- Never a camera image: HouseMind does not look at, keep or send images.

## Lighting

Lamps, and switches or plugs that power a lamp.

| Device | Recognised by | Safety class | Highest step | Who decides | What HouseMind may do | A person wins | Skills |
|---|---|---|---|---|---|---|---|
| Light that only switches on and off | `light`; colour modes `onoff` | Free | Autonomous | House | turn on (transition 0–30 s); turn off (transition 0–30 s) | until the room is empty, at most 4 h | light when entering, light off when leaving, night light |
| Dimmable light | `light`; colour modes `brightness` | Free | Autonomous | House | turn on (brightness 1–100 %, transition 0–300 s); turn off (transition 0–30 s) | until the room is empty, at most 4 h | light when entering, light off when leaving, night light, dim warning |
| Light with colour temperature or colour | `light`; colour modes `color_temp`, `hs`, `rgb`, `rgbw`, `rgbww`, `xy` | Free | Autonomous | House | turn on (brightness 1–100 %, colour temperature 2000–6500 K, transition 0–300 s); turn off (transition 0–30 s) | until the room is empty, at most 4 h | light when entering, light off when leaving, night light, dim warning, daylight rhythm |
| Switch or plug that powers a lamp | `switch`; role `light` (HouseMind gives this role) | Free | Autonomous | House | turn on; turn off | until the room is empty, at most 4 h | light when entering, light off when leaving |

## Curtains, blinds, windows and doors

Everything that opens and closes.

| Device | Recognised by | Safety class | Highest step | Who decides | What HouseMind may do | A person wins | Skills |
|---|---|---|---|---|---|---|---|
| Curtain, blind, shade or shutter | `cover`; device class `curtain`, `blind`, `shade`, `shutter` | Careful | Autonomous | House | open cover; close cover; stop cover; set cover position (position 0–100 %); set cover tilt position (tilt 0–100 %) | until the next part of the day, at most 6 h | closing the curtains at dusk, opening the curtains in the morning, privacy street side, sun protection |
| Awning | `cover`; device class `awning` | Careful | Autonomous | House | open cover; close cover; stop cover; set cover position (position 0–100 %) | until the next part of the day, at most 4 h | sun protection, retract wind rain |
| Motorised window | `cover`; device class `window` | Safe direction only | Autonomous | House | close cover; open cover (propose only) | until the next part of the day, at most 4 h | close on rain |
| Ventilation damper | `cover`; device class `damper` | Free | Autonomous | House | open cover; close cover; set cover position (position 0–100 %) | until the situation changes, at most 4 h | – |
| Garage door, gate or door | `cover`; device class `garage`, `gate`, `door` | Propose only | Propose | Person | close cover (propose only) | until the situation changes, at most 4 h | report open when away |

## Climate

Heating, cooling, ventilation, humidity and hot water.

| Device | Recognised by | Safety class | Highest step | Who decides | What HouseMind may do | A person wins | Skills |
|---|---|---|---|---|---|---|---|
| Thermostat, heat pump or air conditioning | `climate` | Careful | Autonomous | House | set temperature (temperature 5–30 °C); set preset mode (preset away / comfort / eco / home / none / sleep); set hvac mode (mode auto / cool / heat / heat_cool / off) (propose only) | until house mode changes, at most 4 h | heating when someone is home, cooling when someone is home, reaching the temperature you set, saving when everyone is away for long, preheat arrival, lower heating with an open window, frost protection |
| Fan or ventilation unit | `fan` | Free | Autonomous | House | turn on; turn off; set percentage (speed 0–100 %) | until the situation changes, at most 2 h | ventilating when stuffy, ventilating when humid, kitchen hood |
| Humidifier or dehumidifier | `humidifier` | Careful | Autonomous | House | turn on; turn off; set humidity (humidity 30–70 %) | until the situation changes, at most 4 h | keep humidity |
| Water heater or boiler | `water_heater` | Careful | Autonomous | House | set temperature (temperature 45–70 °C) | until the situation changes, at most 4 h | hot water ready, boost on surplus |

## Energy

Feeding back and appliances; the home battery and the car only shown.

| Device | Recognised by | Safety class | Highest step | Who decides | What HouseMind may do | A person wins | Skills |
|---|---|---|---|---|---|---|---|
| Limit on feeding back to the grid | `number`; role `export_limit` (HouseMind gives this role) | Careful | Autonomous | House | set value (value 0–100000) | until the situation changes, at most 12 h | limit giving back at a negative price |
| Start button of an appliance (washing machine, dishwasher …) | `button`; role `appliance_start` (HouseMind gives this role) | Careful | Propose | Shared | press | until the situation changes, at most 12 h | appliances at the cheapest moment |
| Start switch of an appliance | `switch`; role `appliance_start` (HouseMind gives this role) | Careful | Propose | Shared | turn on | until the situation changes, at most 12 h | appliances at the cheapest moment |

## Safety

Locks, alarms, valves, sirens, cameras and the sensors that warn.

| Device | Recognised by | Safety class | Highest step | Who decides | What HouseMind may do | A person wins | Skills |
|---|---|---|---|---|---|---|---|
| Lock | `lock` | Safe direction only | Autonomous | Person | lock | until the situation changes, at most 4 h | lock when away |
| Alarm system | `alarm_control_panel` | Propose only | Propose | Person | alarm arm away (propose only); alarm arm home (propose only); alarm arm night (propose only) | until the situation changes, at most 4 h | propose arm when away |
| Main water valve | `valve`; device class `water` | Safe direction only | Autonomous | House | close valve | until the situation changes, at most 4 h | close on leak |
| Gas valve | `valve`; device class `gas` | Safe direction only | Autonomous | House | close valve | until the situation changes, at most 4 h | close on gas |
| Siren | `siren` | Never | Observe | Person | Nothing: read only | – | – |
| Camera | `camera` | Never | Observe | Person | Nothing: read only | – | – |
| Smoke detector | `binary_sensor`; device class `smoke` | Never | Observe | House | Nothing: read only | – | emergency smoke |
| Carbon monoxide detector | `binary_sensor`; device class `carbon_monoxide`, `co` | Never | Observe | House | Nothing: read only | – | emergency co |
| Gas detector | `binary_sensor`; device class `gas` | Never | Observe | House | Nothing: read only | – | emergency gas |
| Water leak sensor | `binary_sensor`; device class `moisture` | Never | Observe | House | Nothing: read only | – | emergency leak |
| Frost sensor | `binary_sensor`; device class `cold` | Never | Observe | House | Nothing: read only | – | emergency frost |
| Motion, occupancy or presence sensor | `binary_sensor`; device class `motion`, `occupancy`, `presence` | Never | Observe | House | Nothing: read only | – | – |
| Door, window or garage door contact | `binary_sensor`; device class `door`, `window`, `opening`, `garage_door` | Never | Observe | House | Nothing: read only | – | – |
| Power switch of a hob or oven (only off, after a person's yes) | `switch`; role `cooking_off` (HouseMind gives this role) | Safe direction only | Propose | Person | turn off | until the situation changes, at most 4 h | – |

## Other devices

The careful fallback for every kind of device in Home Assistant.

| Device | Recognised by | Safety class | Highest step | Who decides | What HouseMind may do | A person wins | Skills |
|---|---|---|---|---|---|---|---|
| Any other light | `light` | Free | Autonomous | House | turn on; turn off | until the room is empty, at most 4 h | – |
| Any other cover | `cover` | Propose only | Observe | Person | Nothing: read only | – | – |
| Any other switch | `switch` | Propose only | Observe | Person | Nothing: read only | – | – |
| Any other valve (garden tap, sprinkler …) | `valve` | Propose only | Observe | Person | Nothing: read only | – | – |
| Any other on/off sensor | `binary_sensor` | Never | Observe | House | Nothing: read only | – | – |
| Any measuring sensor | `sensor` | Never | Observe | House | Nothing: read only | – | – |
| Weather | `weather` | Never | Observe | House | Nothing: read only | – | – |
| Any other number setting | `number` | Propose only | Observe | Person | Nothing: read only | – | – |
| Any other choice setting | `select` | Propose only | Observe | Person | Nothing: read only | – | – |
| Any other button | `button` | Never | Observe | Person | Nothing: read only | – | – |
| Media player *(experimental: off until an administrator turns it on)* | `media_player` | Careful | Propose | Person | turn off (propose only) | until the situation changes, at most 4 h | – |
| Robot vacuum *(experimental: off until an administrator turns it on)* | `vacuum` | Careful | Autonomous | Shared | start; return to base | until the situation changes, at most 4 h | – |
| Robot lawn mower *(experimental: off until an administrator turns it on)* | `lawn_mower` | Propose only | Propose | Shared | start mowing (propose only); dock | until the situation changes, at most 4 h | – |
| Event (button presses, doorbell, detections) | `event` | Never | Observe | Person | Nothing: read only | – | – |

## Existing automations

The automations you already have in Home Assistant.

| Device | Recognised by | Safety class | Highest step | Who decides | What HouseMind may do | A person wins | Skills |
|---|---|---|---|---|---|---|---|
| An automation you made in Home Assistant | `automation` | Propose only | Propose | Person | turn off (propose only); turn on (propose only) | – | – |
| The switch of a group of lamps that follow the day | `switch` | Propose only | Propose | Person | turn off (propose only); turn on (propose only) | – | – |

## Not in the catalogue

A device that matches no specific kind gets the careful entry for its domain under *Other devices* – for most domains that means HouseMind only reads it. A domain HouseMind does not know at all is left alone. Knowledge updates can add kinds; they can never loosen a hard limit.

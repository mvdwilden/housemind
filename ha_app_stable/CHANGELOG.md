# Changelog

<!-- support:start – made by tools/build_support.py from docs/support.yaml -->
<!-- support:end -->

## 0.4.4 – 27-11-2026

HouseMind has your lamps to itself.

- When a light rule of a room is on Autonomous, HouseMind also does the day's rhythm of those lamps itself, with their levels. Adaptive Lighting no longer steers them.
- While you set a lamp's levels, Adaptive Lighting rests for that lamp. Only you steer it then; after Done the group steers again.
- In What I know you choose what a switch switches: a lamp, an appliance or another device. Switches that became a lamp through a label are there now too.
- The Levels window says at the top in short where the values and the colour come from, and one Save keeps everything: the levels and the waiting time.
- No beta any more: HouseMind is free to install and use. The code is open to read, but not to change or pass on. The changelog starts at 0.4.0.

## 0.4.3 – 27-11-2026

Updating keeps everything HouseMind knows.

- An update through Home Assistant keeps everything: what HouseMind learned, your settings, levels and waiting times. The setup and the introduction do not come back; you only see this message.
- The manual says how to update, and that you never remove HouseMind for it: removing throws everything away.
- A new test checks with every version that an update forgets nothing.

## 0.4.2 – 27-11-2026

Choose per lamp after how many minutes it goes off, and levels come first.

- For each lamp, a slider chooses after how many minutes it goes off once nobody is there: from at once to 30 minutes. Choose nothing and HouseMind learns it as always.
- A lamp with levels always gets one of its four levels, with the brightness and colour you saved. If Dimmed is purple, the night light is purple.
- The day's rhythm only picks a level for such a lamp too, so it changes in steps.
- Just before off, such a lamp dims for a moment in Dimmed's colour. Come back and it gets its light and colour back.

## 0.4.1 – 26-11-2026

HouseMind 0.4.1 is tested with Home Assistant 2026.11.

- On Home Assistant 2026.11, HouseMind is no longer extra careful: it again does what it may by itself, without asking first.
- The tests against a real Home Assistant now run on GitHub for every version: with 2026.4, with this month's version and with next month's beta.
- The manual names the tested versions: 2026.4, 2026.10 and 2026.11.

## 0.4.0 – 25-11-2026

HouseMind 0.4.0 is ready to become the first public beta.

- Soon anyone can install HouseMind from GitHub, with ready-made, signed packages for ordinary computers and for ARM, such as a Raspberry Pi.
- While it is a beta, the App shows as experimental in Home Assistant.
- A new front page and a short, honest summary of how HouseMind is tested.
- A new installation makes its database in one step; an installation from 0.2.0 on simply grows along.

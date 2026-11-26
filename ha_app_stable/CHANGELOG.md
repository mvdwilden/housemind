# Changelog

<!-- support:start – made by tools/build_support.py from docs/support.yaml -->
<!-- support:end -->

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

## 0.3.33 – 25-11-2026

One rule for words, numbers and names, in every language.

- An accent in a name no longer matters: a relay 'Garáge deur' is as protected as 'Garagedeur'.
- A short word only counts as a whole word – 'car' is never 'carbon' – and 'gate2' is simply a gate.
- HouseMind writes numbers and sizes with the decimal sign of your language, also in a language file of your own.
- The Energy screen reads today's figures and the saving once a quarter of an hour, not at every update.

## 0.3.32 – 25-11-2026

The morning card and the wall tablet follow your house's rhythm.

- The morning card stays for as long as it is morning in your house, also on a late Sunday.
- A wall tablet goes dark in your house's night, not only at eleven.
- A house without a rhythm yet keeps the clock: the card from 5 to 11, the tablet dark from 23 to 6.
- So every screen now follows the house, as the light and Vesta's greeting do.

## 0.3.31 – 25-11-2026

Settings are in one place, and in memory.

- Every house setting is in one table: its default, whether a screen may change it and whether it goes in the settings file.
- What House settings saves is really kept, for every setting; a test keeps it that way.
- Updating a screen no longer asks the database for the settings again and again: they are in memory.
- A setting that changes another way, such as a day put back, is read again.

## 0.3.30 – 25-11-2026

A round is quicker in a large house.

- HouseMind knows per room and per device at once what belongs to it, instead of every rule searching the whole house.
- Ventilating, an open window with the heating, the light in a room and a device's sensors use that one list.
- In a house of about 1,750 entities a round goes from about 15 to about 6 milliseconds, and a bigger house no longer gets slower squared.
- A device that is added or moves to another room counts at once.

## 0.3.29 – 25-11-2026

Updating the screens takes less work, above all in a large house.

- Ready for autonomy? is only worked out again when HouseMind decided, learned or got an answer, or when something was set; otherwise once a round.
- Screens open in the same language and at the same level share that work.
- A busy house gives a screen a new picture at most five times a second, and the newest always gets through.
- In a large house with three screens open, a sensor now takes about 40 instead of 150 milliseconds.

## 0.3.28 – 25-11-2026

Less on the screens: only what asks something of you.

- Devices without codes, and the Status column is off until you choose it. Only not available or not recognised is added, and the top says how many are not available.
- Open in Home Assistant takes you to the device; that is also where you choose another icon. Try a level for the whole room is now in the Levels window.
- The room window shows the moments. You start one with your voice or on the wall tablet.
- Check-up › System is one sentence when all is well; only what is wrong gets a tile.
- No Last at line under the network any more, the note under a row of questions only once, and Ready for autonomy? says how often it went well, without a score.

## 0.3.27 – 25-11-2026

The Logbook is calmer: one line per decision, fewer buttons.

- A question, your answer and what came of it are one line: You said yes at 09:14.
- No overview with percentages and no small labels per line any more, but one grey line: 18:29 · Living room · done. Grouping by kind is gone; Filter › Kind already does that.
- Why? is only on Informed now; from Involved the line says it itself. Today different? is a small button.
- Columns and Export only on Expert, as CSV or Markdown. Filter and Group by appear from a list of 20 rows or more.
- What went to an AI is in one place: the Logbook on Expert. A notification for information no longer has Later.

## 0.3.26 – 25-11-2026

Brain is calmer: what was only for developers is gone.

- No meter, decision trace or thought stream in Brain any more, and so no More menu. Every decision is in the Logbook; on Expert it folds open to its checks, in words.
- On Expert there are no codes under a suggestion or a Logbook line any more, but sentences such as: I am 80 % sure.
- Ready for autonomy?: no double heading, no ring with a number and no percentages; one sentence with Read more to the manual. The thresholds are only in the settings file now.
- Energy without the table per quarter, and without the home battery and car: HouseMind does not steer them, and Home Assistant already shows them.
- The reaction time is in About HouseMind on Expert. My preferences no longer has a Help group.

## 0.3.25 – 25-11-2026

HouseMind is quicker and does less work twice.

- A screen starts sooner: it gets the texts in its own language once, without an English list beside them.
- When someone comes home, HouseMind no longer asks Home Assistant for its whole list of actions every time.
- A room sends its devices to the screen only once.
- When a camera sees someone, HouseMind counts that person at once.

## 0.3.24 – 25-11-2026

HouseMind is safer: an ethical hack found a few holes, and they are closed.

- A garage door, gate or door behind a switch, button or relay is never opened by HouseMind. Not even when someone once marked it as a lamp.
- Only an administrator can pause for longer than an hour. The Logbook shows who paused.
- Someone who is not an administrator goes up to Involved by default and makes house rules for one device. A rule for a room or the whole house is made by an administrator.
- Nobody can put an emergency message away with Later any more. Later now counts for you alone.
- On a shared speaker a yes to a suggestion no longer counts; a no does. Vesta's name may only hold ordinary letters.

## 0.3.23 – 24-11-2026

What you choose under Security is now really kept.

- Which alarm, the sounds, the curtains at the alarm, switching the alarm on by itself, the signal lamps and the curtains at a fire: the screen said Saved, but HouseMind never got the choice. Now it does.
- The tick Also learn from the history now stays as well when you switch it off.
- When saving does not work, the screen says so, and never Saved.

## 0.3.22 – 23-11-2026

When the alarm goes off, or at a fire, the house does more.

- Music and TV go quiet, and the warning sounds again every half minute until it is over.
- Lamps you choose under Security flash red at a break-in. The wall tablet turns red, with the camera's image and a button to the alarm.
- On your phone buttons to the camera, the alarm and 112. Whoever did not see the message after two minutes gets it once more.
- At a fire the shutters you choose go up, an air conditioner goes off, and the message says which door is locked. HouseMind never opens a lock.

## 0.3.21 – 23-11-2026

The alarm can switch on by itself, and you can test it.

- If you said yes to 'Switch the alarm on?' the last five times, HouseMind asks an administrator once whether it may do that itself from now on. It is also in House settings › Security.
- HouseMind then switches the alarm on itself when everyone has left or the house goes to sleep, and says so at once. Only when it is safe: no sensor still seeing someone, no window open. It never switches it off.
- If someone quickly switches it off again twice, HouseMind asks again.
- In Test the house you choose 'The alarm goes off': you see what happens, and with Do it for real the lamps of that room go on and only you get the message, as a test.

## 0.3.20 – 23-11-2026

Vesta shows that the alarm is on, and that it went off.

- When the alarm is on, Vesta is blue with a shield: Secured. At night she stays on Good night, with 'secured' added.
- When the alarm went off and was switched off again, Vesta shows Attention, with when it went off, until someone has seen it.
- Everyone also gets a message, so whoever comes home later knows. When the alarm goes on again, it goes away.

## 0.3.19 – 23-11-2026

Heads-ups for water running away at night, rain with something open and a fault, and automations on one device go off and on together.

- If the water meter counts on a little every hour at night while nobody uses water, you get a heads-up in the morning: perhaps a tap is dripping or the toilet keeps running.
- If it will rain within the hour while a window or outside door is open, everyone gets a heads-up saying what is open.
- If a device reports a fault itself, such as a heat pump or a switch that gets too hot, you hear about it. Overheating and overload are important.
- If several automations steer the same lamp, HouseMind switches them off together and on again together. If it cannot take one over, they all stay on.

## 0.3.18 – 23-11-2026

The alarm is in the Logbook, and after it is switched off HouseMind puts the house back.

- Switching the alarm on, it going off and switching it off are now in the Logbook, each in one line. When it goes off, the line says what HouseMind did, instead of a line per lamp.
- A minute after the alarm is switched off, the lamps and curtains go back to how they were just before it went off. What you changed yourself in that minute, and the light in a room where someone is, stays.
- For every action, Why? now gives the real reason, such as 'because the alarm went off', also after a restart.

## 0.3.17 – 23-11-2026

Every room gets its own light, and HouseMind recognises a shower and stuffy air.

- A kitchen and dining room with one sensor are now rooms of their own. Walk into the kitchen in the dark and its light goes on, also when the dining room is lit. If you keep switching the kitchen light off, HouseMind learns that the dining room's light is enough there.
- If the humidity in the bathroom climbs fast while someone was just there, someone is showering. The light stays on, also when the sensor cannot see you behind the shower curtain, and the fan goes on at once.
- If the CO₂ stays high for a quarter of an hour where someone is, you get one heads-up to air the room. At night there is none: the morning briefing tells you.
- In Test the house, a room's zone now counts as that room's sensor. The dining room then also has Someone comes in and Nobody there.

## 0.3.16 – 22-11-2026

A pet no longer puts on a light at night or wakes up the house.

- In rooms with pets, what only a sensor sees does not count as a person while the house sleeps or nobody is home. Now this also holds for a presence sensor, as it sees a dog just as well.
- A person does count: a camera that sees a person there, someone using a switch there, someone who just came from a room without pets (down the stairs, out of the bathroom), or a resident who just came home.
- The dog no longer wakes up the house early. From half an hour before your usual time to get up, movement wakes the house again. Before bedtime everything stays as it was.

## 0.3.15 – 16-11-2026

The Info page of HouseMind in Home Assistant now shows in short what HouseMind does for you.

- At Settings › Apps › HouseMind, the Info page tells what HouseMind takes off your hands, with three screens of the app and a day with HouseMind.
- The Documentation has new pictures, and now also explains the morning and the night, the heads-ups such as a boiler that does not heat, the Check-up and the levels for a lamp.
- The privacy statement in the Documentation is now the same as the one in the app.

## 0.3.14 – 16-11-2026

If the boiler has not heated for more than a day, you get a heads-up.

- Is the water in the boiler at least 10 degrees colder than it is set to, and has it not heated for more than a day? Then everyone gets one important message, with how warm the water still is.
- Then check the device or its own app for a fault, or whether the power is off. HouseMind itself changes nothing on the boiler.
- If the boiler is off or on away, or the house is on vacation, you do not get this. Once it heats again, the message goes away by itself.

## 0.3.13 – 15-11-2026

The morning briefing is said once again, even when reading it out takes a while.

- A long morning briefing was sometimes said a few times in a row. HouseMind took it for a failure, because reading it out took so long.
- Now a sentence that is still being read out counts as said. If it really does not work, for instance because the speaker is off, HouseMind notices at once and tries again later.
- This holds for everything Vesta says by herself, such as the bedtime check. And HouseMind no longer waits half a minute for the speaker: the rest of the house carries on meanwhile.

## 0.3.12 – 14-11-2026

Test the house: with ‘The house sleeps’ each lamp now shows only once.

- When you choose ‘The house sleeps’ in Test the house, you see one line per lamp: what Good night does when everyone has just gone to bed.
- Before, a lamp sometimes showed twice: off ‘because nobody has been there for a while’ and off ‘because the house goes to sleep’. The first belongs to ‘Nobody there’.
- What HouseMind really does stays the same: in the first half hour of the night the lamps in empty rooms go off and open curtains close.

## 0.3.11 – 14-11-2026

Learning and steps is short; what HouseMind knows of your home is now in Brain, and you can always change it again.

- House settings › Learning and steps now only has learning, the history and the parts. Two buttons take you to Brain.
- Brain has a new tab for administrators: What I know. With tiles for To confirm, Confirmed, Not right and Scenes and scripts, by room, and what belongs to one device folded together.
- Everything can go back, also after That's right or Not right: choose something else, say Not right after all, or put it back to To confirm. HouseMind remembers what it thought itself.
- The automations are now in one place: Brain › Ready for autonomy? › Automations, with what they do and Turn off or Turn back on. The thresholds are in the card of a part; going back to an earlier day is at Start again.
- What comes from AI is marked in purple everywhere: from AI.

## 0.3.10 – 14-11-2026

Good night: when the house goes to sleep, HouseMind turns off what is still on and closes what is still open.

- The lamps in rooms where nobody is go off, also in a room without a motion sensor. The bedrooms and outside stay as they are.
- Curtains and shutters that are still open close, also in summer when it only gets dark after bedtime.
- Once a night, in the first half hour: if you turn something on yourself after that, it stays that way.
- Like every rule it starts with watching and asking. In Test the house you see it with ‘The house sleeps’.

## 0.3.9 – 14-11-2026

Test the house now really does it: choose what happens, and with Do it for real the lamp goes on and the curtain closes.

- Pretend and Try it now are together Play it out. You choose what happens and see at once what HouseMind would do; nothing is switched yet.
- With Do it for real HouseMind really does everything on that list in your house, and you see it flow in the network. After a minute everything goes back by itself, or sooner with Put back. While the test runs, the everyday rules keep off those devices.
- Each line says honestly how it would go for real: I really do this too, I ask first, I only watch, or not and why. A house rule or an automation also stops the test.
- Evening brings the light of that evening: in November it is dark then, and in the test the curtains close. Choices that contradict each other, such as Out of bed and Everyone away, no longer go together.
- The test quarter is now called Walk around yourself: a part is on Autonomous for a while, and you walk through the room yourself.

## 0.3.8 – 14-11-2026

On a computer, Test the house is now a small button at the top right.

- On a computer, in a browser, Test the house is no longer in the menu on the left. You open it with the flask at the top right, next to Help and the tour.
- The panel opens to the right of your house, as before. While it is open, the flask is lit. Tap it again or Close to close it.
- Nothing changes on a phone: Test the house stays the tab Test at the bottom. In a narrower window and in the Home Assistant app it stays in the menu.

## 0.3.7 – 14-11-2026

While you set the levels of a lamp, HouseMind leaves that lamp alone.

- As long as the Levels window is open, no rule, proposal or house rule does anything with that lamp. Only safety goes first.
- A try stays on the lamp until you tap Done or close the window, no longer just a minute. So you can calmly slide and look.
- When you close the window, the lamp is back as it was and HouseMind takes over again.
- The tick box to save the levels for all lamps of the same type too is gone. Save is only for the lamp you are setting.

## 0.3.6 – 14-11-2026

Brain › Rhythm now has Coming up: what HouseMind will do in the coming hours.

- Under the bars of Rhythm you see the next moments of the day: it gets dark, the house goes to sleep, getting up and it gets light. With each moment it says what HouseMind will then do.
- With each line you see whether HouseMind does it itself, asks first, only watches, or may not, with the reason, such as a house rule. So you can adjust beforehand.
- If HouseMind does not know a time yet, it says so. Nothing goes on or off: HouseMind works it out quietly, as on a usual day.
- The page What I would do now under More is gone. What HouseMind would do now in each room you see in Test the house.

## 0.3.5 – 14-11-2026

A sensor in the hall that keeps saying someone is there at night no longer keeps the house awake.

- If a presence sensor in a hall said for hours at night that someone was there, HouseMind thought someone was still up. The house then did not go to sleep, and that night did not count for the rhythm.
- So Brain › Rhythm could show only Workdays, and no weekend.
- Now, in a hall, only someone coming in counts, not a sensor that keeps seeing someone. Someone on the sofa still keeps the house awake.

## 0.3.4 – 14-11-2026

‘More’ in Brain now works on an iPhone too.

- On an iPhone, ‘More’ in Brain showed nothing. Its menu sat in the row of tabs, which scrolls sideways, and it got lost there.
- Now the menu lies over the page, under its button: What I would do now, Decision trace and Stream of thoughts are there again.
- If you scroll while it is open, it stays under its button.

## 0.3.3 – 14-11-2026

In the network a group is now one dot, and every dot says which device it is.

- If a room has a group, as in Devices, the network shows only the group: one dot for all its devices. That goes for lamps, but also for curtains and switches.
- If a small group is inside a bigger one, only the big one is there. So each room keeps room for the rest.
- Point at a dot to see its name and how it is. A group also says how many devices are in it.
- When HouseMind does something with a lamp in a group, the line flows to that group's dot.

## 0.3.2 – 13-11-2026

Curtains and lamps that drop off the network for a moment no longer hold HouseMind back for hours.

- If a device was gone from the network for a few seconds and came back, HouseMind thought someone had operated it by hand. It then left it alone for hours, also on Autonomous. So the curtains sometimes did not close in the evening.
- Now only a real hand counts. Whoever operates a curtain or lamp themselves still goes first.
- Test the house shows under Now the temperature of the room itself, not that of a device such as the oven.
- Under Pretend, the manual now also says how to have it done for real: with Try it now under a line.

## 0.3.1 – 13-11-2026

When a rule is on Autonomous, HouseMind just does it, without a message on your screen.

- The short message with Why?, Undo and the thumbs no longer comes after everything HouseMind does.
- You only see it when HouseMind tries something itself while the rule is still on Propose. So if you set a rule back to Propose, the message comes again too.
- What HouseMind does on Autonomous is in the Logbook, with Why? and Undo.
- No message either after a house rule of your own or after something you said yes to.

## 0.3.0 – 12-11-2026

A bigger update: every device and every room now has an icon, on every screen.

- On Home the cards, the floor plan and the network show the icon of every room and device. A lamp shows how bright it is, with its percentage in it or beside it.
- Devices, Brain, the Logbook, Vesta's questions and notices and Levels show every device with its icon too.
- So do Energy, the Check-up and Test the house. The home battery shows with streaks how full it is.
- HouseMind chooses the icon itself, from what it already knows; a plug with a fridge behind it is a fridge. If it does not know what something is, it gets a square with a dot. You choose another icon in Home Assistant, under Icon.

## 0.2.59 – 12-11-2026

Brain › Rhythm now shows the rhythm of the house as a bar, from the first night.

- For each kind of day a bar of 24 hours, as in the manual: Sleeping, Morning, Away and Home, with the times underneath and a legend with icons.
- If HouseMind is not sure of a time yet, the bar is there anyway: that part is striped, with ‘still learning’ beside it. Before, HouseMind first had to be sure of both getting up and going to bed.
- On today's bar a line ‘now’ shows where you are.
- A kind of day with too few nights yet gets an empty bar that says so.

## 0.2.58 – 12-11-2026

The manual is about a third shorter, with real screenshots.

- The manual is rewritten in all four languages: shorter, in plain words, and checked against what the app really does.
- It has new diagrams, and 18 screenshots from the demo house in each language.
- The part about Help fits the new Help: the question mark opens the manual, the compass starts the tour.
- ‘Read more’ under Vesta’s answer still goes to the same part: every part kept its place.

## 0.2.57 – 12-11-2026

The question mark now opens the manual at once. The tour has a button of its own: the compass beside it.

- Tap the question mark at the top right and the manual is open straight away. At the top you can ask Vesta something, typed or spoken.
- The old Help with short questions per level is gone. Vesta still knows those questions and answers them, with ‘Read more’ to the right part of the manual.
- Beside the question mark is a compass: it takes you on the tour again. In the tour they light up together.

## 0.2.56 – 12-11-2026

At ‘Ready for autonomy?’ the Columns and Export buttons are gone.

- Above the Skills and Automations lists at ‘Ready for autonomy?’ there is no Columns and no Export any more: a row and the detail beside it say it all.
- You still see how many there are in the list.
- Devices, Logbook and House settings keep both buttons.

## 0.2.55 – 12-11-2026

A toilet with only a door sensor: HouseMind can now see from the door whether someone is in.

- For a toilet or bathroom with one door and no other sensor, HouseMind asks once whether it is a small room with one door. The question is under House settings › Learning and steps › What HouseMind knows of your home.
- Say That's right, and shut after going in is ‘someone in’: the light stays on, however long. Open and shut again is ‘gone’: the light dims first and then goes out. When the door stays open, HouseMind does nothing.
- If someone puts the light on while the door is shut, someone is in. It works in every home: it follows from the kind of room and its sensors, not from names.

## 0.2.54 – 12-11-2026

The Logbook sometimes stayed empty; that is fixed.

- When two things HouseMind only watched happened in the same second, the Logbook showed no line at all, only the number.
- Now every line has its own place, and all lines are there again.
- A test now checks this in a real browser.

## 0.2.53 – 12-11-2026

For a room without a motion or presence sensor, Brain now says more clearly why HouseMind does not put the light out there by itself.

- In such a room HouseMind can't see whether someone is still there: a door or a switch only says that someone was. So it only watches for ‘light off when leaving’ there.
- If you set that rule to Autonomous yourself, it now also says that your choice stays saved and counts as soon as there is such a sensor.
- The manual says so too, under ‘Who is there’.

## 0.2.52 – 12-11-2026

The Automations list is calmer too: one row per automation, everything about it in one place.

- Under ‘Ready for autonomy?’ › Automations each automation is one row: a ring with how many rooms are ready, the name, the rooms, one sentence and where it stands.
- Where it stands is shown by a label: ‘HouseMind can take over’, ‘Taken over by HouseMind’, ‘HouseMind learns from these’ or ‘Stays as it is’.
- Tap a row: on a computer everything about that automation is on the right, on a phone in a window. The technical details are under ‘Details’.

## 0.2.51 – 12-11-2026

Brain is calmer: each rule is one row, and everything about it is in one place.

- Under ‘Ready for autonomy?’ each rule is one row: a ring with how often it went well, the name, the room, one sentence and the step. What a rule waits for is said in short.
- Tap a row: on a computer everything about that rule is on the right, on a phone in a window. The counters are ticks, the technical details are under ‘Details’.
- With ‘Waits for something’ and ‘Ready’ you filter quickly.
- What I would do now, Decision trace and Stream of thoughts are under ‘More’ on Expert.

## 0.2.50 – 11-11-2026

The cross and the loudspeaker in Vesta's window are bigger.

- The cross that closes Vesta's window and the loudspeaker that turns her voice on or off are now round buttons, bigger than before.
- The sign in them is bigger and clearer too, also on Deep.
- On a phone they are easier to hit with your thumb.

## 0.2.49 – 11-11-2026

A question about a level now covers the whole room, and for every lamp you see what happened with it today.

- When HouseMind asks whether it may set a level, there is one card: ‘Shall I set the dining room to Dimmed?’, with why, and per lamp what it gets or that it stays as it is.
- One answer counts for all lamps of that question. On the phone and with Vesta it is the same question.
- At the bottom of the ‘Levels’ window is ‘Today with this lamp’: when something changed, what, who went first (the day's rhythm, you by hand, a rule, safety) and how bright and which colour.

## 0.2.48 – 11-11-2026

On Autonomous HouseMind no longer asks anything: it just does it, and no longer asks afterwards whether it was good.

- A rule on Autonomous does it itself, without a question before and without ‘Was that good?’ afterwards. Questions belong to Propose.
- ‘Was that good?’ now only comes when HouseMind tried something itself while it was still on Propose, because nobody could see the question. At most once a day per person.
- That once a day now also holds when the App restarts, for example after an update. Before, the question could then come more often on one day.
- Want to say something yourself about what HouseMind did? The thumbs under the message after an action and at ‘Why?’ stay.

## 0.2.47 – 11-11-2026

Every lamp now has four levels: Dimmed, Normal, Bright and Full, with the brightness and the colour that suit that lamp.

- In Brain › Devices and entities, as an administrator, tap ‘Levels’ at a lamp. For each level you choose the brightness, and the colour: it follows the day's rhythm, or you fix it from warm to white. A lamp that shows colours can get a colour too.
- With ‘Try’ the lamp itself goes to that level; after a minute it is back as it was. With ‘Try a level’ at a room you see whether all lamps there match.
- HouseMind now speaks in levels: ‘Shall I set the dining room to Dimmed?’ Each lamp then gets its own percentage. A calm evening, the night light and the short dim before the light goes out use Dimmed; light on coming in uses levels too, as long as HouseMind has not learned anything there yet.
- A lamp that only goes on and off stays as it is at Dimmed, or goes out if you choose so and another lamp stays on. A new lamp gets the levels of a lamp of the same type.
- On smoke, carbon monoxide or a break-in, the lamps go full on, and now white too.

## 0.2.46 – 11-11-2026

A lamp HouseMind leaves alone on purpose keeps its rules in Brain.

- When you work a lamp with your own button, HouseMind leaves it alone. A rule you chose there yourself now simply stays in Brain.
- Only a device taken out of Home Assistant, or switched off there, counts as removed. Only then does HouseMind keep what it learned for 30 days and forget it after that.
- A new rule still only comes for a device HouseMind may work itself.

## 0.2.45 – 11-11-2026

A new device now gets its rules in Brain at once, and a rule goes when its device is gone.

- When you hang new curtains or add a lamp, the rules that go with it are under ‘Ready for autonomy?’ at once, marked ‘Not seen yet’.
- If HouseMind does not know the kind of a room, such as a music room, it asks an administrator once what kind of room it is. Until then the room gets the rules that fit anywhere, such as closing the curtains when it gets dark.
- When you remove a device, its rule leaves Brain. What HouseMind learned for it is kept for 30 days, in case a device comes back.
- A rule for humidity or CO₂ only comes when the room has that sensor too.

## 0.2.44 – 11-11-2026

A camera that can also detect smoke no longer gives a false alarm. And Vesta stays quiet about what is set to ‘Don't show again’.

- Some cameras can also detect smoke or carbon monoxide. When that was switched off in the camera, HouseMind said after a day that a safety sensor no longer answers. That no longer happens.
- A smoke, CO, gas or leak detector now only counts as silent when the whole device no longer answers. If a real detector goes silent, you still get the message.
- When a message is set to ‘Don't show again’, Vesta no longer says it by herself either: not in the room and not on the phone.
- When you put a message off with ‘Later’, Vesta does not bring it to your phone until then either.

## 0.2.43 – 11-11-2026

The curtains in the bedroom now open when you are up.

- In a bedroom the curtains open in the morning when you are out of bed, nobody sleeps in that room any more and it is light outside. Whoever still sleeps is not woken.
- HouseMind looks with what the room has: a sensor that sees the bed, a presence sensor in the room, or nothing. Without a sensor it only opens them when everyone is out, or at a moment it learned from you.
- HouseMind now also learns from an automation that opens the curtains in a bedroom. In Brain, ‘Opening the curtains when you get up’ is a rule of its own per room.
- In Test the house you can choose ‘Someone is in bed’ and ‘Out of bed’ for a bedroom.

## 0.2.42 – 11-11-2026

Looking changes nothing any more: the screen no longer updates the state of the house.

- The Home screen, the morning card, a wall tablet and the count of rooms where something happens now only look at the house.
- Only HouseMind itself, every half minute, decides whether the house is home, away or asleep. An open screen no longer makes a difference.
- So the house behaves the same, whether a screen is open or not.

## 0.2.41 – 11-11-2026

With a test quarter you walk through your house yourself, while a part is on Autonomous for a while.

- In Test the house, choose a room, how long (5, 15 or 60 minutes) and ‘Start’ next to a part. That part is then on Autonomous, with the real sensors.
- Meanwhile every screen says ‘Test running until …’ at the top, with ‘Stop’. Afterwards the step goes back, and HouseMind puts back what it did, except what someone changed themselves.
- A test quarter switches off no automation and no Adaptive Lighting, does not count for learning, and stops at once in an emergency.
- ‘Stop the test and put everything back’ ends every test at once.

## 0.2.40 – 11-11-2026

In Test the house you can now also try for real: once, and back after a minute.

- Next to a rule that can really be tried, there is ‘Try it now’. HouseMind then does it once, the way it would itself.
- After a minute HouseMind puts everything back by itself. With ‘Keep it’ it stays, with ‘Put back’ it goes back at once.
- If someone changes it themselves in that minute, it stays that way. Locks, the alarm and what an automation steers are never tried for real.
- Without a situation you see what HouseMind would do now. A test does not count for learning, and after a restart HouseMind still puts back what a test did.

## 0.2.39 – 11-11-2026

On Expert you now test HouseMind in your own house, without anything being switched.

- As an administrator, choose ‘Test the house’ in the menu. On a wide screen the panel is on the right, next to the house.
- Under ‘Pretend’ you choose what happens, such as someone coming in in the dark. HouseMind says what it would do, whether that is allowed now, and what it would do on Autonomous.
- Under ‘What just happened’ you see per room what HouseMind decided, which check said no, and how fast it was. A test does not count for learning.
- The Help on Expert shows all questions again: some were missing since an earlier version.

## 0.2.38 – 11-11-2026

What you do, you now see at once: in the list of devices and in the demo house.

- Under Brain › Devices the status follows by itself. When a lamp or sensor goes on or off, you see it at once, without opening the tab again.
- In the demo house the buttons under Play yourself also work while time runs. So you can, for example, see a room become empty by itself after ‘No motion’.
- You still choose stories and jumps in time after Stop.

## 0.2.37 – 07-11-2026

The day's rhythm of the light now follows all of Adaptive Lighting's settings.

- HouseMind did not read part of the settings: the brightness mode and the times around sunset and sunrise. Now it does.
- In the evening the light is therefore as soft as you set it, not a few percent brighter.
- The transition when a lamp goes on, and whether a lamp is only adjusted when it goes on, now follow the settings too.

## 0.2.36 – 07-11-2026

Light now follows the day: brighter and whiter by day, softer and warmer in the evening.

- If you have Adaptive Lighting, HouseMind reads its settings, per group of lamps, and takes them over on Autonomous. You keep setting them there, and after a step back that integration goes on again at once.
- If you do not have that integration, HouseMind makes such a line itself for lamps that can, from what it learned in that room for the evening and the day.
- A lamp that burns goes along with the evening. If you choose another colour or brightness, it stays that way until the lamp goes out, and if you often do, HouseMind remembers it.
- If HouseMind knows little of a half hour, it now picks what suits that part of the day, not the average of the whole day. Check-up says when a group points to a lamp that no longer exists.

## 0.2.35 – 07-11-2026

In the network you now see for longer what HouseMind just did.

- The line from the room through Vesta to the device now flows for twenty seconds instead of five, so you also see it when you look a moment later.
- If HouseMind does something else in that time, the line moves there.
- Under the network, the line with what HouseMind did last, the time and ‘Why?’ stays.

## 0.2.34 – 07-11-2026

Vesta now asks one question at a time, with its buttons, and listens only when you tap her.

- A question from Vesta stays until you answer or close her window. No other question comes in between, also not when something changes in the house.
- The buttons are always there, also while she listens, and also with a question she mentions when you tap her.
- After her answer she no longer goes on listening by herself. To ask something else, tap Vesta, also in her window.
- Her window stays longer after an answer, so you can read it in peace.

## 0.2.33 – 07-11-2026

In the demo houses you can now act out someone in every room.

- Every room of the three demo houses now has a motion or presence sensor, also the kitchen, the bedroom, the garage and the garden.
- So Play the house lists every room under People and movement, and HouseMind responds as it would in a real house.
- The simple demo house now has twelve devices and the average one almost forty.
- The story Waking up now goes through the bedroom, the bathroom, the hall and the kitchen.

## 0.2.32 – 07-11-2026

If you worked a lamp by hand, whoever comes in later simply gets light again.

- As long as someone is in the room, your choice stands. If the room has been empty and someone comes in, that is a new moment and the light goes on again as usual when it is dark.
- If you press a button that puts everything out, rooms where nobody was are not affected: whoever comes in later gets light.
- If you put a lamp on from afar while nobody is there, it stays on, as before.
- In a room without a presence or motion sensor HouseMind no longer puts the light out because it thinks nobody is there. It cannot see that there. It no longer asks to either.

## 0.2.31 – 06-11-2026

The light of the alarm or the doorbell no longer holds a room back.

- An automation that only gives a signal with a lamp, such as a colour for the alarm or a blink for the doorbell, no longer holds anything back. It simply stays on, and HouseMind learns nothing from it.
- While such a signal lasts, HouseMind keeps off the lamp. If the lamp stays on, as with an alarm going off, HouseMind keeps off until that lamp changes.
- An automation that only switches lamps goes along in the one question once the rules of those lamps are proven. It is named in it.
- Does an automation do more, such as switching a socket too? Then it stays on and keeps only its own lamp. The rest of the room carries on.
- HouseMind now also understands the new way Home Assistant writes presence and doors as the start of an automation, so it learns from those too.

## 0.2.30 – 06-11-2026

What goes well often enough, HouseMind does itself after one yes.

- When HouseMind sees something go well often enough, it asks once whether it may do it itself from now on. The question stands in Waiting for you and does not expire. One yes is enough.
- If an automation steers it, the question is about the whole automation, and only an administrator gets it. After yes HouseMind does it itself and the automation goes off. Put back twice in a week: it goes back on by itself.
- The bar and the ring at Ready for autonomy? now show how often it went well, with what is still needed below. If HouseMind only watches, that is there too, with the reason.
- If you do yourself what HouseMind is asking, that counts as a yes. If you put the light out and walk away, that counts too.
- A question now has ‘Myself from now on’ instead of ‘Always’. One lamp no longer holds back a whole group.

## 0.2.29 – 04-11-2026

A plug no longer becomes a lamp because it came back after a restart at the same moment as a lamp.

- HouseMind only derives that a switch belongs to a lamp when it really goes on and off. A restart of Home Assistant no longer counts.
- What HouseMind derived as a lamp that way before, it looks at again right after the update. What you answered with That's right or Not right stays.
- If a label in Home Assistant says a device is something else, such as heating, HouseMind does not make it a lamp.
- So a bed heater or the update switch of a meter no longer stands among the lamps of a room.

## 0.2.28 – 04-11-2026

If lamps are in a group, HouseMind uses and shows the group, not every lamp apart.

- The Logbook, What I thought, the Decision trace and Ready for autonomy? show one line for the group, not one per lamp.
- Leave alone means leave alone: such a device now shows only in Devices, where you take the tick away again.
- If a lamp of a group is set to Leave alone, the group takes it along. Set the group itself to Leave alone and everything in it stays still.
- An extra switch of a plug, such as a charging protection, no longer stands among the lamps.
- The extended demo house now has every kind of group of lamps, and Devices says why a group only gathers.

## 0.2.27 – 04-11-2026

‘Tidy Home Assistant’ is now called Check-up.

- In the menu and on a phone, the page that checks Home Assistant is now called Check-up.
- The window that ‘View’ opens is called Home Assistant check-up.
- Help and the manual use the new name, in all four languages.
- Ask Vesta about the check-up and she knows what you mean; she still knows the old name too.

## 0.2.26 – 03-11-2026

The screen connects again at once when you come back.

- Was the screen out of touch for a moment, for example after an update or when the network was gone? When you come back to the screen, it connects again at once.
- When the network is back, the screen also tries at once.
- While you look at the screen, it never waits more than ten seconds between two tries.
- Help and the manual say what ‘No connection – retrying…’ means.

## 0.2.25 – 03-11-2026

HouseMind now knows the part of the day from your own rhythm: night, morning, day and evening.

- It is night as soon as the house sleeps. If someone is still up, two hours after your usual bedtime.
- The morning starts when the house wakes up, the evening when you usually come home. If HouseMind does not know a time yet, the clock counts.
- The light when you come in and Vesta's greeting follow it. In a bedroom no lamp goes on by itself from the usual bedtime.
- In Brain › Rhythm the top line says what it is now, since when and why.

## 0.2.24 – 03-11-2026

In the network the line now flows only while HouseMind does something, and you can answer a question right there.

- When HouseMind does something, the line flows for a few seconds and then goes out by itself, instead of ten minutes.
- Under the drawing stays what it did last today, with the time and Why?.
- A question stays a dotted line for as long as it is open, with Do it and Not now under it: you do not need the Logbook for it.

## 0.2.23 – 03-11-2026

On the tiles of Ready for autonomy? the ring is now at the bottom right.

- The ring is beside the number of rooms, and never lies over the steps.
- The name has the whole width, so a long word stays whole.
- The rings are in one line at the bottom of the tiles, also on a phone.

## 0.2.22 – 03-11-2026

The tiles on Ready for autonomy? are calmer: the ring is now top right.

- The ring with the score is beside the name, and never lies over the steps any more.
- Together the tiles fill the whole row, also on a wide screen.
- On a phone there are still two side by side.

## 0.2.21 – 03-11-2026

Ready for autonomy? is smaller: the parts are now tiles side by side.

- Each tile shows the name, the steps, the ring and the number of rooms. On a computer they are in one row, on a phone two side by side.
- Tap a tile: below it comes one card with the explanation and buttons of that part, and the list is right under it, with only that part.
- The overview per kind above a long list is tiles too, in Logbook, Devices, Existing automations, Scenes and Tidy Home Assistant.

## 0.2.20 – 03-11-2026

In Energy, a screen reader now says how far the measuring is and reaches the blocks in the plan.

- In the first weeks Energy shows a bar with the days HouseMind has measured. A screen reader now reads it with the same words as above it, such as ‘Day 3 of 14’.
- In the plan (Involved and Expert) a screen reader now also reaches the blocks on the timeline. Choose one and it says what HouseMind does then, and why.
- Every bar in the app that shows how far something is now has a name: in Energy, while learning from the history, while Vesta listens and in the demo house.
- The check that opens every screen on every level, on a phone and a large screen, in light and dark, finds nothing to improve again.

## 0.2.19 – 03-11-2026

Logbook now starts with ‘Waiting for you’, and an answer from AI is lost less often.

- The number at Logbook counts the notifications still waiting for you. They are now at the top of Logbook, with their buttons; once handled, they are in the timeline.
- Each question to AI goes with the form of its answer. Where the service can, the AI cannot answer otherwise.
- If an answer does not fit, what was whole counts. If the AI gives no answer after looking things up, HouseMind asks once more.
- What an integration calls a device itself, such as ‘Lights’, stays a plain word in your question to Vesta.

## 0.2.18 – 02-11-2026

On Tidy Home Assistant, System now comes first, with a ring like the clock in Energy.

- The outer ring shows what is on the disk: the database, the backups, old loose backups, the rest and what is free.
- Marks at 85% and 95% show when HouseMind tells you. The inner ring counts the days since the newest backup.
- Point at a part of the ring and HouseMind says it in words. The tiles with the backup, the database and the disk are below it.

## 0.2.17 – 02-11-2026

The network under Home › Rooms now looks like the design: with Vesta large in the middle.

- Vesta breathes in the middle in her colour, with her word under her. When AI is on, a dotted ring stands around her.
- The rooms are blocks with their names, with their devices as small dots. On a phone the network stands upright.
- When HouseMind just did or asked something, a line flows for ten minutes from the room through Vesta to the device. Under it is what happened, with ‘Why?’.
- When all is quiet, a line says when a line will flow. A legend says what the lines mean.

## 0.2.16 – 02-11-2026

Whether Vesta opens her window with a question by herself is now one choice for the whole house.

- An administrator on Expert finds the choice in House settings, in the group Vesta. Off applies to everyone in the house.
- When it is off, her question no longer pops up for anyone and waits in Logbook. Tap Vesta and she still asks it.
- The choice is no longer in My preferences; an own choice from before no longer counts.

## 0.2.15 – 02-11-2026

Energy ‘Now and today’ now also shows the sun, the grid and the house when a meter has an empty sensor.

- A device can have several power sensors. HouseMind took the first, even when it had no value or measured something else, such as the grid on the solar panels.
- Now it picks the sensor that says what it measures and has a value. If that one drops out, it reads the next.
- When the grid is only measured per phase, HouseMind adds the phases up. When the home battery itself gives nothing, it reads the battery of the system it is part of.
- So ‘Today’ fills up again, and Vesta knows what the house uses now.

## 0.2.14 – 02-11-2026

Tidy Home Assistant is now a page of its own in the menu, only for an administrator on Expert.

- It was a group in House settings, in a small window. Now it has the whole width of the screen.
- On a computer it is in the menu on the left, on a phone it is a fifth button at the bottom.
- On the other levels it is no longer in House settings. ‘View’ on a message about it still shows it.

## 0.2.13 – 01-11-2026

If you say ‘Do it’ when HouseMind asks to set the heating to away, the setting from before comes back when someone comes home.

- When nobody has been home for a day, HouseMind can ask to set a heat pump to away. After ‘Do it’ it forgot how the heat pump was set before.
- So when someone came home, it put the heat pump back on its plain setting, even if it was on eco before, for example.
- Now HouseMind remembers the setting from before and puts exactly that back.

## 0.2.12 – 01-11-2026

When Vesta asks something, she now gives the same reason as the screen.

- For some questions Vesta gave a different reason than the screen. She said, for example, ‘because someone came home in the dark’, while the screen said ‘because someone came home 5 min ago and nobody is outside any more’.
- Now HouseMind keeps the whole reason with every question, so Vesta gives the same one.
- This also goes for ‘Why?’ under her question, and for questions about the evening light or heating.

## 0.2.11 – 01-11-2026

A wall switch of a button automation no longer counts as a lamp for the lighting.

- HouseMind took some wall switches for a lamp, because they go on and off together with one.
- Now the lighting leaves the switches of a button automation out altogether. You work those yourselves.
- So no ‘not done’ shows up in Logbook for such a switch either.

## 0.2.10 – 01-11-2026

A button or wall switch that works a lamp no longer keeps HouseMind away from that lamp.

- An automation that only passes on what you do yourselves, such as a wall switch, a remote or a button in the app, is your own hand. HouseMind now works those lamps as well, also on Autonomous.
- HouseMind never asks to turn such an automation off; the button keeps working. What it does counts as something you did, so HouseMind then leaves the lamp alone for a while.
- HouseMind never flips the switch itself. If HouseMind turned the lamp off while the switch is still on, flip it once more.

## 0.2.9 – 01-11-2026

When you come home in the dark, the light at the entrance goes on, and off again a few minutes later.

- These are the lamps on the driveway, at the front door or at the carport. A garden or terrace at the back stays dark.
- They go off after 5 minutes, later if someone is still outside then. If the light is already on, such as the evening light, it stays as it is.
- An automation that turns on the driveway when you come home and off when you leave now teaches HouseMind. On Autonomous, HouseMind can take it over.

## 0.2.8 – 01-11-2026

If you set something to Autonomous, HouseMind switches off the automation that did it at once. If it goes back, the automation goes back on at once.

- If you set a row or a part to Autonomous in ‘Ready for autonomy?’, HouseMind switches off the automation in Home Assistant that did it at once and does it itself. If that automation does more, such as sending a message, it asks first.
- If it goes back to Propose or Observe, you turn a part off, or HouseMind steps back itself, the automation goes back on at once. That way nothing is left undone; you read it in a notification and in Logbook.
- Learning again, factory settings and removing HouseMind turn them back on first, as always.

## 0.2.7 – 01-11-2026

The outside light goes on when it gets dark, also when nobody is home.

- The house looks lived in and burglars keep their distance. It goes off when the house goes to sleep, and at the latest at the time that suits your house; until HouseMind knows, at 23:30.
- When nobody is home, it goes off around the usual bedtime, a few minutes different each evening, so it does not look like a timer.
- An automation that switches the outside light now teaches HouseMind its brightness and its time. On Autonomous, HouseMind asks whether it may take it over.

## 0.2.6 – 01-11-2026

In the manual, ‘Suggestions from AI’ now stands under House rules.

- The part about the house rules an AI may suggest stood under Morning and evening by mistake.
- It now comes at the end of House rules, in all four languages, and the same in the online manual.
- A link to that part still leads to the right place.

## 0.2.5 – 01-11-2026

The changelog is short and readable, and the Documentation tab explains how HouseMind works.

- The changelog in Home Assistant now shows per version only what is new, in plain words, from 0.2.0 on.
- The Documentation tab explains the three steps Observe, Propose and Autonomous, the parts of the house, and devices and entities.
- Two drawings show what takes HouseMind a step further, and how it learns over time while old habits count less.

## 0.2.4 – 01-11-2026

In Brain › Devices, the lamps of a group now stand indented under the group.

- The group comes first; the lamps in it stand indented under it, along a line.
- So you see at once which lamps belong to a group, such as ‘Garage - Lamp 1’ under ‘Garage’.
- A smaller group inside a bigger one is indented too, with its own lamps under it.

## 0.2.3 – 01-11-2026

The light goes on as soon as you come in, and off after the time that suits your home.

- Come in in the dark and the light goes on at once. In a room you often walk through after 10 seconds, no longer after half a minute or more.
- A motion sensor that is quicker than the presence sensor counts at once again. If it gets dark while you sit in a room, the light goes on without waiting.
- How long the light stays on when nobody is there, HouseMind learns per room from your home. Until then it is short in a toilet, bathroom, storage room or cellar, and longer in a hall.
- If you come back while the light dims, or switch it on again straight away, HouseMind waits longer there from then on. The wait counts from the moment the sensor sees nobody.

## 0.2.2 – 01-11-2026

German and French now show in the language lists without ‘not yet reviewed’.

- In My preferences › Language, German and French no longer say ‘not yet reviewed’.
- The same goes for House settings › Vesta › Language of the house.
- The manual no longer mentions it, in all four languages.

## 0.2.1 – 01-11-2026

The anonymous weekly count is gone: one way out of the house fewer.

- The tick box and the preview are no longer in Upkeep and privacy. It never sent anything, and that stays so.
- The privacy statement, Help, the manual and the website no longer mention it.
- If an administrator ever said yes, that is gone after the update; HouseMind keeps nothing of it.

## 0.2.0 – 31-10-2026

You now see in pictures how HouseMind decides, and HouseMind knows better what each device is.

- You now see how HouseMind decides, as a stream: what it saw, what it thought and what it did. A line that flows is done, dots wait for you, a line that stops was not done.
- On Carefree a thin ring around Vesta has a dot for everything HouseMind did today. From Informed, ‘Today’ under What I thought shows per room what it did, asked or left.
- Under Energy you see the power flow: from the sun and the grid to the house, the battery and the car.
- On Expert, Brain › Decision trace shows each decision as a pipeline with the six steps of the gate, the core and the AI apart, and today from source to outcome. In the network the path of what HouseMind just did lights up.
- HouseMind now knows what each entity is: a lamp, a room's thermostat, something that measures the air or the device itself, a status LED or a setting. It acts on that everywhere and never touches a setting.
- A room shows its entities per device now, with what each entity is. Under What HouseMind knows of your home you confirm per device in one go.
- The own light of a sensor or voice assistant no longer counts as light in the room.
- A room with a presence sensor is empty as soon as that sensor sees nobody.
- With AI, HouseMind understands your home better and Vesta is smarter: she looks things up and keeps talking. Something important she always says in her own words, and the core never waits for the AI.
- With AI you see your habits and the weekly report in plain words, you get a question about something unusual, such as a lamp on for hours in an empty room, and the AI suggests house rules.
- You can choose the AI of Home Assistant itself. If it works in the house, an administrator can have camera images described; they never go to the cloud.
- For each question to the AI you see the instruction: why HouseMind asks and what answer it wants.
- A hob or oven that is on while nobody is home becomes a question to switch it off.
- Brain has tabs, you turn parts on and off in House settings, and Don't show again is the administrator's choice.
- About HouseMind has ‘Good to know’. The manual is shorter and is now also in German and French.

![What's new in 0.2.0](data:image/jpeg;base64,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QskfZ08bM0VixoJkaxuWzhfS+/r39a1Ejg+Rzg0NBJIaOrwUUQEREBERBdpZaVlHURyyTCSUAANjBAsb78w/ovZpqXkDIYHzNfo6QOjFnu/7s24a20VFEBERAREQei1xckDrsFszV0bKijkjdO8QMynNGG23kOHON9Tu8Fq0QWsRqGVMzXtLnEMDXPc0NLyOuwJtpYfwqqIgIiICtYe+mjmL6oPIaLsDWBwzdVwSLhVUQbSkxKKCpvLGaiJ1Q2V73Cz3WPWL2PXoStWiIJR9I3wN0Qc0E9Z3IpidIeA2NwvbtO8WPgoooSlZned6fulmd53p+6iiCVmd53p+6WZ3nen7qKIJWZ3nen7pZned6fuooglZned6fulmd53p+6iiCVmd53p+6WZ3nen7qKIJWZ3nen7pZned6fuooglZned6fulmd53p+6iiCVmd53p+6WZ3nen7qKIJWZ3nen7pZned6fuooglZned6fulmd53p+6iiCVmd53p+6WZ3nen7qKIJWZ3nen7pZned6fuooglZned6fulmd53p+6iiCVmd53p+6WZ3nen7qKIJWZ3nen7pZned6fuooglZned6fulmd53p+6iiCVmd53p+6WZ3nen7qKIJWZ3nen7pZned6fuooglZned6ful2jcL8VFEHpNzcovEQegX8B2r3MBub/JR2lm9m/iooJZz2N9ITOexvpCiiCWc9jfSEznsb6QooglnPY30hM57G+kKKIJZz2N9ITOexvpCiiCWc9jfSEznsb6QooglnPY30hM57G+kKKIJZz2N9ITOexvpCiiCWc9jfSEznsb6QooglnPY30hM57G+kKKIJZz2N9ITOexvpCiiCWc9jfSEznsb6QooglnPY30hM57G+kKKIJZz2N9ITOexvpCiiCWc9jfSEznsb6QooglnPY30hM57G+kKKIJZz2N9ITOexvpCiiCWc9jfSEznsb6QooglnPY30hM57G+kKKIJZz2N9ITMDvb/IUUQekW4dqL1ut29u5ECTpHcSvYZNlMyQNa/I4OyvF2m3UR1heS9I/iVFB2dVXmbAcMkgwnC+VV80sJy0bBuLQ23Ydd61lb7NxwwVfJcSiq6qh1qYGxublF7EtJ/VY71TOMvbh+G00UeSSgmfM2XNfMSQRpbS2XtVys9o4JIq11HhraWrrxaomExcLE3Ia23NuR2l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)

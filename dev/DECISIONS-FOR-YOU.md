# Decisions I need from you

Plain-language companion to `OUTSTANDING.md`, which says the same things in
technical terms. Nothing here is built yet — these are choices that change what
gets built.

**Nine of the eleven are settled.** They are kept below with their answers
rather than deleted, so the reasoning stays findable. Two are genuinely open,
and they are first.

**This file said "five of eight" until 2026-09-01 and listed four decisions as
settled *and* still-open at the same time** — the answers had been recorded in
the table at the top and the original questions left standing underneath. That
is fixed here, and it is the failure this file is most prone to: it is written
twice, once as a table and once as prose, and only the table was being updated.

---

## Still open — two

### A. Is "steak on Wednesday" a preset thing, or a weekly thing?

**Raised by your own instruction** that all customisation should go through
presets where possible — which is a good rule and this is the one place it
might go too far.

Pinning a specific recipe to a specific night can work two ways:

- **A weekly veto.** You say it this week, it applies this week, it is gone next
  week. This is what is currently designed.
- **A preset field.** "My comfort week always has steak on Wednesday", carried
  by the preset and reapplied whenever you pick it.

They cost the same to build — three dropdowns either way. What differs is what
happens when you delete that recipe from your library: a preset holding a
pointer to it needs a rule for what to do (my answer: generate that slot
normally and say so, never fail).

**The research argument for the weekly version is the interesting bit.** Rigid
all-or-nothing rule sets correlate with *regaining* weight afterwards. The
design's position is that the system should hold the numbers and you should
keep the vetoes — that some things stay yours, week to week, precisely so the
whole thing does not become a set of rules to break.

**My recommendation: build the weekly one first, decide the preset version
after you have used it for a month.** The preset form is a pure addition on
top; nothing is wasted either way.

### B. Is "preset" the right word?

You have been calling them profiles. I recommend **preset** for one boring
reason: there is already a `config/profile.json` holding your body's details,
and a word doing two jobs in a *filename* is worse than in prose.

Mechanical to change, but it should be settled before a file called
`presets.json` exists and gets referenced everywhere.

---

## Two things only you can do

### 1. Cronometer — **working, keep going** ✅

Eight days now stored, 24th through 31st, unbroken. That is the logging
landing.

The effect is already visible. The app's estimate of what you burn has moved
from **1710 → 1858 calories** as the log filled, and the gap to the formula
estimate (2573) has narrowed from **33.5% → 27.8%**. It needs to be inside 25%
to be accepted, so it is close but not there.

**Two days look partly logged**, and they are pulling it down:

| | | |
|---|---|---|
| 29 Aug | 429 cal, 17 g protein | almost certainly a partial day |
| 30 Aug | 1262 cal, 39 g protein | low protein for you — looks partial too |

Your other six days average **1841**. Worth a quick look at whether those two
were genuinely light days or just unfinished.

**Two more weeks still matters, even with a full log.** The estimate compares
what you ate against how your weight moved, and your weight is currently *flat*
(99.71 → 99.77 kg over 7 days). Over one week that number is mostly water, not
fat — the app's own minimum is 7 days and you are exactly at it. Fourteen days
is where the trend starts meaning something.

Nothing to do but carry on. I would re-check around **8 September**.

### 2. Hevy key — **there is somewhere to put it now** ✅

I have added a `HEVY_API_KEY=` line to your `.env`, with a comment explaining
what it is. Paste the key after the `=` and it is done — nothing else needs to
change, and do not send it to me.

I have already confirmed Hevy records **effort per set**, which makes gym
progress measurable without ever doing a max-weight test — and detects fatigue
*before* your lifts drop (same weight, same reps, felt harder). The only thing
left is whether your own logs fill that field in, which is one check once the
key is in place.

---

## Settled

| | Decision | Answer |
|---|---|---|
| 1 | What to call the weekly modes | **Preset**, pending B above |
| 2 | Must a strict block say what comes next | **Yes — required, with an explicit override** |
| 3 | Bad week: loosen rules or budget treats | **CSIRO treats**, and it is a preset setting, not a global one |
| 4 | How strict is "processed" | **Neither.** "Strict" stops being a concept |
| 5 | How many treats, how big | **Defined per preset**, same as 3 |
| 6 | 5:2 ready-made? | **Yes — and it needs no new machinery.** It is the Fast 800 strategy on two days |
| 7 | What is a lazy week? | **Left undefined on purpose.** The dials get built; you write the preset |
| 8 | What next? | **Freezer** — designed in `design-04` |
| 9 | Two goals — body *and* fitness | **Yes, confirmed** |
| 10 | How much can a preset actually control? | **Almost everything** — audited, 2026-09-01. See below |
| 11 | Can a preset change how long food keeps? | **No — and it stops being one number at all.** See below |

### 10 — How much can a preset control? Almost everything

You restated the requirement twice, and the second time was stronger:

> *"I want presets to be the predominant way to customise meal planning.
> Everything should be on the table for analysis for integration into
> presets."*

So I went through **every** number written into the planning code — about fifty
of them — and ruled each one. The result:

| | Count | Meaning |
|---|---|---|
| **A preset can set it** | 25 | anything from how many cuisines a week has to how much protein powder goes in a shake |
| **Stays in a settings file** | 4 | all four are already editable; they just are not week-to-week choices |
| **Stays in the code** | 6 | see below |

**The six that stay in the code are a much better list than the eleven I first
produced**, and the reason is a mistake worth telling you about. My first pass
ruled things "not a preset" because *a mood* would not vary them. That is the
wrong question — the right one is whether a **week** would, and a week with
guests, a long weekend, a training block and a lazy week are all weeks. Under
the right question, "how many people am I cooking for" is obviously a preset
thing, and I had it filed as a life decision.

The six survivors each fail a specific test rather than my taste — mostly they
are arithmetic (the calories-equal-protein-plus-carbs-plus-fat identity), or
they would produce a week the app itself rejects, or they change nothing about
the plan at all (one of them only decides when a warning is printed).

**One thing I got wrong in your favour and then had to walk back.** I ruled the
gym-morning shake "code" because there is a real nutritional argument for it —
a shake is the only breakfast you can drink ten minutes before lifting. That
argument is correct and it is an argument for a **default**, not for a lock.
You can now set a different one; the reasoning goes in the help text next to
the field.

**Three things need a small code change before they can be settings at all**,
because the numbers are currently written into English sentences the AI reads —
"no more than two dinners with the same protein", "max 2 slices of bread per
serving", and "a long cook is 60+ minutes". That last one turns out to be
written in **four separate places** that have to agree by hand, which is a bug
in its own right and worth fixing whether or not any of this ships.

**What this costs on screen:** the settings panel goes from nine groups to
nineteen. Nine stay open and ten go behind an "Advanced" fold — every one of
them is still a preset setting, just not one you trip over. The point of your
rule is that nothing is locked in the code; a setting being one click down is
not that.

### 11 — Food keeping times: not a preset, and not one number either

You said this directly: *"it needs to be a per-dish measurement, not part of
preset."* Agreed, and it is already the design in `design-05`.

**The reason is better than "a preset should not touch food safety".** The app
currently uses **one** number — 3 days — for everything, and that number is
wrong in both directions at once:

| Dish | Actually keeps | App says | |
|---|---|---|---|
| a beef stew | 4 days | 3 | a day of good food thrown away |
| a rice tray bake | **2 days** | 3 | **unsafe** — cooked rice grows something cooking does not kill |

A preset over that number could only ever have picked a *different* wrong
number. So instead the AI reports what **kind** of dish it made, and a small
reference table says how long that kind keeps. You get more accuracy than a
preset could have given you, and the rice case gets fixed rather than averaged.

I had floated a version where a preset could only ever make the window
*shorter*. That is dropped: it fixes the stew case never.

**This is now the first thing being built** — moved to the front on
2026-09-01. It was designed and then ranked behind three preset prompts, which
meant the only change in the whole plan where being wrong makes you *ill* was
the one queued last. Everything else on the list makes the app more flexible;
this one fixes something that is wrong right now, today, on your shipped
config. It also has to go first for a practical reason: the freezer work and
the "declare the week's shape" work both let batched food stretch further, and
stretching further while one wrong number is the only limit makes the rice case
worse rather than better.

### What 2 turned into: the app plans short and commits long

You said this was why you wanted a longer planning horizon, and you were right
in a way I had answered too narrowly. I read "longer horizon" as "plan 14 days
at once" and explained why that is not needed. **The real gap was that there
was no plan *above* the week at all** — blocks existed one at a time, with
nothing saying a strict phase is followed by a staged return.

The research describes a **20-week arc**: about 12 weeks restricted, then three
stepped increases of a couple of weeks each, then normal eating. Every one of
those weeks is still planned seven days at a time. So:

- **what gets generated:** one week
- **what you commit to:** the whole arc

Each block names what follows it, and the app draws the run as a timeline. The
practical effect is that at the moment you commit, you see *"two weeks at 800,
then six weeks stepping back up"* — not just the two weeks. Which is the honest
version of what you are actually signing up for.

Skipping the return is possible, but it has to be an explicit switch, and it
gets recorded — so "I know what I'm doing" never looks the same as "nobody
noticed".

### What 4 turned into: no more "strict"

You said strict is not a term you should be catering for, and that the logic
belongs in the preset. Agreed — and making that work means pinning down exactly
one thing so everything else can move freely.

There were two questions hiding in "how strict is processed", and only one is a
preference:

- **How processed is this dish?** A fact about the food. Has to be measured the
  same way always, or the number means nothing and you cannot search on it. It
  will use the worst ingredient, which is what the shopping list already does.
- **What is allowed this week?** Entirely the preset's business.

Which gives you *more* than the strict/lenient choice I originally offered. A
preset can say "nothing processed", or "convenience allowed but at most two
such ingredients per meal", or "allowed on Friday only", or say nothing at all.

### What 7 turned into: a lazy week gets dials, not a definition

Worth separating, because you reached for "allow more processed food" and I do
not think that is quite what you meant.

A 40-minute from-scratch dinner is not an easy week. A tin of lentils in a
10-minute bowl is — and it is barely processed at all. **Effort and processing
are different things**, and the app already tracks cooking time.

So a lazy week relaxes cooking time first and allows a limited number of
convenience ingredients second — a cap, not a free pass. The dials get built
and you write the preset; I am deliberately not shipping a "lazy" definition
you would then have to argue with.

The audit in 10 above widened this considerably: how many meals come from
recipes you already like, how hard the week pushes for variety, and how many
cuisines it runs are all now dials a lazy week can turn.

### What 9 turned into: two goals that can disagree out loud

A commitment carries two:

- **a body goal** — lose 8 kg, hold, or reshape
- **a fitness goal** — raise VO2 max, get stronger, or maintain

They are pursued at the same time and **they work against each other.** Eating
in a deficit compromises recovery, so the research lowers every
back-off-from-training threshold by about 20% during a fat-loss phase, and it
blunts the gains a VO2-max block is trying to produce.

With one goal you would have to pick, and the app would never know a trade was
being made. With two, **it can see the conflict and tell you** — rather than
quietly serving one and letting the other drift.

Which is your situation right now: your stated fitness goal is VO2 max, and you
are 99.8 kg against a target of 80. Those pull in opposite directions today.

On your wording — "meal strategy" felt wrong to you and I think that is because
**the thing is broader than food.** A preset covers what you eat, how much
cooking you are up for, and how much effort a week can take. "Meal strategy" is
only the first third of it.

---

## The order things get built

Added 2026-09-01. The full version with dependencies is in `dev/README.md`;
this is the plain-language one.

| | What | Why here |
|---|---|---|
| **1** | **Food keeping times** (§11) | The only thing on the list that is wrong *now* rather than missing |
| **2** | **The hard-coding audit** | Writes no code. Produces the list of what a preset is allowed to change, which the editor then shows |
| **3** | **Presets themselves** — the file, and picking one each week | Everything about flexibility hangs off this |
| **4** | **The preset editor** | So you can define a preset without editing JSON. This was always the point |
| **5** | **"Fast 800 for four days"** and **"steak on Wednesday"** | The two things you are most frustrated by. Both are cheaper once presets exist |
| **6** | **The freezer**, then **declaring the week's shape** | Both need §1 first |
| **7** | Cronometer export · calendar location · Hevy | Independent. Each starts with a five-minute manual test |
| **8** | **Blocks** — "Fast 800 for the next 14 days, then step back up" | The biggest piece. Needs 3, 4 and 5 present |
| **9** | Training analytics | Read-only first: show you the numbers, earn trust, and only then let anything suggest changing a session |

**Two things moved after a review of the whole plan on 2026-09-01.** Food
keeping times went from the middle to the front, for the reason in §11. And
"Fast 800 for four days" and "steak on Wednesday" moved from *first* to
*after presets* — not because they got harder, but because both turned out to
need something presets build anyway, so doing them first would have meant
building it twice.

**Four real mistakes were found in the plan and fixed**, all before any code
was written, which is the point of writing it down first. The one worth knowing
about: a "bad week, take it easy" preset would have quietly wiped your entire
banned-ingredients list — all 17 of them, including the seed oils and the fruit
you avoid — with no error and nothing on screen to show it. The mechanism that
did it looked obviously correct and had been through two rounds of review.

## What I still have to work out — no input needed

- **How frozen portions are tracked between weeks.** This is the gap blocking
  your Monday/Tuesday/Thursday example, and it is the biggest single piece of
  unfinished design.
- **Correcting Garmin's calorie-burn figure** against what your weight actually
  does. This is the tool that would settle the 860-calorie disagreement above.
- **Making sense of your sleep and heart-rate-variability data.** It is being
  collected every day and read by nothing.
- **When to back off training** — the rules for spotting accumulated fatigue
  and easing off before it becomes a problem.
- A handful of smaller things, all listed in `OUTSTANDING.md`.

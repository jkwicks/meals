# Meals User Manual

A plain-English guide to the AI Weekly Meal Planner.

---

## 1. What is this app?

Meals plans **a whole week of food for you**. You tell it your nutrition goals (calories, protein, carbs), and it:

- Fills a seven-day grid with breakfasts, lunches, dinners and snacks that add up to your targets
- Works around your workouts (feeding you more on training days)
- Builds your **shopping lists**, split into one list per shopping trip
- Keeps a library of recipes you love, so you can bring them back any time

Behind the scenes, a computer program does all the maths and a food-savvy AI picks the actual dishes. You never have to touch that part — you just click buttons.

---

## 2. Getting started

1. Open the Meals folder on your computer.
2. Open a Terminal window, and type:

   ```
   ./scripts/server.sh start
   ```

3. Open your web browser and go to: **http://localhost:8080**

That's it — the app is now running. You can leave it running in the background.

> **One-time setup note:** the app needs a free "OpenRouter" key (like a password that lets it talk to the AI). If that hasn't been set up yet, ask whoever installed the app, or see the project's README under "Quick Start". You only do this once.

Other useful commands:

| Command | What it does |
|---|---|
| `./scripts/server.sh status` | Checks if the app is running |
| `./scripts/server.sh stop` | Turns the app off |
| `./scripts/server.sh restart` | Turns it off and on again (fixes most odd behaviour) |

---

## 3. A tour of the screen

Along the left side you'll see six buttons. Here's what each one does:

| Button | What it's for |
|---|---|
| **Plan** | The main screen — your week laid out as a grid of meal cards, seven days across and one row per meal. This is where you'll spend most of your time. |
| **Today** | Just today's meals, so you can see what's on the menu without hunting through the week. |
| **Shopping** | Your shopping lists as a full page, one per trip — the comfortable way to work through them in the shop (see section 7). |
| **Library** | Your personal recipe collection — every meal you've saved or imported lives here. |
| **Insights** | Charts and summaries of how your week is tracking against your goals. |
| **Settings** | Practical options: which day your week starts on, which days you shop, and which AI brain the app uses. |

Across the **top of the screen** you'll also see:

- **Coloured bars** — one per day, showing at a glance how that day's meals stack up against your targets.
- **Current Week / Next Week selector** — the app keeps two weeks on file at once (see section 5.5).
- **A shopping-list icon** — slides your shopping lists in from the right, beside the week (see section 7).
- **A printer icon** — downloads a printable PDF menu (see section 8).

Beneath those bars is the **"staged changes" bar**. Think of it as a sticky note listing everything you've changed that will take effect on the *next* week you generate. If it names something you've edited, that's the app telling you "I've got that ready for next time."

---

## 4. Generating your week

This is the big one. Follow these steps:

1. On the **Plan** screen, click the **Generate** button. A window called the **review dialog** opens — this is where you set everything up before the app starts cooking up ideas.
2. In the review window, check and adjust anything you like (details in section 5):
   - **Daily targets** — how many calories, protein and carbs you want each day
   - **Training schedule** — any workouts this week
   - **Pantry clear** — food in the fridge you'd like used up
   - **Cuisines, cooking styles and batch-cooking preferences**
   - **People per meal** — how many people you're cooking for
3. Click **Generate**.
4. A progress window appears. The app works through your meals one type at a time (dinners first, then the rest). You'll see a progress bar and messages scrolling by as it works.

> **Good to know:** generating a full week can take **5 to 20 minutes** — real recipes are being written for you, and that takes time. You don't have to sit and wait: you can keep clicking around the app, open your shopping lists, even switch to another browser tab. When it's done, your new week appears on the grid automatically.

> **Good to know:** if a single meal can't be created, you'll see a red **"NOT GENERATED"** card. That's fine — everything else is kept, and you can retry just that one meal (see section 5.4).
---

## 5. Making the week yours

### 5.1 Changing your daily nutrition targets

In the review window, **Daily targets** shows one row per day with calories, protein and carbs. Type new numbers for any day you want to change. You only ever type three numbers — fat works itself out automatically (eat fewer carbs and the app quietly makes up the difference with fat).

- A day you've edited gets an **amber dot** next to it, and its name appears in the staged-changes bar (e.g. "Mon +200 kcal").
- Your changes apply to the **next week you generate** — they don't rewrite the week you're looking at, so you can compare.
- Changed your mind? Use **"Discard pending changes"** on the staged-changes bar, or reset a single day from the review window.

### 5.2 Adding workouts

In the review window, open **Training schedule**. Each row is one workout: which day, what time, what type (weights, running, walking, or rest), how long, and roughly how many calories you'll burn.

When you add a workout, the app automatically:

1. **Adds extra food to that day** — more protein for weights days, more carbs for running days.
2. **Puts extra carbs in the meal right after your workout** to refuel you.
3. **Keeps meals just before a workout light** so you're not exercising on a heavy stomach.

A training day gets a green **⚡** marker so you can spot it at a glance.

### 5.3 Using up what's in the fridge

In the review window, **Pantry clear** is a simple list of things you'd like eaten this week — "600g chicken thighs", "half a bag of spinach", whatever you like. The app will *prefer* to include them where they naturally fit. It won't force them into a meal where they don't belong (no chicken in your breakfast smoothie).

> **Good to know:** anything you give a weight to is now **taken off your shopping list**. Put "600g chicken thighs" in the pantry and a recipe needing 800g asks you to buy 200g; a recipe needing 400g drops off the list entirely, with a line at the bottom saying so — so you can spot it if the pantry is out of date. An item with no weight ("half a bag of spinach") stays on the list and is simply marked "some already in the pantry", because the app has no number to subtract and won't invent one. Nothing is remembered between runs: the list is worked out fresh from whatever your Pantry clear says right now, so once you've eaten something, just delete the row.

### 5.4 Cook once, eat twice — and fixing a meal you don't like

- **"Link to next lunch"** (on any dinner card): one click tells the app "tomorrow's lunch is tonight's dinner". The dinner's recipe and shopping amounts automatically grow to cover the extra meal. Both cards get a coloured dot and line showing they're linked, and hovering over either card highlights the pair.
- **Regenerate one day** (refresh icon next to a day's name): re-does every cooked meal for that day only. Everything else stays put.
- **Regenerate one meal** (small refresh icon on a single card): the tiniest fix — only that one meal gets a new recipe, with the rest of the day untouched. Use this on a red "NOT GENERATED" card to retry it.

### 5.5 This week and next week

The selector at the top of the screen switches between **Current Week** and **Next Week**. Looking at either one never changes it. The Generate button always says which one it's about to build ("Generate Current Week" or "Generate Next Week"), so you can't accidentally overwrite the wrong week. It's a handy trick to generate next week while you're still eating this one.

---

## 6. Your recipe library

The **Library** section is your personal cookbook. Recipes get into it three ways:

- **Bookmark** — click the bookmark icon on any meal card to save that recipe as a favourite.
- **Import** — paste a recipe's text, a list of ingredients, or even just a web link, and the app reads it into a proper recipe with quantities and nutrition worked out.
- **Swap** — click the ⇄ icon on any meal card to replace it with something from your favourites. The app shows you what the swap costs nutrition-wise before you commit.

Saved recipes never disappear, even when the week they came from is long gone. Re-cooking an old favourite next month finds the original entry rather than saving a duplicate.

---

## 7. Shopping lists

**Two ways in, showing exactly the same thing.** The shopping-list icon slides the lists in from the right so you can read them *beside* the week they came from — handy for "what is Wednesday's trip actually for". The **Shopping** tab in the left-hand rail opens the same lists as a full page, which is the better one to work through in the shop. Tick something in one and it stays ticked in the other.

**How they're organised:** instead of one giant list, you get **one list per shopping trip**. If you shop on Sunday and Wednesday, you get two lists — the first covering meals cooked Sunday to Wednesday, the second covering the rest of the week.

- Each list is grouped by supermarket section, **in the order you'd actually walk a shop**: fresh produce first, then the dry middle aisles, then the fridges and the meat and fish counters at the end — which is also the right order for keeping cold things cold. Each section header shows how many items are in it.
- If you linked a leftover or regenerated a meal a minute ago, the amounts are already updated — the list is always rebuilt fresh from your current week.
- Some items get a note like "buy fresh closer to the day" — the app is reminding you not to buy that perishable too early. Others say "600g already in the pantry", which means the amount shown is what's left to buy after what you told it you already have (see section 5.3).
- **Ticking things off works while you shop.** Ticks are per-trip and live only in that browser tab — they're a scratch list, not something the app remembers — but nothing else you do in the app will wipe them out from under you.

**Taking the list shopping with you:** click **"Copy for Keep"** on a list. It copies the list to your clipboard in a format that, when pasted into Google Keep, turns every line into its own tickable checkbox — perfect for ticking things off in the supermarket. The first line names which trip it is (`═══ Shop Wednesday ═══`) so two lists pasted into Keep don't get muddled, and each section header is ruled off (`── PRODUCE ──`) so you don't find yourself trying to buy "Dairy & Eggs".

> **Good to know:** if a list looks shorter than expected, check whether any meal card on the grid is red ("NOT GENERATED") — a failed meal leaves a note on the list rather than its ingredients.

---

## 8. Printing your menu

Click the **printer icon** in the top bar. The app downloads a PDF (`weekly_menu.pdf`) containing:

- A one-page overview of the whole week
- A prep checklist if you have a batch-cooking day planned
- Every recipe, one page each, grouped by meal type
- The complete shopping list

Print it straight from your PDF viewer and stick it on the fridge.

---

## 9. Changing settings

The **Settings** section holds the practical options:

- **Week start day** — which day your planning week begins on (e.g. Monday or Sunday).
- **Shopping days** — the days you actually shop, which control how the shopping lists are split.
- **Model** — which AI "brain" the app uses. If week generation has been unreliable lately, switching to a different model here can fix it. (This choice lasts for one run only; the app's saved default doesn't change.)

---

## 10. Something not working?

| Problem | What to do |
|---|---|
| A meal card is red and says **"NOT GENERATED"** | Click the small refresh icon on that card to retry just that meal. |
| Generation failed with an error about a key or API | The OpenRouter key is missing or wrong. Check the setup in section 2, then click Generate again. |
| Generation is taking forever or came back empty | It's usually the AI model misbehaving rather than a crash. Try `./scripts/server.sh restart`, and if it keeps happening, try a different model in Settings. |
| A training day's meals didn't get any bigger | Check the workout's "type" is one the app recognises (weights / running / walking / rest). An unrecognised workout type is quietly ignored. |
| Calories are right but protein is too low | Portion size can only scale a meal up or down — it can't change what the meal *is*. If this keeps happening, try a different model in Settings. |
| The shopping list looks wrong | Double-check you're looking at the right trip (lists are split by shopping day) and that no meals are red on the grid. Lists rebuild from the week every time you open them. |
| The app won't open at all | Run `./scripts/server.sh status` to check it's running, and `./scripts/server.sh restart` to give it a fresh start. |

If you're stuck, the project's README has a deeper troubleshooting section — this manual is deliberately the friendly version.
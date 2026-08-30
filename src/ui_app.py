"""NiceGUI front end — the whole week on one high-density desktop screen.

The only web front end, and now a complete one: it can both generate a week and
rearrange it, so `python planner.py` is an alternative rather than a
prerequisite.

**Generating is the only thing here that calls the model**, and it writes
everything at once — `week_plan.json` and a history entry per cooked day —
because a generated week that isn't saved is a 20-minute run one browser
refresh from being lost. Grid *edits* ("Link to next lunch", a favorite swap,
a skip estimate) are in-memory only until they're acted on: "Discard pending
changes" throws them away, "Generate week" folds them into a full re-plan, and
the staged bar's "Save changes" (`ui_generation.save_grid`) writes them to
`week_plan.json` directly, with no model call — a deterministic edit needs no
LLM in the loop to be fully decided. Whichever of Save/Generate ran, it clears
the grid-edit part of the staged-changes bar, because saving is what it just
did — see `PlannerState.pending_changes()` for why the target/training/pantry
parts deliberately do not.

Four regions, mirroring how the week is actually read:

- **Header** — shared chrome above every destination, and read-only apart
  from the week selector: the selector itself, the reporting strip (week
  dates, plant-diversity count, and the week's shape — cook sessions, days
  cooking, portions, shopping trips), and macro telemetry (one horizontal
  bar per day, in the same 8-column grid the canvas below uses, so a day's
  bar sits directly above its column of meals).
- **Staged-changes bar** — one persistent strip, visible under the header no
  matter which destination is open, naming everything staged for the next
  generation and offering Review / Generate week / Discard pending changes.
  See `ui_staged_bar.py`.
- **Rail** — a slim vertical tab strip (`ui.tabs().props("vertical")`)
  choosing one of five destinations: Plan (the week grid — `ui_plan.py`),
  Today (`ui_today.py`), Library (the recipe catalog and import —
  `ui_catalog_browser.py`), Insights (the trend charts — `ui_insights.py`), Settings
  (`ui_settings.py`) — plus, between the first two tabs and the last three,
  the **action block** phase 6b collected out of `ui.header()` and the Plan
  panel: Generate, Shopping, Shuffle styles, PDF menu, Mobile page. The
  destinations answer "what am I looking at", these answer "what do I want
  to do", and nothing else on the page is clickable chrome except the week
  selector and the staged-changes bar.
- **Destination panel** — whichever of the five is selected, in a
  `ui.tab_panels` beside the rail. Every panel builds once and stays mounted
  (tab-panel style, not routed pages), which is what makes navigating away
  from Plan with unsaved edits and back lose nothing: `PlannerState` is never
  reconstructed, only hidden and shown.

This replaced a three/four-region layout (left drawer / header / canvas /
right-hand shopping drawer) in phase 3 of `ui-redesign.md`. The drawer held
five kinds of work with nothing in common but "global" — actions, per-run
inputs, plan inputs, a content library, and a readout — each communicated
with its own ad-hoc disclaimer. Giving each kind of work its own destination
(or, for per-run inputs, the review dialog) is what let those disclaimers
collapse into one honest staged-changes bar. The right-hand shopping
slide-over is untouched by this — a different drawer, opened from the rail's
own Shopping button since phase 6b (it was in `ui.header()` before).

Why this can await the repository directly
------------------------------------------
NiceGUI page handlers run *on* the event loop, so `await REPOSITORY.load_*()`
is the natural call here — this is the async repository paying off. Do **not**
reach for `repository.run_sync()` in this file: it detects the running loop and
hands the coroutine to a scratch thread, which is pure overhead when we are
already async, and would serialise page loads behind a thread pool.

The same rule is what makes the Generate button viable. `generate_week_plan` is
awaited straight from its click handler, and it keeps the loop free by
dispatching each day's blocking API call to a worker thread — so the progress
modal actually animates during the run instead of painting once it is over, and
other tabs stay responsive throughout. Anything long added here must do the
same; a bare blocking call in a handler freezes every connected browser.

Why refreshables, and not a re-run model
---------------------------------------
A re-run front end re-executes its whole module on every widget interaction,
forcing the grid into a session-state cache purely to survive that. NiceGUI
keeps the Python objects alive per client, so the UI binds to a `PlannerState`
and only the `@ui.refreshable` sections that depend on a changed field are
re-rendered. Changing the week start repaints 7 columns, not the page.

State is created *inside* the page function on purpose: module-level state
would be shared by every browser tab connected to this server.

This file is a page shell, not the whole UI
--------------------------------------------
Every widget used to be a closure inside this one ~2000-line function. It is
now split by concern into flat-sibling modules — `ui_cards` (the grid),
`ui_telemetry` (the header's banner/macro bars), `ui_shopping` (the
right-hand slide-over), `ui_plan`/`ui_today`/`ui_catalog_browser`/
`ui_insights`/`ui_settings` (the five rail destinations), `ui_review` (the
staged-input dialog), `ui_staged_bar` (the persistent pending-changes strip),
`ui_generation` (everything that writes `week_plan.json`), `ui_catalog`
(favorites helpers shared by cards and the catalog browser) — each exposing
one `build_*(ctx)` factory that returns the refreshable functions/elements
other modules or this shell need. `planner_page()` now does four things:
build a `UIContext`, call each `build_*` in dependency order, lay out the
header, the staged-changes bar and the rail's action block (the three
regions with no natural module of their own — header because it stitches
together `ui_telemetry`'s pieces, the bar because it needs `ui_review` and
`ui_generation` both already built, the action block because it needs
handles from four), and register every returned refreshable into one topic
map (see `ui_context.Refreshables`) in place of a hand-written
`refresh_all()`.
"""

import os
from typing import Optional

from dotenv import load_dotenv
from nicegui import app as fastapi_app, ui

from api import build_api_router
from export_menu import build_week_menu_html, build_week_menu_pdf
from planner import WeekPlan, configure_logging
from repository import PROJECT_ROOT, LocalJSONRepository
from shopping import aggregate_cook_events
from ui_adherence import build_adherence
from ui_cards import build_cards
from ui_catalog import build_rename_dialog
from ui_catalog_browser import build_catalog_browser
from ui_context import Refreshables, UIContext
from ui_generation import build_generation
from ui_insights import build_insights
from ui_inspector import build_inspector
from ui_plan import build_plan
from ui_review import build_review
from ui_settings import build_settings
from ui_shopping import build_shopping
from ui_staged_bar import build_staged_bar
from ui_state import PlannerState
from ui_telemetry import build_telemetry
from ui_today import build_today
from ui_theme import (
    RADIUS_CARD,
    RAIL_WIDTH_CLASS,
    SPACE_BASE,
    SPACE_SECTION,
    SPACE_TIGHT,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
    WEEK_SELECTION_LABELS,
    card_hover_css,
    chain_css,
    week_grid_scroll,
)

# Explicit path — see the matching note in planner.py. NiceGUI's reloader can
# also start the process from a different directory than the one you typed in.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
configure_logging()

# One repository for the server, imported once rather than re-executed per
# interaction. It holds paths only, so pointing the app at a different backend
# stays a one-line change here. File names live on REPOSITORY.paths
# (repository.py's StoragePaths), not as a module constant here.
REPOSITORY = LocalJSONRepository()

# The read-only API boundary (phase 5 of ui-redesign.md) — mounted onto
# NiceGUI's own FastAPI app rather than a second server, since `nicegui.app`
# already *is* the `FastAPI` instance Uvicorn serves. Registered at module
# scope, before `ui.run()` runs, the same timing `@ui.page("/")` relies on.
fastapi_app.include_router(build_api_router(REPOSITORY))

# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------


@ui.page("/")
async def planner_page() -> None:
    state = await PlannerState.load(REPOSITORY)
    # Read once per page load for the Insights charts and the Settings
    # destination's sync-status dialog — nothing on this page writes
    # biometrics.json (the sync CLI does), so there's no reason to re-read it
    # on repaint, and both `build_*` factories are synchronous anyway.
    biometrics = await REPOSITORY.load_biometrics()
    refreshables = Refreshables()
    ctx = UIContext(state=state, repository=REPOSITORY, refreshables=refreshables)

    ui.dark_mode(True)
    ui.add_css(
        # Quasar's page container assumes comfortable padding; a 7-column week
        # needs the horizontal space back.
        ".nicegui-content { padding: 0.75rem; gap: 0.75rem; }\n"
        # Emitted once per page rather than from `canvas`, which is refreshable
        # and would stack another copy into the head on every repaint. The
        # bound is the week's shape, not its current contents, so it stays
        # valid however the grid is edited afterwards.
        + chain_css((len(state.days) * len(state.meal_types)) // 2)
        + "\n"
        + card_hover_css()
    )

    # Build order matters only where one module's factory needs another's
    # return value: generation before review (its "Generate" button starts a
    # run via generation.run_generation), generation before cards (a card's
    # regenerate icon calls into it), cards and review before plan (the Plan
    # destination's canvas is cards' own, and its own "Generate" button
    # opens the review dialog rather than running the week directly — phase
    # 6b moved that button out of the Plan panel into the rail, so `plan` no
    # longer takes `review` at all, but the rail block below still needs
    # both), cards
    # before today and before catalog_browser (both open cards' own recipe
    # detail dialog), cards and review before inspector (its slot cards open
    # cards' own recipe detail dialog, and its "Edit targets" link opens
    # review's dialog), inspector before telemetry (telemetry's day cell
    # wires the click that opens it), rename_dialog before catalog_browser
    # (its per-row edit icon opens the one shared dialog), review and
    # generation before staged_bar (its Review/Generate week/Discard actions
    # call straight into both), adherence before inspector and before today
    # (both draw its mark buttons on the same shared `today_card`/
    # `context_strip` renderers) — everything before the refresh-topic
    # registration below (every topic there names a section some `build_*`
    # returned).
    generation = build_generation(ctx)
    review = build_review(ctx, generation)
    cards = build_cards(ctx, generation)
    plan = build_plan(ctx, cards)
    adherence = build_adherence(ctx)
    inspector = build_inspector(ctx, cards, review, adherence)
    telemetry = build_telemetry(ctx, inspector)
    shopping = build_shopping(ctx)
    today = build_today(ctx, cards, adherence)
    rename_dialog = build_rename_dialog(ctx)
    catalog_browser = build_catalog_browser(ctx, cards, rename_dialog)
    settings = build_settings(ctx, biometrics)
    insights = build_insights(ctx, biometrics)
    staged_bar = build_staged_bar(ctx, review, generation)

    with ui.header(bordered=True).classes(f"bg-slate-900 px-{SPACE_SECTION} py-{SPACE_BASE} flex flex-col gap-{SPACE_BASE}"):
        with ui.element("div").classes(f"flex flex-row items-baseline gap-{SPACE_SECTION}"):
            with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_TIGHT}"):
                ui.icon("restaurant_menu").classes(f"{TEXT_HEAD} text-slate-300")
                ui.label("AI Weekly Meal Planner").classes(
                    f"{TEXT_HEAD} font-semibold tracking-wide"
                )

            async def on_week_selection_change(event) -> None:
                target = event.value
                if target == state.week_selection:
                    return
                # `switch_week` only reads from disk — it never generates —
                # so this is instant regardless of which week it's loading.
                await state.switch_week(REPOSITORY, target)
                refreshables.refresh("plan")

            # No `bind_value` here on purpose: binding would let NiceGUI's
            # polling loop write `state.week_selection` the moment the user
            # picks an option, before `switch_week` has loaded that week's
            # plan — every other piece of state (`week_plan`, `edited`, the
            # spec) would then disagree with `week_selection` until the
            # `await` above finishes. `switch_week` is the only thing that's
            # allowed to set it, and only once the load it names has landed.
            ui.select(
                WEEK_SELECTION_LABELS,
                value=state.week_selection,
                on_change=on_week_selection_change,
            ).props("dense outlined size=sm").classes("text-slate-200 w-32")

            ui.label().classes(f"{TEXT_BODY} text-slate-400").bind_text_from(
                state,
                "week_plan",
                backward=lambda plan: (
                    f"generated {plan.generated_at[:16].replace('T', ' ')}"
                    if plan
                    else "no cached week — showing planned shape only"
                ),
            )
        telemetry.week_banner()
        # `inset=True` pushes this past the rail so each day's telemetry sits
        # over that day's meals in the canvas below — the header is fixed and
        # full-width, the canvas lives inside a destination panel to the right
        # of the rail, and phase 3 left the two a full column out of step.
        with week_grid_scroll(inset=True):
            telemetry.telemetry()

    with ui.element("div").classes(f"w-full px-{SPACE_SECTION} pt-{SPACE_TIGHT}"):
        staged_bar.bar()

    # ---- refresh topics ------------------------------------------------------
    # Registered here, last, once every section named below actually exists —
    # each came back from a `build_*` call above. Every call site inside
    # those modules refers to a topic by string (`refreshables.refresh(
    # "plan")`, "targets", ...), so none of them needed this registration to
    # exist yet: nothing calls `.refresh()` until a user interaction fires,
    # long after this function has finished building.
    #
    # "plan" replaces what used to be a hand-written `refresh_all()` — every
    # section that reads the generated week, so a generation, a reload, a
    # leftover link, or a settings control that reshapes the week (week
    # start, servings) all repaint the same set. `telemetry` recurs across
    # several narrower topics (a plain macro edit, a training edit) because
    # it is the one section every kind of target change is visible in;
    # `review.targets_editor` is deliberately left out of "telemetry" alone —
    # see `ui_review.day_target_row`'s `sync()` — because rebuilding it
    # mid-edit would steal the input focus. `staged_bar.bar` rides on
    # "plan"/"targets"/"training"/"telemetry" rather than a topic of its own,
    # since `pending_changes()` reads nothing those four don't already cover
    # between them — "telemetry" is the one a bare keystroke in a target
    # input actually fires (see below), and the bar isn't the section that
    # trap is about, so it can safely be part of it.
    refreshables.on(
        "plan",
        telemetry.week_banner,
        telemetry.telemetry,
        cards.canvas,
        plan.week_failures,
        shopping.shopping_panel,
        review.targets_editor,
        review.training_editor,
        today.today_view,
        inspector.panel,
        staged_bar.bar,
        # Generation is what appends the `meal_history.json` entries the
        # Insights charts pair against `daily_actuals`, so a finished run
        # changes what this destination has to draw. It owns no input, so
        # the focus-theft trap that keeps other sections off "plan" does not
        # apply to it.
        insights.panel,
    )
    refreshables.on("today", today.today_view)
    # Marking a meal or a session changes exactly three sections, and none is
    # on the week grid: `"plan"` would additionally rebuild the 28-card
    # canvas, the telemetry header and the shopping panel on every click of a
    # tick, none of which draw a mark. The third member arrived with the
    # trend charts — Insights' adherence tiles are the only readout in the
    # app that counts marks rather than showing one — which is why this
    # comment no longer says the topic cannot grow one.
    refreshables.on("adherence", today.today_view, inspector.panel, insights.panel)
    # The inspector panel has no focused input (its targets are read-only —
    # editing lives in the review dialog), so unlike "telemetry" it's safe to
    # repaint on every kind of target/training change, not just the narrow
    # ones. Registered on its own topic too so `inspector.open()` can force a
    # repaint the moment it opens, independent of anything else changing.
    refreshables.on("inspector", inspector.panel)
    # `staged_bar.bar` rides on "telemetry" too, not just "targets"/
    # "training": `ui_review.day_target_row`'s `sync()` deliberately refreshes
    # only "telemetry" on a keystroke (refreshing "targets" would rebuild the
    # very section that owns the focused input — the focus-theft trap
    # the `ui-work` skill documents). The bar isn't part of that section,
    # so it can safely ride along without reintroducing the trap.
    refreshables.on("telemetry", telemetry.telemetry, staged_bar.bar)
    # `today_view` joins both of these because it reads the same live numbers
    # the header does — its calorie bar divides by `targets_for`, and its
    # context strip and per-card workout badges come from `day_context`, which
    # reads the review dialog's training schedule through `planning_config()`.
    # It is deliberately *not* in "telemetry": that topic exists to repaint
    # the header on every keystroke of a focused target input without
    # disturbing the dialog, and rebuilding four cards plus a
    # `planning_config()` per keystroke is the cost this narrow topic was
    # carved out to avoid.
    refreshables.on(
        "targets",
        review.targets_editor,
        telemetry.telemetry,
        today.today_view,
        inspector.panel,
        staged_bar.bar,
        # Reports the live figure per macro, so an override in the review
        # dialog changes what it says even though nothing in Settings moved.
        settings.targets_source,
    )
    # Its own topic as well, for the toggles' own repaint: switching a macro
    # to manual has to redraw *that section* (the per-day inputs appear) and
    # nothing else on this list needs a second pass for it.
    refreshables.on("settings", settings.targets_source)
    refreshables.on(
        "training",
        review.training_editor,
        telemetry.telemetry,
        review.targets_editor,
        today.today_view,
        inspector.panel,
        staged_bar.bar,
    )
    # Adding or removing a pantry row changes the count the staged bar shows
    # and nothing else on the page — typing *into* a row refreshes nothing at
    # all, which is what keeps the cursor in the field being edited.
    refreshables.on("pantry", review.pantry_editor, staged_bar.bar)
    refreshables.on("catalog", cards.canvas, catalog_browser.catalog_grid)
    refreshables.on("favorites", catalog_browser.catalog_grid)
    refreshables.on("catalog_browser", catalog_browser.catalog_grid)
    refreshables.on("shopping", shopping.shopping_panel)
    # `week_banner`, not `plan.week_failures`: phase 6b moved the
    # shopping-trip count out of the Plan row and into the header's
    # reporting strip, and that count is the only thing here that
    # repartitions when the shopping days change. What is left in the Plan
    # row (a failure list) does not depend on them at all.
    refreshables.on("shopping_days", telemetry.week_banner, shopping.shopping_panel)
    refreshables.on("swap_matches", cards.swap_matches)
    refreshables.on("swap_dialog", cards.swap_dialog_body)
    refreshables.on("recipe_detail", cards.recipe_detail)

    # ---- the rail's action block ---------------------------------------------
    # Phase 6b of `ui-redesign.md`. Every control that starts something now
    # lives in one strip: the five destinations answer "what am I looking
    # at", these answer "what do I want to do", and nothing else on the page
    # is clickable chrome. What moved here, and from where:
    #
    # - **Generate / Shuffle styles** from the Plan destination's own header
    #   row (`ui_plan.week_summary`, now gone). Plan needed its own Generate
    #   because the staged-changes bar hides whenever `pending_changes()` is
    #   empty — a fresh page load — and "how do I generate a week" must never
    #   have no answer on screen. A rail button satisfies that better than a
    #   panel one did: it is visible from all five destinations, not just
    #   from Plan. The bar's own "Generate week" shortcut is unaffected.
    # - **Print PDF / mobile export / shopping** from `ui.header()`. The
    #   header is `position: fixed` above the telemetry grid, so every
    #   control in it was competing for the strip the day columns need — the
    #   original diagnosis's finding 6 ("developer controls in the user's
    #   first screen") recurring for a different set of controls.
    #
    # Shopping is the one 6b argued should stay global rather than move into
    # Plan, because the drawer is read *against* the grid and Daily View and
    # the inspector show slots worth shopping against too. The rail *is*
    # global, so it keeps that property while still being in the one place a
    # click starts something.
    #
    # These render as ordinary children of `ui.tabs()`, between the two
    # groups of tabs. Quasar's QTabs puts its default slot inside a flex
    # `.q-tabs__content` (a column, in `vertical` mode) and only registers
    # actual QTab children with its model, so a plain div sits in the flow
    # without joining the selection — no second tabs element, and therefore
    # no second `ui.tab_panels` value to keep in sync with the first.
    #
    # `RAIL_WIDTH_CLASS` is pinned (see `ui_theme`), so nothing here may
    # measure wider than the rail: the header's copy of the week grid is
    # inset by exactly that many pixels to sit over the canvas, and a button
    # that widened the rail would slide every day's telemetry off its column.
    # Hence `TEXT_MICRO`, `align=left`, and `w-full` on every button rather
    # than an intrinsic width.
    def rail_button(icon: str, label: str, on_click, tooltip: str, classes: str, props: str):
        button = (
            ui.button(label, icon=icon, on_click=on_click)
            .props(f"no-caps align=left dense {props}")
            .classes(f"w-full {TEXT_MICRO} {classes}")
        )
        with button:
            ui.tooltip(tooltip)
        return button

    def download_pdf_menu() -> None:
        # One export path, not two: `window.print()` used to print whatever
        # the dashboard happened to look like (icons, drawers, macro bars —
        # none of it a recipe), which was a different, worse document than
        # the PDF download right next to it. `build_week_menu_pdf` reads the
        # same `state.week_plan` the grid shows, so it always matches
        # whatever edits (leftover links, regenerated days) are on screen —
        # print it from the browser's viewer or file it away as-is.
        if state.week_plan is None:
            ui.notify("Generate a week first — there's nothing to export yet.", type="warning")
            return
        ui.download(
            build_week_menu_pdf(state.week_plan),
            filename="weekly_menu.pdf",
            media_type="application/pdf",
        )

    def download_html_menu() -> None:
        # Same source as the PDF (`state.week_plan`), a different shape: one
        # scrolling page sized for a phone instead of pages meant for a
        # printer, with tap-to-strike steps for cooking from screen in hand
        # rather than a sheet on the counter.
        if state.week_plan is None:
            ui.notify("Generate a week first — there's nothing to export yet.", type="warning")
            return
        ui.download(
            build_week_menu_html(state.week_plan).encode("utf-8"),
            filename="weekly_menu.html",
            media_type="text/html",
        )

    def on_shuffle_styles() -> None:
        state.shuffle_styles()
        refreshables.refresh("plan")
        ui.notify(
            "Styles cleared — next Generate will re-roll every cook slot.",
            type="positive",
        )

    def shopping_item_count(plan: Optional[WeekPlan]) -> str:
        # Short enough for a 168px rail: "Shopping list (108 items)" is the
        # header's old wording and does not fit. The drawer it opens says
        # the rest.
        if plan is None:
            return "Shopping"
        items = aggregate_cook_events(plan.events_on_days(state.days), state.days).items()
        return f"Shopping ({len(items)})" if items else "Shopping"

    def rail_actions() -> None:
        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_TIGHT} w-full px-{SPACE_TIGHT} py-{SPACE_BASE} my-{SPACE_TIGHT} "
            "border-y border-slate-800"
        ):
            # Un-flat and colour-carrying on purpose — these two are the
            # week's primary verbs, and every tab around them is a flat
            # label. The three below stay flat and slate: an export is an
            # occasional control, and giving it the same weight is what made
            # the old header read as an undifferentiated row of icons.
            generate = rail_button(
                "bolt",
                "Generate",
                review.open,
                "Opens cuisine, diet-style, bulk-prep, long-cook, targets, "
                "training and pantry options, then generates every meal set "
                "to cook in this grid — one API call per meal type, covering "
                "each day it's cooked. Overwrites the selected week's cached "
                "plan and appends to history.",
                "font-semibold",
                "unelevated color=primary",
            )
            generate.bind_text_from(
                state,
                "week_selection",
                backward=lambda w: f"Generate {WEEK_SELECTION_LABELS[w].lower()}",
            )
            shopping_button = rail_button(
                "shopping_cart",
                "Shopping",
                shopping.shopping_drawer.toggle,
                "Every shopping trip in this week, grouped by department — "
                "built from the grid as it stands, including any edits.",
                "text-slate-900 font-semibold",
                "unelevated color=teal",
            )
            shopping_button.bind_text_from(
                state, "week_plan", backward=shopping_item_count
            )
            rail_button(
                "casino",
                "Shuffle styles",
                on_shuffle_styles,
                "Once a week is generated, its slots keep the style/cuisine "
                "they resolved to, so re-generating repeats them and only "
                "reworks the dish. This blanks style/cuisine on every cook "
                "slot (leftover links and skips are untouched) so the next "
                "Generate rotates them fresh — nothing is written to disk "
                "until you generate.",
                "text-slate-400",
                "flat",
            )
            rail_button(
                "print",
                "PDF menu",
                download_pdf_menu,
                "Download this week as a PDF — summary, every recipe, and "
                "the shopping list.",
                "text-slate-400",
                "flat",
            )
            rail_button(
                "smartphone",
                "Mobile page",
                download_html_menu,
                "Download this week as a mobile-friendly page — tap a recipe "
                "step or shopping item to check it off.",
                "text-slate-400",
                "flat",
            )

    # ---- rail + destinations --------------------------------------------------
    # Tab-panel style, not routed pages: every destination builds once and
    # stays mounted, hidden rather than torn down, so `PlannerState` is never
    # reconstructed by navigating away from Plan and back — the trap a routed
    # approach would have needed a per-connection store outside this function
    # to avoid. `ui.tabs().props("vertical")` is the same tabs/tab_panels
    # machinery the old Week/Today setup used, restyled as a rail; nothing
    # about how NiceGUI wires a tab strip to its panels changes with
    # orientation.
    #
    # "Today" is read-only on purpose — no favorite/swap/regenerate buttons,
    # no click-to-detail (see `ui_today`'s module docstring). `value=plan_tab`
    # keeps the week grid — everything this app has ever shown — the default
    # destination, so a page load looks exactly as it did before the rail
    # existed.
    with ui.row().classes("w-full flex-1 flex-nowrap items-stretch gap-0"):
        # A pinned width, not the intrinsic one: the Daily View tab's label is
        # whichever day is being browsed, so an auto-width rail would resize —
        # and shift the canvas sideways out from under the header's grid —
        # every time you stepped through the week. See `RAIL_WIDTH_CLASS`.
        with ui.tabs().props("vertical dense").classes(
            f"bg-slate-900 border-r border-slate-800 shrink-0 py-{SPACE_BASE} {RAIL_WIDTH_CLASS}"
        ) as rail:
            plan_tab = ui.tab("Plan", icon="calendar_view_week").props("no-caps")
            today_tab = ui.tab("Daily View", icon="today").props("no-caps")
            rail_actions()
            library_tab = ui.tab("Library", icon="menu_book").props("no-caps")
            insights_tab = ui.tab("Insights", icon="insights").props("no-caps")
            settings_tab = ui.tab("Settings", icon="settings").props("no-caps")
            # The label becomes the day being browsed ("Daily View · Sun 23 Aug",
            # or "Mon 24 Aug" once you step away). Injected rather than computed
            # here: `build_today` owns which day is on screen, and it ran well
            # before this tab existed.
            today.bind_tab(today_tab)

        with ui.tab_panels(rail, value=plan_tab).classes("w-full flex-1 bg-transparent p-0"):
            with ui.tab_panel(plan_tab).classes("p-0"):
                plan.panel()
            with ui.tab_panel(today_tab).classes("p-0"):
                today.today_view()
            with ui.tab_panel(library_tab).classes("p-0"):
                catalog_browser.panel()
            with ui.tab_panel(insights_tab).classes("p-0"):
                insights.panel()
            with ui.tab_panel(settings_tab).classes("p-0"):
                settings.panel()


if __name__ in {"__main__", "__mp_main__"}:
    # reload=False on purpose: once generation lands, an in-memory week plan
    # would be thrown away by every source-file save.
    ui.run(
        title="AI Weekly Meal Planner",
        # server.sh passes MEALS_UI_PORT so its MEALS_PORT override reaches
        # here; 8080 keeps `python ui_app.py` working on its own.
        port=int(os.environ.get("MEALS_UI_PORT", "8080")),
        dark=True,
        reload=False,
        show=False,
    )

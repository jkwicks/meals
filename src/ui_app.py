"""NiceGUI front end — the whole week on one high-density desktop screen.

The only web front end, and now a complete one: it can both generate a week and
rearrange it, so `python planner.py` is an alternative rather than a
prerequisite.

**Generating is the only thing here that writes to disk**, and it writes
everything at once — `week_plan.json` and a history entry per cooked day —
because a generated week that isn't saved is a 20-minute run one browser
refresh from being lost. Grid *edits* ("Link to next lunch") are still
in-memory only: they live in the client's `PlannerState` until "Reload from
disk" throws them away, and the header carries an "edited — not saved" chip
while any are outstanding. Generating clears that chip, because saving is what
it just did.

Three regions, mirroring how the week is actually read:

- **Left drawer** — the global knobs (week start, household size, shopping
  days, model), the per-day macro targets and the pantry list, plus the
  generation trigger. Everything that applies to the whole week rather than one
  meal. Target overrides and the pantry are *inputs to the next run*: they are
  held in `PlannerState`, merged into `planning_config()`, and never written
  back to config.json.
- **Header** — macro telemetry: one horizontal bar per day, in the *same*
  7-column grid as the canvas below, so a day's bar sits directly above its
  column of meals.
- **Canvas** — 7 day columns x 4 stacked meal cards, cook vs. leftover
  distinguished by colour, border and badge. Lives inside a "Week" tab
  (`ui.tabs`/`ui.tab_panels`) alongside a "Today" tab — a read-only preview
  of just today's four cards (`ui_today`), which needs `WeekPlan.
  week_start_date` to know whether the loaded week's dates actually cover
  today rather than just sharing its weekday names. See "This file is a
  page shell" below for why the header isn't split the same way.
- **Right drawer** — the shopping list, one section per trip, opened from the
  header. It is derived from the plan on every repaint, so it always describes
  the week as the grid currently stands rather than as it was generated.

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
`ui_telemetry` (the header's banner/pipeline/macro bars), `ui_shopping` (the
right-hand slide-over), `ui_drawer` (the left drawer), `ui_generation`
(everything that writes `week_plan.json`), `ui_catalog` (favorites helpers
shared by cards and drawer) — each exposing one `build_*(ctx)` factory that
returns the refreshable functions/elements other modules or this shell need.
`planner_page()` now does four things: build a `UIContext`, call each
`build_*` in dependency order, lay out the header (the one region with no
natural module of its own — it is pure page layout, stitching together
`ui_telemetry`'s and `ui_shopping`'s pieces), and register every returned
refreshable into one topic map (see `ui_context.Refreshables`) in place of a
hand-written `refresh_all()`.
"""

import os
from typing import Optional

from dotenv import load_dotenv
from nicegui import ui

from export_menu import build_week_menu_html, build_week_menu_pdf
from planner import WeekPlan, configure_logging
from repository import PROJECT_ROOT, LocalJSONRepository
from shopping import aggregate_cook_events
from ui_cards import build_cards
from ui_catalog import build_rename_dialog
from ui_catalog_browser import build_catalog_browser
from ui_context import Refreshables, UIContext
from ui_drawer import build_drawer
from ui_generation import build_generation
from ui_prep_options import build_prep_options
from ui_shopping import build_shopping
from ui_state import PlannerState
from ui_telemetry import build_telemetry
from ui_today import build_today
from ui_theme import (
    RADIUS_CARD,
    SPACE_BASE,
    SPACE_SECTION,
    SPACE_TIGHT,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
    WEEK_SELECTION_LABELS,
    card_hover_css,
    chain_css,
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

# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------


@ui.page("/")
async def planner_page() -> None:
    state = await PlannerState.load(REPOSITORY)
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
    # return value: generation before cards (a card's regenerate icon calls
    # into it), generation before prep_options (its "Generate" button starts
    # a run via generation.run_generation), prep_options before drawer (the
    # drawer's sticky Generate button opens this dialog rather than running
    # the week directly), cards before today and before catalog_browser (both
    # open cards' own recipe detail dialog), rename_dialog before drawer and
    # before catalog_browser (both offer a per-row edit icon into the one
    # shared dialog), catalog_browser before drawer (the drawer's "Browse
    # all" button opens it), everything before the refresh-topic registration
    # at the bottom (every topic there names a section some `build_*`
    # returned).
    generation = build_generation(ctx)
    prep_options = build_prep_options(ctx, generation)
    cards = build_cards(ctx, generation)
    telemetry = build_telemetry(ctx)
    shopping = build_shopping(ctx)
    today = build_today(ctx, cards)
    rename_dialog = build_rename_dialog(ctx)
    catalog_browser = build_catalog_browser(ctx, cards, rename_dialog)

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
            # Linking is an in-memory reshuffle: nothing here writes
            # week_plan.json, so say so rather than letting the grid imply the
            # cached week on disk has changed.
            ui.label("edited — not saved").classes(
                f"{TEXT_MICRO} font-semibold px-{SPACE_TIGHT} {RADIUS_CARD} bg-amber-400/15 text-amber-300"
            ).bind_visibility_from(state, "edited")
            ui.space()
            ui.label().classes(f"{TEXT_BODY} text-slate-400").bind_text_from(
                state, "model", backward=lambda model: f"model: {model}"
            )

            def shopping_item_count(plan: Optional[WeekPlan]) -> str:
                if plan is None:
                    return "Shopping list"
                items = aggregate_cook_events(
                    plan.events_on_days(state.days), state.days
                ).items()
                if not items:
                    return "Shopping list"
                return f"Shopping list ({len(items)} items)"

            # One export path, not two: `window.print()` used to print
            # whatever the dashboard happened to look like (icons, drawers,
            # macro bars — none of it a recipe), which was a different,
            # worse document than "Download PDF Menu" right next to it. Now
            # this button *is* that download — `build_week_menu_pdf` reads
            # the same `state.week_plan` the grid shows, so it always
            # matches whatever edits (leftover links, regenerated days) are
            # on screen right now, and it's the one PDF the app produces —
            # print it from the browser's viewer or file it away as-is.
            def download_pdf_menu() -> None:
                if state.week_plan is None:
                    ui.notify("Generate a week first — there's nothing to export yet.", type="warning")
                    return
                ui.download(
                    build_week_menu_pdf(state.week_plan),
                    filename="weekly_menu.pdf",
                    media_type="application/pdf",
                )

            print_button = ui.button(icon="print", on_click=download_pdf_menu).props(
                "dense flat no-caps"
            ).classes("text-slate-300")
            with print_button:
                ui.tooltip("Download this week as a PDF — summary, every recipe, and the shopping list.")

            # Same source as the PDF button (`state.week_plan`), a different
            # shape: one scrolling page sized for a phone instead of pages
            # meant for a printer, with tap-to-strike steps for cooking from
            # screen in hand rather than a sheet on the counter.
            def download_html_menu() -> None:
                if state.week_plan is None:
                    ui.notify("Generate a week first — there's nothing to export yet.", type="warning")
                    return
                ui.download(
                    build_week_menu_html(state.week_plan).encode("utf-8"),
                    filename="weekly_menu.html",
                    media_type="text/html",
                )

            mobile_button = ui.button(icon="smartphone", on_click=download_html_menu).props(
                "dense flat no-caps"
            ).classes("text-slate-300")
            with mobile_button:
                ui.tooltip(
                    "Download this week as a mobile-friendly page — tap a recipe step or "
                    "shopping item to check it off."
                )

            # Prominent and un-dense on purpose — this is the button that
            # gets used every single week, not an occasional control, so it
            # gets the same visual weight as "Generate" rather than blending
            # into the rest of the flat header icons.
            shopping_button = (
                ui.button(icon="shopping_cart", on_click=shopping.shopping_drawer.toggle)
                .props("no-caps unelevated color=teal")
                .classes("text-slate-900 font-semibold shadow-md shadow-teal-500/20")
            )
            shopping_button.bind_text_from(
                state, "week_plan", backward=shopping_item_count
            )
            with shopping_button:
                ui.tooltip(
                    "Every shopping trip in this week, grouped by department — "
                    "built from the grid as it stands, including any edits."
                )
        telemetry.week_banner()
        telemetry.context_pipeline()
        telemetry.telemetry()

    drawer = build_drawer(ctx, generation, prep_options, rename_dialog, catalog_browser)

    # ---- refresh topics ----------------------------------------------------
    # Registered here, last, once every section named below actually exists —
    # each came back from a `build_*` call above. Every call site inside
    # those modules refers to a topic by string (`refreshables.refresh(
    # "plan")`, "targets", ...), so none of them needed this registration to
    # exist yet: nothing calls `.refresh()` until a user interaction fires,
    # long after this function has finished building.
    #
    # "plan" replaces what used to be a hand-written `refresh_all()` — every
    # section that reads the generated week, so a generation, a reload, a
    # leftover link, or a drawer control that reshapes the week (week start,
    # servings) all repaint the same set. `telemetry` recurs across several
    # narrower topics (a plain macro edit, a training edit) because it is the
    # one section every kind of target change is visible in; `targets_editor`
    # is deliberately left out of "telemetry" alone — see `ui_drawer.
    # day_target_row`'s `sync()` — because rebuilding it mid-edit would steal
    # the input focus.
    refreshables.on(
        "plan",
        telemetry.week_banner,
        telemetry.telemetry,
        cards.canvas,
        drawer.week_summary,
        shopping.shopping_panel,
        drawer.targets_editor,
        drawer.training_editor,
        today.today_view,
    )
    refreshables.on("today", today.today_view)
    refreshables.on("telemetry", telemetry.telemetry)
    # `today_view` joins both of these because it reads the same live numbers
    # the header does — its calorie bar divides by `targets_for`, and its
    # context strip and per-card workout badges come from `day_context`, which
    # reads the drawer's training schedule through `planning_config()`. It is
    # deliberately *not* in "telemetry": that topic exists to repaint the
    # header on every keystroke of a focused target input without disturbing
    # the drawer, and rebuilding four cards plus a `planning_config()` per
    # keystroke is the cost this narrow topic was carved out to avoid.
    refreshables.on("targets", drawer.targets_editor, telemetry.telemetry, today.today_view)
    refreshables.on(
        "training",
        drawer.training_editor,
        telemetry.telemetry,
        drawer.targets_editor,
        today.today_view,
    )
    refreshables.on("catalog", drawer.favorites_list, cards.canvas, catalog_browser.catalog_grid)
    refreshables.on("favorites", drawer.favorites_list, catalog_browser.catalog_grid)
    refreshables.on("catalog_browser", catalog_browser.catalog_grid)
    refreshables.on("shopping", shopping.shopping_panel)
    refreshables.on("shopping_days", drawer.week_summary, shopping.shopping_panel)
    refreshables.on("swap_matches", cards.swap_matches)
    refreshables.on("swap_dialog", cards.swap_dialog_body)
    refreshables.on("pipeline_detail", telemetry.pipeline_detail)
    refreshables.on("recipe_detail", cards.recipe_detail)
    # Registered like every other section for consistency, though nothing
    # refreshes it today — no drawer control changes what it shows yet
    # (see `ui_state.pipeline_value`: only "workout" is wired up).
    refreshables.on("context_pipeline", telemetry.context_pipeline)

    # ---- tabs ---------------------------------------------------------------
    # The header (week selector, banner, context pipeline, telemetry) stays
    # shared chrome above both tabs — it's weekly macro data either view would
    # want, and splitting it apart is a call for whichever tab actually needs
    # to make it, not this one. Only the main content area — previously just
    # a bare `cards.canvas()` call — is tab-scoped.
    #
    # "Today" is read-only on purpose — no favorite/swap/regenerate buttons,
    # no click-to-detail (see `ui_today`'s module docstring). `value=week_tab`
    # keeps the week grid — everything this app has ever shown — the
    # default, so a page load looks exactly as it did before tabs existed.
    with ui.tabs().classes("w-full") as tabs:
        week_tab = ui.tab("Week", icon="calendar_view_week")
        today_tab = ui.tab("Today", icon="today")
        # The label becomes the day being browsed ("Today · Sun 23 Aug", or
        # "Mon 24 Aug" once you step away). Injected rather than computed
        # here: `build_today` owns which day is on screen, and it ran well
        # before this tab existed.
        today.bind_tab(today_tab)

    with ui.tab_panels(tabs, value=week_tab).classes("w-full bg-transparent p-0"):
        with ui.tab_panel(week_tab).classes("p-0"):
            cards.canvas()
        with ui.tab_panel(today_tab).classes("p-0"):
            today.today_view()


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

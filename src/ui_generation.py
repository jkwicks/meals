"""Everything that can change `state.week_plan` from disk: a full generation,
a reload, or the two narrower single-day/single-meal retries.

`build_generation(ctx)` is called once per page load, before the drawer that
holds the "Generate"/"Reload from disk" buttons and before the canvas whose
cards hold the per-day/per-meal regenerate icons — both of those need the
functions this returns. `run_generation`, `regenerate_day` and
`regenerate_meal` each take the clicked button as a parameter (rather than
closing over a `generate`/`regen_button` name the way the pre-split
monolithic page function did) precisely because that button is now built by
a different module, after this one.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Dict, Optional, Tuple

from nicegui import ui

from planner import (
    REJECTION_REASON_LABELS,
    RejectionEntry,
    api_key_error,
    generate_week_plan,
    meal_type_order,
    planning_rule,
    record_week_history,
    regenerate_single_day,
    regenerate_single_meal,
    resolve_auto_choices,
    short_error,
)
from ui_context import UIContext
from ui_state import (
    GENERATION_STAGE_BANKED,
    GENERATION_STAGE_PENDING,
    GENERATION_STAGE_RUNNING,
    SlotView,
    generation_stage_views,
)
from ui_theme import (
    RADIUS_CARD,
    RADIUS_PANEL,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_TIGHT,
    SURFACE_PANEL,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
    WEEK_SELECTION_LABELS,
    pluralize,
)
from week import (
    DEFAULT_INVENTORY_RULES,
    MODE_COOK,
    WeekSpec,
    clear_batch_links,
    clear_cuisines,
    clear_recipe_pins,
    clear_styles,
    humanize,
    slot_label,
    spread_batch,
    validate_week,
)


def apply_batch_selections(spec: WeekSpec, config: dict) -> Tuple[WeekSpec, dict]:
    """Turn the popup's bulk-prep/long-cook toggles into leftover links.

    **Each batch takes one meal type, straight across the front of the week.**
    Bulk prep claims the lunches, long cook claims the dinners, both starting
    at day 1 and running as far as the fridge window allows — so with the
    shipped config, Monday-Wednesday lunches are all one prepped dish and
    Monday-Wednesday dinners are all another. Nothing is searched for and
    nothing competes: the two batches cannot collide because they are on
    different rows of the grid, and neither can drift late because both start
    at the earliest day there is.

    That pairing is not arbitrary. A soup/stew/curry (`BULK_PREP_RULE`'s own
    candidates) is exactly the dish that reheats at a desk and travels in a
    container, and an oven roast or braise (`BATCH_ROAST_ANCHOR_RULE`'s) is
    dinner food. It also means Monday eats two *different* dishes rather than
    the same one twice, which is what any arrangement filling all six slots
    from a single row would have forced.

    **The anchor is bookkeeping, not a choice.** Every recipe has to live on
    some slot — that is what a cook slot is — and prep day has no slot of its
    own in the grid, so the first day a batch is eaten holds the recipe and
    the rest point back at it. Earlier versions *searched* for that day
    (earliest dinner with room, weekends preferred, second toggle excluding
    the first's day), and the search was the entire source of both the
    late-week drift and the two toggles fighting over the same dinners. Day 1
    is always a valid anchor and always the safest one, so there is nothing
    left to search for.

    Returns the (possibly updated) spec and a dict of the two anchor slot ids
    actually chosen (None where a toggle was off, or where `spread_batch`
    could not grow that anchor past what an ordinary meal already gets — see
    its docstring) — merge straight into `config` so
    `generate_meal_type_week`/`generate_sunday_prep_session` can read
    `long_cook_anchor`/`bulk_prep_anchor` off it. A grid whose lunches or
    dinners are already claimed by the user leaves the corresponding batch
    with nothing to grow into; `generate_week` below warns rather than
    generating a mislabeled meal silently.
    """
    target_servings = planning_rule(config, "batch_target_servings")
    fridge_safe_days = (config.get("inventory_rules") or DEFAULT_INVENTORY_RULES).get(
        "fridge_safe_days", DEFAULT_INVENTORY_RULES["fridge_safe_days"]
    )
    # These batches are cooked on prep day — the day *before* `spec.days[0]`,
    # the eighth column `ui_cards.prep_day_column` draws — not on the day
    # their anchor slot happens to sit. So the bound that matters is measured
    # from there: day index i is i+1 days out of the fridge, giving indices
    # 0..fridge_safe_days-1. `max_span_days` (anchor-relative) is deliberately
    # not passed as well; it can only ever be looser than this one here, since
    # every anchor is day 0.
    max_day_index = fridge_safe_days - 1
    anchors: Dict[str, Optional[str]] = {"long_cook_anchor": None, "bulk_prep_anchor": None}

    if config.get("bulk_prep_enabled"):
        spec, anchors["bulk_prep_anchor"] = spread_batch(
            spec, "lunch", target_servings, max_day_index=max_day_index
        )

    if config.get("long_cook_enabled"):
        spec, anchors["long_cook_anchor"] = spread_batch(
            spec, "dinner", target_servings, max_day_index=max_day_index
        )

    return spec, anchors


@dataclass
class GenerationHandles:
    run_generation: Callable
    reload_from_disk: Callable
    regenerate_day: Callable
    regenerate_meal: Callable
    save_grid: Callable
    offer_rejection_prompt: Callable


def build_generation(ctx: UIContext) -> GenerationHandles:
    state = ctx.state
    REPOSITORY = ctx.repository
    refreshables = ctx.refreshables

    # ---- reload -------------------------------------------------------

    async def reload_from_disk() -> None:
        await state.reload_plan(REPOSITORY)
        refreshables.refresh("plan")
        label = WEEK_SELECTION_LABELS[state.week_selection]
        ui.notify(
            f"Reloaded {label}" if state.week_plan else f"No cached plan for {label}",
            type="positive" if state.week_plan else "warning",
        )

    # ---- generation ---------------------------------------------------
    # The run is long (30s-3min per meal type) and its progress is the only
    # thing on screen worth looking at while it happens, so it gets a modal
    # rather than a toast. Built once per page: opening it is a state change,
    # not a construction, so the day-by-day updates below can just assign to
    # these elements from the progress callbacks.

    # The per-stage checklist. Everything it needs was already arriving on the
    # loop: `on_meal_type` fires once per meal type, before its call, with
    # that stage's cook count — only the rendering was missing.
    # `ui_state.generation_stage_views` holds the one piece of it worth
    # testing (which stage is banked, and why that is off by one from what
    # the callback counts); this end is widget construction, per the
    # `ui-work` skill's line on where logic goes.
    #
    # `_stage_progress` is closure-local for the same reason
    # `_pending_rejection` below is: presentation-only scratch state for one
    # widget, read by nothing else and by no refresh topic. `done` is what
    # that function calls `started`.
    _stage_progress: dict = {"order": [], "done": 0, "cooks": {}, "complete": False}

    # Glyph, never hue: the palette contract caps every colour at two
    # meanings and emerald — the obvious tick — is already the cook status,
    # so a green check in front of a meal type would read as a slot state
    # rather than as progress. Same reasoning `ADHERENCE_MARK_ICONS` records
    # for the three marks on a card.
    STAGE_ICONS = {
        GENERATION_STAGE_BANKED: "check_circle",
        GENERATION_STAGE_RUNNING: "autorenew",
        GENERATION_STAGE_PENDING: "radio_button_unchecked",
    }

    @ui.refreshable
    def stage_checklist() -> None:
        stage_views = generation_stage_views(
            _stage_progress["order"],
            _stage_progress["done"],
            _stage_progress["cooks"],
            _stage_progress["complete"],
        )
        if not stage_views:
            return
        with ui.element("div").classes(f"flex flex-col gap-{SPACE_HAIR} w-full"):
            for stage in stage_views:
                tone = (
                    "text-slate-400"
                    if stage.state == GENERATION_STAGE_PENDING
                    else "text-slate-200"
                )
                with ui.element("div").classes(
                    f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT} min-w-0"
                ):
                    ui.icon(STAGE_ICONS[stage.state]).classes(f"{TEXT_BODY} shrink-0 {tone}")
                    ui.label(stage.label).classes(f"{TEXT_MICRO} min-w-0 truncate {tone}")
                    if stage.detail:
                        ui.label(stage.detail).classes(f"{TEXT_MICRO} shrink-0 text-slate-400")

    with ui.dialog().props("persistent") as progress_dialog:
        with ui.element("div").classes(
            f"{SURFACE_PANEL} {RADIUS_PANEL} p-{SPACE_PAGE} w-[32rem] max-w-full flex flex-col gap-{SPACE_BASE}"
        ):
            with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_TIGHT}"):
                ui.icon("bolt").classes(f"{TEXT_HEAD} text-slate-300")
                ui.label("Generating week").classes(f"{TEXT_HEAD} font-semibold")
            progress_status = ui.label("Starting…").classes(f"{TEXT_BODY} text-slate-300")
            progress_bar = ui.linear_progress(value=0.0, size="10px", show_value=False).props(
                "rounded color=primary"
            )
            stage_checklist()
            ui.label(
                "One API call per meal type, 30s–3 min each. This window stays "
                "until the whole week is done."
            ).classes(f"{TEXT_MICRO} text-slate-400")
            # Portion trims and failed days both arrive as notes, mid-run. A log
            # keeps them all — a single status label would overwrite the trim
            # you wanted to read with the next day's heading.
            progress_log = ui.log(max_lines=200).classes(
                f"w-full h-40 {TEXT_MICRO} bg-slate-950 {RADIUS_CARD}"
            )

    # ---- rejection capture ---------------------------------------------
    # See CLAUDE.md's "Rejection capture" — the point of this phase.
    # Discarding a recipe — whether by hitting regenerate or by swapping in a
    # favorite — is thrown away today; this is what asks why, without getting
    # in the way of the retry/swap that already ran. `offer_rejection_prompt`
    # is exposed on `GenerationHandles` (rather than staying regenerate_meal's
    # private helper) so ui_cards.py's swap handler can raise the same
    # prompt for the same reason: a swap is exactly as deliberate a "no" to
    # the old recipe as the regenerate icon is, and was silently exempt from
    # capture before.
    #
    # A plain `fixed`-positioned div, not `ui.notify`/`ui.dialog`: NiceGUI's
    # `ui.notify` only ever forwards its `actions` to Quasar as serialized
    # JSON, so a Python `on_click` handler on one of its buttons has nothing
    # to bind to — checked directly against this app's installed NiceGUI
    # (3.16) before writing this. `ui.dialog` is a real container but is
    # modal (a dimmed backdrop), which is the opposite of "appears alongside
    # the retry, never in front of it". A `fixed` div is a real NiceGUI
    # element tree, so its buttons are ordinary `on_click` callbacks, and it
    # visually floats regardless of where in the page it's built — the same
    # reason `ui.header`'s own fixed positioning works regardless of DOM
    # nesting.
    #
    # `_pending_rejection` is closure-local, not a `PlannerState` field: it
    # is presentation-only scratch state for one widget (which recipe this
    # prompt is currently about), never read by another module or refresh
    # topic, so it doesn't belong on the one UI object the `ui-work` skill
    # says is tested.
    _pending_rejection: Dict[str, str] = {}

    @ui.refreshable
    def rejection_prompt() -> None:
        if not _pending_rejection:
            return
        with ui.element("div").classes(
            f"fixed bottom-4 right-4 z-50 flex flex-row flex-wrap items-center gap-{SPACE_TIGHT} "
            f"p-{SPACE_TIGHT} {RADIUS_CARD} border border-slate-700 bg-slate-900 shadow-lg max-w-sm"
        ):
            ui.label(f"Why replace \"{_pending_rejection['recipe_name']}\"?").classes(
                f"{TEXT_MICRO} text-slate-400"
            )
            for reason, label in REJECTION_REASON_LABELS.items():
                ui.button(
                    label, on_click=lambda r=reason: record_rejection(r)
                ).props("dense flat no-caps size=sm").classes("text-slate-300")
            ui.button(icon="close", on_click=dismiss_rejection_prompt).props(
                "dense flat size=xs"
            ).classes("min-h-0 p-0 text-slate-400")

    def dismiss_rejection_prompt() -> None:
        # No action = no record — an ignored prompt must not silently log a
        # default reason.
        _pending_rejection.clear()
        rejection_prompt.refresh()

    async def record_rejection(reason: str) -> None:
        if not _pending_rejection:
            return
        entry = RejectionEntry(
            date=date.today().isoformat(),
            slot_id=_pending_rejection["slot_id"],
            recipe_name=_pending_rejection["recipe_name"],
            reason=reason,
            marked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        await REPOSITORY.save_rejection_entry(entry.model_dump())
        dismiss_rejection_prompt()
        ui.notify("Noted — thanks", type="info", timeout=2000)

    def offer_rejection_prompt(slot_id_value: str, recipe_name: str) -> None:
        _pending_rejection["slot_id"] = slot_id_value
        _pending_rejection["recipe_name"] = recipe_name
        rejection_prompt.refresh()

    rejection_prompt()

    def generation_spec() -> WeekSpec:
        """The week we're about to ask for: what's on screen, with live servings.

        `state.spec` deliberately ignores the drawer's people-per-meal once a
        week is generated (that plan's portions came from its own
        `servings_per_meal`), so the value is reapplied here — otherwise the
        control would silently do nothing on every run after the first.
        """
        return state.spec.model_copy(update={"servings_per_meal": max(1, int(state.servings or 1))})

    async def generate_week(button) -> None:
        # The drawer wins over the file throughout: the model, the per-day
        # target overrides and the pantry list are the ones the user can
        # actually see, and this run is what they were entered for.
        config = state.planning_config()
        key_error = api_key_error()
        if key_error:
            # Checked up front rather than left to fail per-day: it would fail
            # all seven identically, after a wait, with nothing to show for it.
            ui.notify(key_error, type="negative", close_button=True, timeout=0)
            return

        spec = generation_spec()
        # A full-week generation always starts from a clean slate: style and
        # cuisine are blanked on every cook slot so resolve_auto_choices below
        # re-rolls the whole week fresh, rather than repeating whatever a
        # previous run on this same grid happened to resolve — the sticky
        # behaviour `PlannerState.shuffle_styles` otherwise leaves in place.
        # This is what makes a schedule change (e.g. a training session
        # moving into the pinned-breakfast window) actually take effect on
        # the next Generate, instead of being silently blocked by a slot that
        # already carries a concrete style from before the change. It also
        # covers the case where the popup's cuisine picker narrows
        # config["cuisines"] out from under a slot's previous concrete pick.
        # A *user's* mode changes, leftover links and skips are untouched —
        # those are structural edits they made on purpose, not picks due for
        # a re-roll.
        #
        # `clear_recipe_pins` for the same reason: a pinned favourite from the
        # previous run would still be sitting on its slot, and
        # `select_favorite_assignments` only ever fills an *empty* one — so
        # without this, week one's favourites would be re-served every week
        # forever and the reuse window would never get a chance to advance.
        #
        # `clear_batch_links` is the same rule applied to the one kind of
        # leftover link the user did *not* make: `spread_batch`'s own. It only
        # ever adds claims, counting what an anchor already has, so its
        # previous output would satisfy this run's target — linking nothing,
        # re-picking the same anchor, and freezing the batch shape and its
        # day permanently. `SlotSpec.link_origin` is what tells the kinds
        # of link apart.
        spec = clear_batch_links(clear_recipe_pins(clear_cuisines(clear_styles(spec))))
        # Bulk-prep/long-cook are fully-automatic leftover links, applied
        # before validate_week (so that single pass checks the grid actually
        # being generated from) and before resolve_auto_choices below (so it
        # never wastes a style/cuisine pick on a slot about to become a
        # leftover). Anchors ride on config rather than a new
        # generate_week_plan parameter — see apply_batch_selections.
        spec, batch_anchors = apply_batch_selections(spec, config)
        config = dict(config, **batch_anchors)

        # apply_batch_selections returns None for a toggle that requested a
        # batch but never found room to grow one — most often because every
        # dinner on the grid already feeds the next day's lunch, leaving
        # nothing for a second batch to claim once the first has run. Surface
        # that now, not as a dinner card that quietly never says "bulk prep"
        # on it three weeks from now.
        stranded = [
            label
            for enabled_key, anchor_key, label in (
                ("long_cook_enabled", "long_cook_anchor", "Long cook"),
                ("bulk_prep_enabled", "bulk_prep_anchor", "Bulk prep"),
            )
            if config.get(enabled_key) and not batch_anchors.get(anchor_key)
        ]
        if stranded:
            ui.notify(
                f"{' and '.join(stranded)} couldn't find a day with room to grow this run "
                "— every dinner may already be linked to the next day's lunch. "
                "Unlink one, or turn off one of the two toggles, to give it room.",
                type="warning",
                multi_line=True,
                close_button=True,
                timeout=0,
            )

        errors = validate_week(spec, config)
        if errors:
            ui.notify(
                "Can't generate — " + " ".join(errors[:3]),
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return

        history = await REPOSITORY.load_history()
        # Resolve auto styles/cuisines before the run so the recorded plan says
        # what it actually cooked, and so rotation continues across the week.
        spec = resolve_auto_choices(spec, config, history)

        stages = meal_type_order(config)
        cooking_days = len({slot.day for slot in spec.cook_slots()})
        done = 0

        def on_meal_type(meal_type: str, cooks: int) -> None:
            """Fired on the loop by generate_week_plan, once per meal type, before its call."""
            nonlocal done
            done += 1
            progress_bar.value = (done - 1) / len(stages)
            label = humanize(pluralize(meal_type)).capitalize()
            progress_status.text = (
                f"Generating {label} ({done}/{len(stages)}) — {cooks} recipe(s)…"
                if cooks
                else f"{label} ({done}/{len(stages)}) — nothing to cook, all leftovers or skipped"
            )
            # Safe to touch NiceGUI elements from here: `progress_callback`
            # never crosses the thread boundary (unlike `note_callback`, which
            # `on_calling_loop` has to re-schedule) — `generate_week_plan`
            # fires it on the loop, between stages.
            _stage_progress["done"] = done
            _stage_progress["cooks"][meal_type] = cooks
            stage_checklist.refresh()

        button.props("loading")
        _stage_progress["order"] = stages
        _stage_progress["done"] = 0
        _stage_progress["cooks"] = {}
        _stage_progress["complete"] = False
        stage_checklist.refresh()
        progress_status.text = (
            f"Starting {len(stages)} meal type(s) across {cooking_days} cooking day(s) "
            f"on {state.model}…"
        )
        progress_bar.value = 0.0
        progress_log.clear()
        progress_dialog.open()

        try:
            week_plan = await generate_week_plan(
                spec,
                config,
                history,
                progress_callback=on_meal_type,
                note_callback=progress_log.push,
                repository=REPOSITORY,
            )
            progress_status.text = "Saving…"
            progress_bar.value = 1.0
            # The last stage only becomes banked here: `on_meal_type` fires
            # *before* each call, so nothing else ever ticks the final one.
            _stage_progress["complete"] = True
            stage_checklist.refresh()
            # Targets whichever week is selected in the header — generating
            # while "Next Week" is showing must not overwrite "current".
            await REPOSITORY.save_week_plan(week_plan.model_dump(), state.week_selection)
            await record_week_history(week_plan, REPOSITORY, config)
            # `meal_history.json` just grew, and the Library table's
            # "Last eaten" column is the only reader of it held on state.
            # Re-read here rather than at the next page load, for the same
            # reason `recipe_catalog` is kept in sync by every handler that
            # mutates it: the copy on state is the one the UI believes.
            state.history = await REPOSITORY.load_history()
        except Exception as exc:
            # Per-day failures never reach here — generate_week_plan absorbs
            # those into WeekPlan.failures. This is the whole run coming apart
            # (no config, storage unwritable), so nothing is adopted and the
            # week on screen is left exactly as it was.
            ui.notify(
                f"Generation failed: {short_error(exc)}",
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return

        # Saved before adopted, so the grid can't show a week that isn't on
        # disk — `edited` clearing is a claim that they match.
        state.adopt_plan(week_plan)
        # "catalog" as well as "plan": the run just appended to history, so
        # the Library table's "Last eaten" column is stale until it repaints.
        refreshables.refresh("plan", "catalog")

        if week_plan.failures:
            ui.notify(
                f"{len(week_plan.failures)} meal(s) failed to generate — "
                "they show as NOT GENERATED. "
                + " · ".join(
                    f"{slot_label(key)}: {error}" for key, error in week_plan.failures.items()
                ),
                type="warning",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
        else:
            ui.notify(
                f"Generated {cooking_days} cooking day(s) and saved "
                f"{WEEK_SELECTION_LABELS[state.week_selection]}",
                type="positive",
            )

    async def run_generation(button) -> None:
        if state.generating:
            return
        # Claimed before the first `await`, not just before the API calls:
        # every await below is a point where a second click gets its turn, and
        # the guard is worthless if it can be passed twice in between.
        state.generating = True
        try:
            await generate_week(button)
        finally:
            state.generating = False
            button.props(remove="loading")
            progress_dialog.close()

    # ---- save without generating ----------------------------------------

    async def save_grid(button) -> None:
        """Write the grid exactly as it stands, with no model call.

        A favorite swap, a leftover link/unlink or a skip estimate are all
        deterministic edits — `state.week_plan` already reflects them the
        instant they're clicked (`PlannerState.swap_slot_with_favorite` and
        siblings) — so nothing about them needs the LLM in the loop. Before
        this, the *only* way to make one stick was `run_generation`, which
        pays for a full multi-minute re-plan of every meal type just to
        write down an edit that was already fully decided.

        `state.week_plan`'s object identity is unchanged here — this isn't
        adopting a new plan, just persisting the one already on screen — so
        it deliberately does not call `state.adopt_plan`: that method's own
        job is discarding unsaved edits (it resets `_spec`), the opposite of
        what a same-plan persist is doing. Setting `edited` False directly is
        the whole of what's needed to clear the staged-changes bar's "grid
        edited" line.

        Deliberately does **not** call `record_week_history` — that records
        what the *model* chose, for next week's rotation, and a grid edit
        makes no new choice for it to remember; the favorite/leftover it
        wrote down already came from history-aware selection.
        """
        if state.week_plan is None or not state.edited:
            return
        button.props("loading")
        try:
            await REPOSITORY.save_week_plan(state.week_plan.model_dump(), state.week_selection)
        except Exception as exc:
            ui.notify(
                f"Save failed: {short_error(exc)}",
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return
        finally:
            button.props(remove="loading")
        state.edited = False
        refreshables.refresh("plan")
        ui.notify(f"Saved {WEEK_SELECTION_LABELS[state.week_selection]}", type="positive")

    # ---- narrower retries -----------------------------------------------

    async def regenerate_day(day: str, button) -> None:
        """Re-cook one day's meals in place, via the canvas header's refresh icon.

        Deliberately lighter than `generate_week`: no progress modal, just the
        clicked button's own `loading` prop, because one day is a single API
        call rather than up to seven. Mutual exclusion with a whole-week run
        (and with a second regenerate click) is `state.generating` /
        `state.regenerating_day` — checked but never surfaced with a toast,
        same as `run_generation`'s own re-entry guard.
        """
        if state.generating or state.regenerating_day:
            return
        spec = state.spec
        if state.week_plan is None or not spec.cook_slots_on(day):
            return

        config = state.planning_config()
        key_error = api_key_error()
        if key_error:
            ui.notify(key_error, type="negative", close_button=True, timeout=0)
            return

        state.regenerating_day = day
        button.props("loading disable")
        try:
            history = await REPOSITORY.load_history()
            # Resolves only what's still `auto` on this day (e.g. after
            # "Shuffle styles") — every other day's already-concrete
            # style/cuisine is left exactly as it was.
            resolved_spec = resolve_auto_choices(spec, config, history)
            plan = await regenerate_single_day(
                day,
                resolved_spec,
                config,
                state.week_plan,
                history,
                repository=REPOSITORY,
            )
            await REPOSITORY.save_week_plan(plan.model_dump(), state.week_selection)
            await record_week_history(plan, REPOSITORY, config, days=[day])
            # `meal_history.json` just grew, and the Library table's
            # "Last eaten" column is the only reader of it held on state.
            # Re-read here rather than at the next page load, for the same
            # reason `recipe_catalog` is kept in sync by every handler that
            # mutates it: the copy on state is the one the UI believes.
            state.history = await REPOSITORY.load_history()
        except Exception as exc:
            ui.notify(
                f"Regenerating {day} failed: {short_error(exc)}",
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return
        finally:
            state.regenerating_day = None
            button.props(remove="loading disable")

        # Saved before adopted, same ordering as a full generation — the grid
        # can't show a day that isn't on disk.
        state.adopt_plan(plan)
        # "catalog" as well as "plan": the run just appended to history, so
        # the Library table's "Last eaten" column is stale until it repaints.
        refreshables.refresh("plan", "catalog")

        # regenerate_single_day writes one failures entry per cook slot on
        # `day` (see planner.py) — any of them present means the whole call
        # failed, since it's still one atomic API call for the day.
        day_failures = {key: error for key, error in plan.failures.items() if key.startswith(f"{day}:")}
        if day_failures:
            error = next(iter(day_failures.values()))
            ui.notify(
                f"{day} failed to regenerate — {error}",
                type="warning",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
        else:
            ui.notify(f"Regenerated {day}", type="positive")

    async def regenerate_meal(view: SlotView, button) -> None:
        """Re-cook one meal in place, via the small refresh icon on its card.

        Narrower still than `regenerate_day`: one API call for one slot
        rather than every cook on the day, via `planner.regenerate_single_meal`
        (siblings on the same day are treated as fixed and their macros
        subtracted from the day's budget). Unlike `regenerate_single_day`,
        that function doesn't catch its own exceptions into
        `WeekPlan.failures` — this is a single targeted retry, not a day walk
        — so the `try` here is what turns a raised exception into a toast
        instead of an unhandled error.
        """
        if state.generating or state.regenerating_day or state.regenerating_meal:
            return
        if view.mode != MODE_COOK or state.week_plan is None:
            return
        spec = state.spec
        day = view.day
        target_slot_id = view.id

        config = state.planning_config()
        key_error = api_key_error()
        if key_error:
            ui.notify(key_error, type="negative", close_button=True, timeout=0)
            return

        state.regenerating_meal = target_slot_id
        button.props("loading disable")
        try:
            history = await REPOSITORY.load_history()
            # Resolves only what's still `auto` on this day, same reasoning
            # as `regenerate_day` — every other day's already-concrete
            # style/cuisine is left exactly as it was.
            resolved_spec = resolve_auto_choices(spec, config, history)
            plan = await regenerate_single_meal(
                target_slot_id,
                resolved_spec,
                config,
                state.week_plan,
                history,
                repository=REPOSITORY,
            )
            await REPOSITORY.save_week_plan(plan.model_dump(), state.week_selection)
            await record_week_history(plan, REPOSITORY, config, days=[day])
            # `meal_history.json` just grew, and the Library table's
            # "Last eaten" column is the only reader of it held on state.
            # Re-read here rather than at the next page load, for the same
            # reason `recipe_catalog` is kept in sync by every handler that
            # mutates it: the copy on state is the one the UI believes.
            state.history = await REPOSITORY.load_history()
        except Exception as exc:
            ui.notify(
                f"Regenerating {slot_label(target_slot_id)} failed: {short_error(exc)}",
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return
        finally:
            state.regenerating_meal = None
            button.props(remove="loading disable")

        # Saved before adopted, same ordering as a full generation — the grid
        # can't show a meal that isn't on disk.
        state.adopt_plan(plan)
        # "catalog" as well as "plan": the run just appended to history, so
        # the Library table's "Last eaten" column is stale until it repaints.
        refreshables.refresh("plan", "catalog")
        ui.notify(f"Regenerated {slot_label(target_slot_id)}", type="positive")

        # `view` is the SlotView captured before this call — its `.recipe` is
        # still the one just discarded. None here means the slot was
        # NOT_GENERATED before this click (a prior failure, not a real
        # suggestion), which has nothing to name as rejected — see CLAUDE.md's
        # "A failed meal must not fail the week" on why NOT_GENERATED cards
        # also carry this same regenerate button.
        if view.recipe is not None:
            offer_rejection_prompt(target_slot_id, view.recipe.name)

    return GenerationHandles(
        run_generation=run_generation,
        reload_from_disk=reload_from_disk,
        regenerate_day=regenerate_day,
        regenerate_meal=regenerate_meal,
        save_grid=save_grid,
        offer_rejection_prompt=offer_rejection_prompt,
    )

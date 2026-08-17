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
from typing import Callable, Dict, Optional, Tuple

from nicegui import ui

from planner import (
    WEEKEND_DAYS,
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
from ui_state import SlotView
from ui_theme import WEEK_SELECTION_LABELS, pluralize
from week import (
    MODE_COOK,
    WeekSpec,
    clear_cuisines,
    humanize,
    parse_slot_id,
    slot_label,
    spread_batch,
    validate_week,
)


def apply_batch_selections(spec: WeekSpec, config: dict) -> Tuple[WeekSpec, dict]:
    """Turn the popup's bulk-prep/long-cook toggles into leftover links.

    Two independent `week.spread_batch` calls, both anchored on "dinner" (the
    only meal type link rules let feed both another dinner and a lunch).
    bulk_prep runs first and gets first claim on whatever room the grid has
    — it is the priority batch, and everything else (long_cook, and the
    ordinary per-day dinners) fills in around whatever it takes. long_cook
    runs second, with a real day preference of its own (weekends), and its
    search excludes bulk_prep's anchor day so a week with both toggles on
    still gets two distinct batches rather than one dinner double-booked.

    Returns the (possibly updated) spec and a dict of the two anchor slot ids
    actually chosen (None where a toggle was off, no valid anchor existed, or
    `spread_batch` couldn't grow that anchor past what an ordinary dinner
    already gets — see its docstring) — merge straight into `config` so
    `generate_meal_type_week`/`generate_sunday_prep_session` can read
    `long_cook_anchor`/`bulk_prep_anchor` off it.

    On a week whose lunches are already linked to the previous day's dinner
    (`autofill_leftovers`, or repeated "Link to next lunch" clicks), there is
    often only one slot left anywhere for a batch to grow into — so with both
    toggles on, whichever runs first claims it and the other gets nothing.
    Running bulk_prep first means that scarcity falls on long_cook, not on
    the priority batch. `generate_week` below checks for exactly that and
    warns rather than generating a mislabeled dinner silently.
    """
    target_servings = planning_rule(config, "batch_target_servings")
    anchors: Dict[str, Optional[str]] = {"long_cook_anchor": None, "bulk_prep_anchor": None}

    if config.get("bulk_prep_enabled"):
        spec, anchors["bulk_prep_anchor"] = spread_batch(spec, "dinner", target_servings)

    if config.get("long_cook_enabled"):
        weekend_days = [day for day in spec.days if day in WEEKEND_DAYS]
        exclude_days = (
            {parse_slot_id(anchors["bulk_prep_anchor"])[0]}
            if anchors["bulk_prep_anchor"]
            else None
        )
        spec, anchors["long_cook_anchor"] = spread_batch(
            spec, "dinner", target_servings, prefer_days=weekend_days, exclude_days=exclude_days
        )

    return spec, anchors


@dataclass
class GenerationHandles:
    run_generation: Callable
    reload_from_disk: Callable
    regenerate_day: Callable
    regenerate_meal: Callable


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

    with ui.dialog().props("persistent") as progress_dialog:
        with ui.element("div").classes(
            "bg-slate-900 rounded-lg p-4 w-[32rem] max-w-full flex flex-col gap-2"
        ):
            with ui.element("div").classes("flex flex-row items-center gap-1.5"):
                ui.icon("bolt").classes("text-sm text-amber-300")
                ui.label("Generating week").classes("text-sm font-semibold")
            progress_status = ui.label("Starting…").classes("text-xs text-slate-300")
            progress_bar = ui.linear_progress(value=0.0, size="10px", show_value=False).props(
                "rounded color=primary"
            )
            ui.label(
                "One API call per meal type, 30s–3 min each. This window stays "
                "until the whole week is done."
            ).classes("text-[10px] text-slate-500")
            # Portion trims and failed days both arrive as notes, mid-run. A log
            # keeps them all — a single status label would overwrite the trim
            # you wanted to read with the next day's heading.
            progress_log = ui.log(max_lines=200).classes(
                "w-full h-40 text-[10px] bg-slate-950 rounded"
            )

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
        if state.cuisine_override:
            # A slot on screen still carries the concrete cuisine a previous
            # run picked from the FULL list — validate_week would reject it
            # the instant the popup's pick narrows config["cuisines"] out
            # from under it. Clearing lets resolve_auto_choices below re-pick
            # every cook slot's cuisine from the narrower list instead.
            spec = clear_cuisines(spec)
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

        button.props("loading")
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
            # Targets whichever week is selected in the header — generating
            # while "Next Week" is showing must not overwrite "current".
            await REPOSITORY.save_week_plan(week_plan.model_dump(), state.week_selection)
            await record_week_history(week_plan, REPOSITORY, config)
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
        refreshables.refresh("plan")

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
        refreshables.refresh("plan")

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
        refreshables.refresh("plan")
        ui.notify(f"Regenerated {slot_label(target_slot_id)}", type="positive")

    return GenerationHandles(
        run_generation=run_generation,
        reload_from_disk=reload_from_disk,
        regenerate_day=regenerate_day,
        regenerate_meal=regenerate_meal,
    )

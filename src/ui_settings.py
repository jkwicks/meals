"""Settings: week start, shopping days, model, and an integrations list whose
rows open read-only detail views. Everything here is a durable-feeling global
rather than a per-run input — the opposite category from `ui_review.py`'s
contents, which is the whole reason the two used to be crammed into one drawer.

The integrations list replaces `ui_telemetry.py`'s old per-day
`context_pipeline` chip row (28 chips: 3 unconnected stages x 7 days, plus
workout x 7 days) — a decision made for phase 3 of `ui-redesign.md` rather
than wiring the three unbuilt stages or deleting the roadmap outright.

**Phase 6e turned three of those four rows into doors.** The finding was that
`biometrics.json`'s sync checkpoints, `schedule.json`'s `base_schedule`/
`location_rules` and the review dialog's `training_schedule` are all data the
app already reads on every generation and shows nowhere — so each is now a
`ui.dialog` opened from its own `PIPELINE_STAGES` row: which days each sync
source has, where each day is spent, and what is trained when. All three are
**reads**. Nothing here triggers a sync, writes a config file, or edits a
schedule; the row that owns a piece of state keeps owning it (Review's
training editor, the sync CLI), and this is a window onto it.

That is also what forced the two stale `PIPELINE_STAGES` descriptions — a
"Health Connect Sync — not built yet" row cannot open a dialog listing four
real Garmin weigh-ins. See the note above that constant in `ui_theme.py`.

**Dialogs rather than more sections in the panel**, on the maintainer's call:
the ask (ISSUES.md item 8) says "own popup/page" for each of the three, the
rail is deliberately five destinations and these are reference views rather
than places to work, and three tables stacked under the three selects would
bury the selects. Each body is `@ui.refreshable` and repainted on open, the
same shape `ui_inspector.py` uses — that is what keeps a tab left open
overnight from drawing yesterday's 14-day sync window.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Dict, Optional

from nicegui import ui

from planner import (
    TARGET_MODE_AUTO,
    TARGET_MODE_MACROS,
    TARGET_MODE_MANUAL,
    selectable_models,
)
from ui_context import UIContext
from ui_state import SyncSourceStatus, location_view, sync_status
from ui_today import location_row, session_chip
from ui_theme import (
    PIPELINE_STAGES,
    RADIUS_CARD,
    RADIUS_PANEL,
    RADIUS_PILL,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    SYNC_DAY_STYLES,
    TARGET_FIELDS,
    TARGET_SOURCE_ROWS,
    TEXT_BODY,
    TEXT_DISPLAY,
    TEXT_HEAD,
    TEXT_MICRO,
)
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP

# What each `<meal_type>_mode` actually does to the grid, in one clause.
# Straight out of `week.apply_location_modes` — including the leftover
# fallback, which is the part a reader cannot infer from the word "leftover"
# and the part most likely to look like a bug when Monday's Office lunch
# cooks instead of inheriting.
LOCATION_MODE_NOTES = {
    MODE_COOK: "cooked fresh that day",
    MODE_LEFTOVER: "inherits the previous day's dinner — cooks instead when there is none",
    MODE_SKIP: "not planned",
}

# The rows that open something. Keyed by `PIPELINE_STAGES[0]`; a stage absent
# from this map renders as a plain status row, which is what `readiness` (the
# one genuinely unbuilt stage) still is.
SYNC_STAGE = "sync"
LOCATION_STAGE = "context"
WORKOUT_STAGE = "workout"


def _stamp(iso: Optional[str]) -> str:
    """An ISO date as "Mon 24 Aug", or an em dash when there isn't one.

    Not `ui_theme.format_day_label`: that one takes a weekday *name* plus an
    optional date and exists to degrade to the name when a plan predates
    `week_start_date`. Here the date is the only thing there is, and its
    absence means "never" rather than "unknown day".
    """
    if not iso:
        return "—"
    return datetime.fromisoformat(iso).strftime("%a %-d %b")


@dataclass
class SettingsHandles:
    panel: Callable
    # So a future caller — a rail shortcut, a "sync is stale" banner — can
    # open one of the three detail views without going through the Settings
    # destination first. Nothing does today.
    open_stage: Callable[[str], None]
    # Registered on "settings" (its own toggles) and on "targets", because a
    # review-dialog override changes the figures this section reports even
    # though nothing here was touched.
    targets_source: Callable


def build_settings(ctx: UIContext, biometrics: dict) -> SettingsHandles:
    state = ctx.state
    repository = ctx.repository
    refreshables = ctx.refreshables

    # ---- daily targets: where each macro's number comes from ---------------
    #
    # The one section on this page that *writes* — see
    # `PlannerState.set_target_mode`. Everything else in Settings is either a
    # per-session preference (week start, shopping days, model) or a read view,
    # and everything in `ui_review.py` is an input to the next run that
    # deliberately evaporates. A mode is neither: it answers "where does this
    # number come from", and an answer that reset on every page load would be
    # worse than the silence it replaces.
    #
    # It exists because the answer was genuinely unknowable from the UI.
    # `hydrate_dynamic_targets` replaces an `auto` macro's `weekly_schedule`
    # figure outright, so the file could say 1000 kcal while every run planned
    # 1722 — and the header, reading the file, displayed the 1000. Naming the
    # source per macro, next to the live computed value and the arithmetic
    # behind it, is what makes that legible rather than something you work out
    # by reading `planner.py`.

    def macro_value(macro: str) -> str:
        """The macro's current figure, as one string for the whole week.

        Collapsed to a single number when every day agrees (which `auto`
        calories and locked protein both produce) and to a range when they
        don't, rather than seven figures in a settings row — the per-day
        breakdown is one expansion below.
        """
        values = {round(float(state.planned_targets(day)[macro]), 1) for day in state.days}
        low, high = min(values), max(values)
        return f"{low:.0f}" if low == high else f"{low:.0f}–{high:.0f}"

    def basis_note(macro: str) -> Optional[str]:
        """How an `auto` macro got its number, in the engine's own figures."""
        basis = state.planning_config().get("dynamic_basis")
        if not basis:
            return None
        if macro == "calories":
            return (
                f"{basis['bmr_method'].replace('_', '-').title()} BMR "
                f"{basis['bmr']:.0f} → TDEE {basis['tdee']:.0f} "
                f"({basis['tdee_source'].replace('_', ' ')}) − "
                f"{basis['deficit_kcal']:.0f} deficit "
                f"({basis['current_weight_kg']:.1f} → {basis['target_weight_kg']:.1f} kg)"
            )
        multiplier = (state.config.get("user_profile") or {}).get("protein_multiplier") or 1.8
        return (
            f"{basis['target_weight_kg']:.0f} kg target weight × {multiplier} — "
            "locked to the target, not today's weight"
        )

    async def on_mode(macro: str, event) -> None:
        # `set_target_mode` seeds `weekly_schedule` from the engine on the way
        # into manual, so both the toggle and the per-day inputs below it have
        # to repaint. "targets" also reaches the review dialog's curve and the
        # telemetry header, which are now reading a different source.
        await state.set_target_mode(repository, macro, event.value)
        refreshables.refresh("settings", "targets", "plan")

    def manual_day_inputs(macro: str) -> None:
        """Per-day inputs for a macro that is manual, persisted on blur.

        Debounced and written through `save_manual_targets` rather than
        per-keystroke, so a half-typed "1" doesn't briefly become the day's
        target on disk. Deliberately *not* refreshable per edit — the same
        focus-theft rule `ui_review.day_target_row` follows — so nothing here
        repaints while a number is being typed.
        """
        async def on_edit(day: str, event) -> None:
            if event.value is None or event.value == "":
                return
            state.set_manual_target(day, macro, float(event.value))
            await state.save_manual_targets(repository)
            refreshables.refresh("targets")

        with ui.element("div").classes(
            f"flex flex-row flex-nowrap gap-{SPACE_TIGHT} w-full overflow-x-auto"
        ):
            for day in state.days:
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_HAIR} min-w-0 flex-1"
                ):
                    ui.label(day[:3]).classes(
                        f"{TEXT_MICRO} text-slate-500 text-center"
                    )
                    ui.number(
                        value=float(state.config["weekly_schedule"][day][macro]),
                        min=0,
                        step=10,
                        precision=0,
                        on_change=lambda event, d=day: on_edit(d, event),
                    ).props("dense outlined debounce=600").classes(
                        f"w-full {TEXT_MICRO}"
                    )

    @ui.refreshable
    def targets_source() -> None:
        for macro, label, fixed_note, unit in TARGET_SOURCE_ROWS:
            switchable = macro in TARGET_MODE_MACROS
            mode = state.target_modes.get(macro, TARGET_MODE_AUTO)
            with ui.element("div").classes(
                f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_BASE} {RADIUS_CARD} "
                "border border-slate-800 bg-slate-800/30 w-full min-w-0"
            ):
                with ui.element("div").classes(
                    f"flex flex-row flex-nowrap items-center justify-between "
                    f"gap-{SPACE_BASE} min-w-0"
                ):
                    ui.label(label).classes(
                        f"{TEXT_BODY} font-semibold text-slate-200 shrink-0"
                    )
                    if switchable:
                        ui.toggle(
                            {TARGET_MODE_AUTO: "Auto", TARGET_MODE_MANUAL: "Manual"},
                            value=mode,
                            on_change=lambda event, m=macro: on_mode(m, event),
                        ).props("dense no-caps unelevated size=sm").classes(TEXT_MICRO)
                    else:
                        ui.label("Fixed" if macro == "fat_g" else "Yours").classes(
                            f"{TEXT_MICRO} px-{SPACE_TIGHT} py-{SPACE_HAIR} {RADIUS_PILL} "
                            "bg-slate-700/40 text-slate-400 shrink-0"
                        )
                    ui.element("div").classes("flex-1 min-w-0")
                    ui.label(f"{macro_value(macro)} {unit}").classes(
                        f"{TEXT_BODY} font-mono text-slate-300 shrink-0"
                    )
                note = fixed_note
                if switchable:
                    note = (
                        basis_note(macro)
                        if mode == TARGET_MODE_AUTO
                        else "Your number — the engine leaves it alone, and a "
                        "workout no longer adds to it."
                    )
                if note:
                    ui.label(note).classes(f"{TEXT_MICRO} text-slate-500")
                # Carbs are always per-day editable; a switchable macro only
                # once it is manual. Fat has no inputs at all — an editable
                # fat would be a second answer to what `derive_fat_g` already
                # computes, the same reason the review dialog shows it and
                # never types it.
                if macro == "net_carbs_g" or (switchable and mode == TARGET_MODE_MANUAL):
                    manual_day_inputs(macro)

    # ---- sync status ------------------------------------------------------

    def sync_strip(status: SyncSourceStatus) -> None:
        """One cell per day in the window, oldest left.

        `flex-nowrap`, like every other icon-or-chip row in this UI: Quasar's
        own `.flex` sets `flex-wrap: wrap`, and a strip that wrapped would
        break the one thing it is for — reading a fortnight left to right.
        """
        with ui.element("div").classes(
            f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR}"
        ):
            for day in status.days:
                look = SYNC_DAY_STYLES[day.state]
                with ui.element("div").classes(
                    f"w-3 h-3 {RADIUS_CARD} shrink-0 {look['classes']}"
                ):
                    ui.tooltip(f"{_stamp(day.date)} — {look['phrase']}")

    def sync_source_card(status: SyncSourceStatus) -> None:
        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_BASE} {RADIUS_CARD} "
            "border border-slate-800 bg-slate-950/30"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-baseline justify-between gap-{SPACE_BASE}"
            ):
                ui.label(status.label).classes(
                    f"{TEXT_HEAD} font-semibold text-slate-200"
                )
                ui.label(f"{status.recorded_total} row(s) on file").classes(
                    f"{TEXT_MICRO} text-slate-500"
                )

            if not status.connected:
                # Never run. The strip below would be 14 identical outlines
                # saying the same thing less clearly.
                ui.label(
                    f"Never synced — nothing in {status.section}, and no checkpoint."
                ).classes(f"{TEXT_BODY} text-slate-500")
                return

            with ui.element("div").classes(
                f"flex flex-row flex-wrap gap-x-{SPACE_SECTION} gap-y-{SPACE_HAIR}"
            ):
                # Two dates, not one "latest". The gap between them is the
                # information: checked through Wednesday with the last
                # weigh-in on Sunday is three mornings nobody stood on the
                # scale, which is a different situation from a source nobody
                # has synced since Sunday.
                ui.label(f"Last checked {_stamp(status.last_checked)}").classes(
                    f"{TEXT_BODY} text-slate-400"
                )
                ui.label(f"Last recorded {_stamp(status.last_recorded)}").classes(
                    f"{TEXT_BODY} text-slate-400"
                )

            sync_strip(status)

            summary = " · ".join(
                f"{status.count(key)} {SYNC_DAY_STYLES[key]['count']}"
                for key in SYNC_DAY_STYLES
                if status.count(key)
            )
            ui.label(summary).classes(f"{TEXT_MICRO} text-slate-500")

    @ui.refreshable
    def sync_body() -> None:
        # `date.today()` here rather than at build time: the body is
        # repainted on open, so a tab left open overnight draws the window
        # ending on the day it is actually being read.
        statuses = sync_status(biometrics, date.today())

        with ui.element("div").classes(f"flex flex-col gap-{SPACE_SECTION}"):
            ui.label(
                "The last 14 days, per source, as data/biometrics.json records "
                "them. A filled cell is a day with a row; a dim cell is a day "
                "the sync asked about and found nothing — a forgotten weigh-in "
                "or an unlogged day, which is a real answer; an outline is a "
                "day nobody has asked about yet."
            ).classes(f"{TEXT_BODY} text-slate-400")

            for status in statuses:
                sync_source_card(status)

            with ui.element("div").classes(
                f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_BASE} {RADIUS_CARD} "
                "border border-slate-800 bg-slate-950/30"
            ):
                ui.label("This page never syncs.").classes(
                    f"{TEXT_BODY} font-semibold text-slate-300"
                )
                ui.label(
                    "Nothing on it reaches Garmin or Cronometer — starting the "
                    "server doesn't either. Fetching a missing day is the sync "
                    "CLI's job, and it walks back from each source's own "
                    "checkpoint, so re-running it costs nothing for days "
                    "already checked:"
                ).classes(f"{TEXT_MICRO} text-slate-500")
                ui.label(
                    "./venv/bin/python src/integrations/sync_service.py "
                    "--sync-garmin --sync-cronometer"
                ).classes(f"{TEXT_MICRO} font-mono text-slate-400 break-all")

    # ---- location defaults ------------------------------------------------

    @ui.refreshable
    def location_body() -> None:
        # One config, seven days — `day_context` would be seven
        # `apply_training_adjustments` passes over the week for the same
        # answer. See `ui_state.location_view`.
        config = state.planning_config()
        meal_types = state.meal_types

        with ui.element("div").classes(f"flex flex-col gap-{SPACE_SECTION}"):
            ui.label(
                "Where each day is spent by default, and what that does to its "
                "meals before anything is generated. These are defaults from "
                "schedule.json — there is no calendar integration, so a day "
                "that turns out differently is not reflected here, and an "
                "already-generated week keeps whatever its grid says."
            ).classes(f"{TEXT_BODY} text-slate-400")

            for day in state.days:
                location = location_view(config, meal_types, day)
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_BASE} {RADIUS_CARD} "
                    "border border-slate-800 bg-slate-950/30"
                ):
                    # The bare weekday name, deliberately: `base_schedule` is
                    # keyed by weekday and applies to every week, so dating it
                    # against the loaded plan would read as a claim about this
                    # Monday in particular.
                    ui.label(day).classes(f"{TEXT_HEAD} font-semibold text-slate-200")

                    if location is None:
                        ui.label("No default location.").classes(
                            f"{TEXT_BODY} text-slate-500"
                        )
                        continue

                    location_row(location)

                    if not location.meal_modes:
                        ui.label(
                            "Constrains nothing — meals follow week_defaults."
                        ).classes(f"{TEXT_MICRO} text-slate-500")
                        continue

                    for meal_type in meal_types:
                        mode = location.meal_modes.get(meal_type)
                        if mode is None:
                            continue
                        with ui.element("div").classes(
                            f"flex flex-row flex-nowrap items-baseline gap-{SPACE_TIGHT}"
                        ):
                            ui.label(meal_type.upper()).classes(
                                f"{TEXT_MICRO} font-semibold tracking-widest "
                                "text-slate-500 shrink-0"
                            )
                            # A skip carrying an estimate is a meal that was
                            # eaten, not one that was missed — the whole
                            # distinction `SlotSpec.skip_estimate` exists to
                            # make. So it does not get MODE_SKIP's own note:
                            # "not planned" beside "eaten out, ~795 kcal" is
                            # two clauses contradicting each other on one line.
                            estimate = location.skip_estimates.get(meal_type)
                            ui.label(
                                "not cooked, but eaten"
                                if estimate
                                else LOCATION_MODE_NOTES.get(mode, mode)
                            ).classes(f"{TEXT_MICRO} text-slate-400 min-w-0")
                            if estimate:
                                with ui.element("div").classes("shrink-0"):
                                    ui.label(
                                        f"~{float(estimate['calories']):.0f} kcal counted"
                                    ).classes(f"{TEXT_MICRO} text-amber-300/70")
                                    ui.tooltip(
                                        "Counted into the day's totals so the "
                                        "meals that are cooked aren't briefed "
                                        "for calories already eaten."
                                    ).classes("max-w-xs")

    # ---- workout schedule -------------------------------------------------

    @ui.refreshable
    def workout_body() -> None:
        by_day = {day: state.training_for(day) for day in state.days}
        active = [
            session
            for sessions in by_day.values()
            for session in sessions
            if not session.is_rest
        ]

        with ui.element("div").classes(f"flex flex-col gap-{SPACE_SECTION}"):
            ui.label(
                "The week's sessions as the next generation would see them, "
                "including anything staged in Review and not yet generated. "
                "Each one's estimated burn is what expands that day's calorie "
                "budget and pins its post-workout meal. Edit them in Review → "
                "Training Schedule; nothing here writes."
            ).classes(f"{TEXT_BODY} text-slate-400")

            with ui.element("div").classes(
                f"flex flex-row flex-wrap gap-x-{SPACE_SECTION} gap-y-{SPACE_HAIR} "
                f"p-{SPACE_BASE} {RADIUS_CARD} border border-slate-800 bg-slate-950/30"
            ):
                ui.label(f"{len(active)} session(s)").classes(
                    f"{TEXT_BODY} font-semibold text-slate-200"
                )
                ui.label(
                    f"{sum(1 for sessions in by_day.values() if any(not s.is_rest for s in sessions))} "
                    "day(s) training"
                ).classes(f"{TEXT_BODY} text-slate-400")
                ui.label(
                    f"+{sum(session.burn_kcal for session in active):.0f} kcal across the week"
                ).classes(f"{TEXT_BODY} text-amber-300/70")

            for day, sessions in by_day.items():
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_BASE} {RADIUS_CARD} "
                    "border border-slate-800 bg-slate-950/30"
                ):
                    # Weekday name, not a date, for the same reason the
                    # location page uses one: `training_schedule` is keyed by
                    # weekday and repeats every week.
                    ui.label(day).classes(f"{TEXT_HEAD} font-semibold text-slate-200")
                    if not sessions:
                        ui.label("Nothing scheduled.").classes(
                            f"{TEXT_BODY} text-slate-500"
                        )
                        continue
                    with ui.element("div").classes(
                        f"flex flex-row flex-wrap items-center gap-{SPACE_TIGHT}"
                    ):
                        for session in sessions:
                            session_chip(session)

            ui.label(
                "Proposing sessions from Garmin activity history, and anything "
                "that scores adherence against them, is future-ideas.md's 5b — "
                "it needs a completion schema this page deliberately doesn't "
                "invent."
            ).classes(f"{TEXT_MICRO} text-slate-500")

    # ---- the dialogs ------------------------------------------------------
    # One per surface, built once and reused, keyed by nothing: unlike the day
    # inspector there is no per-open parameter — each dialog always shows the
    # same week.

    def detail_dialog(title: str, body: Callable) -> ui.dialog:
        with ui.dialog() as dialog:
            with ui.element("div").classes(
                f"bg-slate-900 {RADIUS_PANEL} border border-slate-800 "
                "overflow-y-auto max-h-[85vh] w-[36rem] max-w-full"
            ):
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_SECTION} p-{SPACE_PAGE}"
                ):
                    with ui.element("div").classes(
                        "flex flex-row items-center justify-between"
                    ):
                        ui.label(title).classes(
                            f"{TEXT_DISPLAY} font-semibold text-slate-200"
                        )
                        ui.button(icon="close", on_click=dialog.close).props(
                            "dense flat round size=sm"
                        ).classes("text-slate-400")
                    body()
        return dialog

    dialogs: Dict[str, ui.dialog] = {
        SYNC_STAGE: detail_dialog("Biometric sync", sync_body),
        LOCATION_STAGE: detail_dialog("Location defaults", location_body),
        WORKOUT_STAGE: detail_dialog("Workout schedule", workout_body),
    }
    bodies: Dict[str, Callable] = {
        SYNC_STAGE: sync_body,
        LOCATION_STAGE: location_body,
        WORKOUT_STAGE: workout_body,
    }

    def open_stage(key: str) -> None:
        # Repaint before showing, not on a refresh topic: two of these read
        # live state (`training_schedule`, `planning_config`) and one reads
        # the clock, and a dialog that is closed 99% of the time has no
        # business being repainted by every edit that touches them.
        bodies[key].refresh()
        dialogs[key].open()

    # ---- the panel --------------------------------------------------------

    def integrations_status() -> None:
        with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT}"):
            for key, label, icon, description, connected in PIPELINE_STAGES:
                openable = key in dialogs
                row = ui.element("div").classes(
                    f"flex flex-row flex-nowrap items-center gap-{SPACE_BASE} "
                    f"p-{SPACE_TIGHT} {RADIUS_CARD} border border-slate-800 "
                    "bg-slate-950/30 "
                    + ("cursor-pointer hover:border-slate-700" if openable else "")
                )
                if openable:
                    row.on("click", lambda _=None, k=key: open_stage(k))
                with row:
                    ui.icon(icon).classes(
                        f"{TEXT_HEAD} "
                        + ("text-emerald-300" if connected else "text-slate-600")
                    )
                    with ui.element("div").classes("flex flex-col min-w-0 flex-1"):
                        ui.label(label).classes(f"{TEXT_BODY} font-semibold text-slate-200")
                        ui.label(description).classes(f"{TEXT_MICRO} text-slate-500")
                    ui.label("Connected" if connected else "Not connected").classes(
                        f"{TEXT_MICRO} font-semibold uppercase tracking-wide shrink-0 "
                        + ("text-emerald-300" if connected else "text-slate-600")
                    )
                    # The chevron is the only thing distinguishing a row that
                    # opens something from one that doesn't — "Connected" says
                    # nothing about whether there is a page behind it.
                    ui.icon("chevron_right" if openable else "remove").classes(
                        f"{TEXT_HEAD} shrink-0 "
                        + ("text-slate-400" if openable else "text-slate-800")
                    )

    @ui.refreshable
    def panel() -> None:
        all_days = list(state.config["weekly_schedule"].keys())

        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_SECTION} p-{SPACE_SECTION} max-w-xl"
        ):
            ui.label("Settings").classes(f"{TEXT_HEAD} font-semibold text-slate-200")

            def on_week_start(event) -> None:
                # Set the field explicitly before refreshing: `bind_value`
                # keeps state in sync through the binding loop, which runs
                # *after* this handler, so a refresh relying on it alone
                # would repaint the old week order.
                state.week_start = event.value
                refreshables.refresh("plan")

            ui.select(
                all_days,
                label="Week starts on",
                on_change=on_week_start,
            ).bind_value(state, "week_start").props("dense outlined").classes(
                f"w-full {TEXT_BODY}"
            )

            def on_shop_days(event) -> None:
                state.shop_days = list(event.value or [])
                # Shop days *are* the window boundaries, so this repartitions
                # every list in the shopping drawer.
                refreshables.refresh("shopping_days")

            ui.select(
                all_days,
                label="Shopping days",
                multiple=True,
                on_change=on_shop_days,
            ).bind_value(state, "shop_days").props("dense outlined use-chips").classes(
                f"w-full {TEXT_BODY}"
            )

            ui.select(
                selectable_models(state.models_config),
                label="Model",
            ).bind_value(state, "model").props("dense outlined").classes(f"w-full {TEXT_BODY}")

            ui.separator()
            with ui.element("div").classes(
                f"flex flex-row items-baseline justify-between gap-{SPACE_BASE}"
            ):
                ui.label("Daily Targets").classes(
                    f"{TEXT_BODY} font-semibold text-slate-300"
                )
                ui.label("where each number comes from").classes(
                    f"{TEXT_MICRO} text-slate-500"
                )
            with ui.element("div").classes(
                f"flex flex-col gap-{SPACE_BASE} w-full min-w-0"
            ):
                targets_source()

            ui.separator()
            ui.label("Integrations").classes(f"{TEXT_BODY} font-semibold text-slate-300")
            integrations_status()

    return SettingsHandles(
        panel=panel, open_stage=open_stage, targets_source=targets_source
    )


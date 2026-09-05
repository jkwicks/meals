"""The "Today" tab: a read-only preview of today's meals, in today's context.

Deliberately minimal — no favorite/swap/regenerate buttons yet. Clicking a
card does open the same recipe detail dialog the Week tab's cards use
(`cards.open_detail`, from `ui_cards.CardHandles`) — one dialog reused by
both tabs, same as it's already reused across all 28 Week-tab cards, rather
than a second copy living here. Not built on `ui_cards.meal_card` itself:
that function's action-row buttons all need `ui_catalog`/`ui_generation`
wired in, none of which a card with no buttons needs, so a smaller card of
its own here is a real decoupling rather than a "fix later" shortcut.

**What the Week tab can't show, and this tab can.** Location and training are
per-day facts, and the Week tab has seven columns to fit into a screen — the
best it manages is a bolt in the telemetry header saying *that* a day
has a workout. Today is one day wide, so it has the room to say which
session, at what time, for how many calories, and where the day is spent. So
that context strip lives here rather than being pushed up into shared chrome:
it is the thing this tab is *for*, not a smaller copy of the header's marker.

Both come from `ui_state.day_context`, which reads them off the config the
next run would use — so a training session added in the drawer shows up here
immediately, matching the calorie bar directly above it rather than the file
on disk.

**The day-rendering helpers below (`location_row` through `today_card`) are
module-level, not nested in `build_today`, so `ui_inspector.py`'s day
inspector (phase 4 of `ui-redesign.md`) can call the exact same functions for
an arbitrary day instead of duplicating this rendering. `today_card` is the
one that needs `cards: CardHandles` passed explicitly rather than closed
over, for that reason.**
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from nicegui import ui

from planner import workout_session_id
from ui_adherence import AdherenceHandles
from ui_cards import CardHandles
from ui_context import UIContext
from ui_state import (
    DayContext,
    LocationView,
    MealAdherenceView,
    PlannerState,
    SlotView,
    TrainingView,
    WorkoutExerciseView,
    WorkoutMarkView,
    WorkoutSessionView,
    day_context,
    is_gym_session,
    workout_session_view,
)
from ui_theme import (
    ADHERENCE_MARK_ICONS,
    ADHERENCE_MARK_ORDER,
    ADHERENCE_SOURCE_ICONS,
    ADHERENCE_UNMARKED_ICON,
    LOCATION_ACCENT,
    adherence_mark_tooltip,
    format_day_label,
    MACRO_LABELS,
    MACRO_TINTS,
    RADIUS_CARD,
    RADIUS_PANEL,
    RADIUS_PILL,
    REST_ACCENT,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    STATUS_SKIP,
    STATUS_STYLES,
    TEXT_BODY,
    TEXT_DISPLAY,
    TEXT_HEAD,
    TEXT_MICRO,
    training_icon,
    TRAINING_ACCENT,
    TRAINING_NOTE_BADGES,
    WEEK_SELECTION_LABELS,
    link_line,
    telemetry_bar,
)
from week import MODE_COOK, MODE_LEFTOVER, humanize, slot_id
from workout import WORKOUT_FEEDBACK_RESPONSES

# The three responses design-06 §7 defines, as a button label/icon pair — kept
# local to this module rather than in `ui_theme.py`: nothing else in the app
# renders workout feedback, and a shared constant nobody else reads is just a
# second place to look. Order follows `WORKOUT_FEEDBACK_RESPONSES` (itself
# read off `WorkoutFeedback.response`'s own field, never hand-copied — see
# that module) rather than being re-stated here.
WORKOUT_FEEDBACK_LABELS = {
    "no_issue": "No issue",
    "mild_irritation": "Mild irritation",
    "worse_than_usual": "Worse than usual",
}
WORKOUT_FEEDBACK_ICONS = {
    "no_issue": "check_circle",
    "mild_irritation": "sentiment_neutral",
    "worse_than_usual": "sentiment_very_dissatisfied",
}

# Every icon+text row below carries `flex-nowrap`. Quasar's own `.flex` rule
# sets `flex-wrap: wrap` and Tailwind's `flex-row` does not undo it, so a
# label long enough to fill its chip wraps *below* its icon and runs back
# underneath it — the same trap the recipe dialog's step rows document.
CHIP = f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT} px-{SPACE_TIGHT} py-[2px] {RADIUS_PILL}"


@dataclass
class DayMarks:
    """One day's adherence marks, plus the handlers that change them.

    A parameter object rather than four more arguments on `today_card` and
    `context_strip`, because both of those are also called by
    `ui_inspector.py` and every one of them would have had to grow the same
    four. It bundles view model and handles deliberately: a widget module is
    exactly where those two meet, and keeping them apart would mean threading
    both through the same call sites anyway.

    Optional throughout — `None` means "this surface doesn't offer marking",
    which is what every mark-drawing function below checks first. Nothing
    today passes None, but the two call sites are a Daily View and a day
    inspector, and a third read-only surface (an export, a printed menu)
    should be able to reuse these renderers without inventing handlers it has
    no page to repaint.
    """

    day: str
    meals: MealAdherenceView
    workouts: List[WorkoutMarkView]
    handles: AdherenceHandles


def build_day_marks(
    state: PlannerState, day: str, adherence: AdherenceHandles
) -> DayMarks:
    """Both mark views for `day`, read once per repaint.

    Once, not once per card, for the same reason `day_context` is built once:
    `meal_adherence_for` walks the day's spec slots and `workout_marks_for`
    walks the activity log, and four cards each asking would be four copies
    of one day's answer.
    """
    return DayMarks(
        day=day,
        meals=state.meal_adherence_for(day),
        workouts=state.workout_marks_for(day),
        handles=adherence,
    )


@dataclass
class WorkoutHandles:
    """`state`, plus the training strip's one workout click action.

    A parameter object for the same reason `DayMarks` is one: `training_row`
    needs `state` to ask `workout_session_view`/`is_gym_session` whether a
    session has generated detail, and `ui_inspector.py`'s reuse of these
    renderers should be free to pass `None` for "no workout affordance here"
    exactly as it already does for `DayMarks`. `open`/`generate` are the same
    action either way — open the dialog if there's something to show,
    otherwise generate the week and open it if that succeeded — split into
    two callables only because the Daily View's own "generate this week's
    workouts" reminder (below the strip) needs the second on its own,
    without a session to open afterwards.
    """

    state: PlannerState
    open: Callable[[str, TrainingView], None]
    generate: Callable[[str, TrainingView], None]


# ---- the day-context strip: where you are, what you're training -----------
# Module-level so `ui_inspector.py` can reuse them for an arbitrary day —
# see the module docstring.


def location_row(location: LocationView) -> None:
    with ui.element("div").classes(f"flex flex-row flex-wrap items-center gap-{SPACE_TIGHT}"):
        with ui.element("div").classes(f"{CHIP} {LOCATION_ACCENT}"):
            ui.icon("place").classes(TEXT_BODY)
            ui.label(location.name).classes(
                f"{TEXT_BODY} font-semibold tracking-wide"
            )

        # One chip per restriction tag, humanized, with the model's own
        # sentence for it on hover. The tag is a config token ("portable")
        # and the phrase is what it means; showing the phrase inline would
        # be three lines of prose above four cards, and showing only the
        # tag would leave "no long prep" to be guessed at.
        for tag, phrase in location.phrase_pairs:
            with ui.element("div").classes(
                f"{CHIP} bg-slate-800/60 text-slate-300 ring-1 ring-inset "
                "ring-slate-700/60"
            ):
                ui.label(humanize(tag)).classes(TEXT_MICRO)
                ui.tooltip(phrase).classes("max-w-xs")

        if location.max_prep_minutes is not None:
            ui.label(
                "no prep at the eating location"
                if location.max_prep_minutes == 0
                else f"≤ {location.max_prep_minutes} min prep"
            ).classes(f"{TEXT_MICRO} text-slate-400 italic")

        if location.notes:
            ui.label(location.notes).classes(f"{TEXT_MICRO} text-slate-400 italic")


def session_chip(session: TrainingView) -> None:
    if session.is_rest:
        # `bedtime` regardless of the type, unlike the branch below: this
        # chip says "Rest day", and `is_rest` also catches a zero-burn
        # session of a real type. Icon follows the label it sits beside.
        with ui.element("div").classes(f"{CHIP} {REST_ACCENT}"):
            ui.icon("bedtime").classes(TEXT_BODY)
            ui.label("Rest day").classes(f"{TEXT_MICRO} font-semibold tracking-wide")
        return

    with ui.element("div").classes(f"{CHIP} {TRAINING_ACCENT}"):
        ui.icon(training_icon(session.type)).classes(TEXT_BODY)
        ui.label(session.time).classes(f"{TEXT_BODY} font-mono")
        ui.label(session.label.title()).classes(
            f"{TEXT_BODY} font-semibold tracking-wide"
        )
        detail = " · ".join(
            part
            for part in [
                f"{session.duration_minutes} min" if session.duration_minutes else "",
                f"{session.burn_kcal:.0f} kcal" if session.burn_kcal else "",
            ]
            if part
        )
        if detail:
            ui.label(detail).classes(f"{TEXT_MICRO} font-mono text-slate-400")


def completion_mark(mark: Optional[WorkoutMarkView], marks: Optional[DayMarks]) -> None:
    """Whether a declared session happened, and the one click that says so.

    Three renders, because there are three genuinely different states and
    only one of them is editable:

    - **Recorded by Garmin** — a plain icon, not a button. `activity_log` is
      the answer for these and nothing on this page may overwrite it; the
      tooltip carries what the watch actually saw, which is the half worth
      reading when a 20-minute walk is answering for a declared hour.
    - **Marked by hand** — a button, because a manual mark is a row in
      `adherence.json` and a mis-click has to be takeable back.
    - **Neither** — the button that writes one.

    A day with no calendar date renders nothing at all rather than an
    unmarked circle: without a date there is nothing to match the activity
    log against, so "not done" would be stated as fact having never been
    checked. That is `WorkoutMarkView.markable`, and it is the same
    honest-silence default `context_strip` takes for a day with no context.
    """
    if mark is None or not mark.markable or marks is None:
        return

    if mark.source == "garmin":
        icon = ui.icon(ADHERENCE_SOURCE_ICONS["garmin"]).classes(
            f"{TEXT_BODY} text-slate-300"
        )
        with icon:
            detail = mark.detail
            ui.tooltip(
                f"Recorded by Garmin — {detail}" if detail else "Recorded by Garmin"
            )
        return

    button = ui.button(
        icon=(
            ADHERENCE_SOURCE_ICONS["manual"] if mark.marked else ADHERENCE_UNMARKED_ICON
        ),
        on_click=lambda m=mark: marks.handles.mark_workout(marks.day, m),
    )
    button.props("dense flat round size=xs").classes(
        f"min-h-0 p-{SPACE_HAIR} "
        + ("text-slate-200" if mark.marked else "text-slate-400 hover:text-slate-300")
    )
    with button:
        ui.tooltip(
            "Marked done — click to clear"
            if mark.marked
            else "Garmin didn't record this — mark it done"
        )


def workout_action(day: str, session: TrainingView, workout: Optional[WorkoutHandles]) -> None:
    """The one workout affordance beside a gym session's chip: open the
    generated detail (design-06's Adaptive Workout, Task 5.1) if there is
    any, or offer to generate this week's whole plan if there is not.

    Nothing renders for a non-gym session (`is_gym_session`) — a cardio or
    walk session has no exercise plan to generate — and nothing renders at
    all when `workout` is None, the same "no handles, no affordance" rule
    `completion_mark` follows for adherence marking.
    """
    if workout is None or not is_gym_session(session):
        return
    view = workout_session_view(workout.state, day, session)
    if view is not None:
        button = ui.button(
            icon="fitness_center", on_click=lambda: workout.open(day, session)
        )
        button.props("dense flat round size=xs").classes(
            f"min-h-0 p-{SPACE_HAIR} text-slate-300 hover:text-slate-100"
        )
        with button:
            ui.tooltip("View this session's workout")
        return

    # No detail yet — offered only once a program is actually selected, and
    # busy-guarded the same way a whole-week meal run is
    # (`state.generating`/`state.regenerating_day`).
    if not workout.state.config.get("active_gym_program"):
        return
    button = ui.button(
        icon="fitness_center", on_click=lambda: workout.generate(day, session)
    )
    button.props(
        "dense flat round size=xs"
        + (" loading disable" if workout.state.generating_workout else "")
    ).classes(f"min-h-0 p-{SPACE_HAIR} text-slate-400 hover:text-slate-300")
    with button:
        ui.tooltip("Generate this week's workouts")


def training_row(
    context: DayContext,
    marks: Optional[DayMarks] = None,
    workout: Optional[WorkoutHandles] = None,
) -> None:
    by_session = {mark.session_id: mark for mark in (marks.workouts if marks else [])}
    with ui.element("div").classes(f"flex flex-row flex-wrap items-center gap-{SPACE_TIGHT}"):
        for session in context.sessions:
            if session.is_rest:
                # Nothing to complete: `TrainingView.is_rest` folds a typed
                # rest day and a zero-burn session together, and neither is a
                # session that could have been done or missed.
                session_chip(session)
                continue
            # `flex-nowrap` per the standing Quasar trap — this is an
            # icon-beside-content row, and `.flex`'s own `flex-wrap: wrap`
            # would drop the mark below the chip it belongs to.
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR}"
            ):
                session_chip(session)
                completion_mark(
                    by_session.get(workout_session_id(session.time, session.type)),
                    marks,
                )
                workout_action(session.day, session, workout)

        # Only worth printing once there are two sessions to add up —
        # under one it would just restate the chip beside it. This is the
        # figure `apply_training_adjustments` put onto the day's calorie
        # budget, which is the denominator of the bar directly above.
        if len(context.active_sessions) > 1:
            ui.label(f"+{context.total_burn_kcal:.0f} kcal on today's budget").classes(
                f"{TEXT_MICRO} text-slate-400 italic"
            )


def context_strip(
    context: DayContext,
    marks: Optional[DayMarks] = None,
    workout: Optional[WorkoutHandles] = None,
) -> None:
    """The location and training rows, or nothing at all.

    Nothing is the honest render for a config with no `base_schedule` and
    an untrained day — the same opt-in tolerance `week.location_for` and
    `apply_training_adjustments` extend. An empty bordered panel would be
    a UI element announcing the absence of a feature.
    """
    if context.location is None and not context.sessions:
        return
    with ui.element("div").classes(
        f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_BASE} {RADIUS_CARD} border border-slate-800 "
        "bg-slate-950/30 w-fit max-w-full"
    ):
        if context.location is not None:
            location_row(context.location)
        if context.sessions:
            training_row(context, marks, workout)


# ---- the cards --------------------------------------------------------


def card_context_badges(context: DayContext, meal_type: str) -> None:
    """The location/training markers for one meal, if either has an opinion.

    Per meal rather than only on the strip because both constraints are
    scoped to a meal type, not to the day: `location_rules.Office` names
    `lunch_mode` and says nothing about the breakfast eaten at home before
    leaving, and a post-workout note lands on exactly one meal. A day-wide
    chip would be read as applying to all four.
    """
    location = context.location
    brief = location.brief(meal_type) if location else ""
    note = context.meal_notes.get(meal_type)
    if not brief and note is None:
        return

    with ui.element("div").classes(f"flex flex-row flex-wrap items-center gap-{SPACE_TIGHT} mt-0.5"):
        if brief:
            with ui.element("div").classes(f"{CHIP} {LOCATION_ACCENT}"):
                ui.icon("place").classes(TEXT_MICRO)
                ui.label(location.name).classes(
                    f"{TEXT_MICRO} font-semibold tracking-wide"
                )
                ui.tooltip(brief).classes("max-w-xs")
        if note is not None:
            badge = TRAINING_NOTE_BADGES[note.kind]
            with ui.element("div").classes(f"{CHIP} {TRAINING_ACCENT}"):
                ui.icon(badge["icon"]).classes(TEXT_MICRO)
                ui.label(badge["label"]).classes(
                    f"{TEXT_MICRO} font-semibold tracking-wide"
                )
                ui.tooltip(note.text).classes("max-w-xs")


def meal_marks(meal_type: str, marks: Optional[DayMarks]) -> None:
    """The three "what happened to this" buttons, or nothing.

    Nothing in three cases, and each is a real state rather than a guard
    against a bug: the surface offers no marking at all, the day has no
    calendar date to key a mark on (`MealAdherenceView.markable`), or this
    slot is a skip — nothing was planned to be eaten there, so "did you eat
    it" has no answer and a row of buttons would invite one.

    Clicking the status a slot already carries clears it (`mark_meal`), which
    is why the tooltip on a selected button says so: three buttons with no
    visible way back would be three one-way doors.
    """
    if marks is None or not marks.meals.markable:
        return
    slot = slot_id(marks.day, meal_type)
    if slot not in marks.meals.planned:
        return

    current = marks.meals.status_for(slot)
    for status in ADHERENCE_MARK_ORDER:
        selected = current == status
        button = ui.button(
            icon=ADHERENCE_MARK_ICONS[status],
            on_click=lambda s=status: marks.handles.mark_meal(marks.day, meal_type, s),
        )
        # Glyph and fill only — no hue. Every colour in the palette already
        # means something (the `ui-work` skill's table), and emerald, the
        # obvious tick colour, is the cook status.
        button.props("dense flat round size=xs").classes(
            f"min-h-0 p-{SPACE_HAIR} "
            + (
                "text-slate-100 bg-slate-700"
                if selected
                else "text-slate-400 hover:text-slate-300"
            )
        )
        with button:
            ui.tooltip(adherence_mark_tooltip(status, selected))


def today_card(
    view: Optional[SlotView],
    meal_type: str,
    context: DayContext,
    cards: CardHandles,
    marks: Optional[DayMarks] = None,
) -> None:
    if view is None:
        view = SlotView(day="", meal_type=meal_type, status=STATUS_SKIP, title="—")
    look = STATUS_STYLES[view.status]
    clickable = "cursor-pointer" if view.recipe else ""

    card = ui.element("div").classes(
        f"meal-card card-{view.status} {RADIUS_CARD} p-{SPACE_SECTION} flex flex-col gap-{SPACE_TIGHT} min-w-0 "
        f"w-56 {look['card']}"
    )

    with card:
        # The header row is a **sibling** of the clickable body below, not a
        # child of it — the same structure `ui_cards.meal_card` uses and for
        # the same reason: a click on a mark button would otherwise bubble
        # through the body's handler and open the recipe dialog on top of the
        # mark it just recorded. This is why the click moved off the card
        # element itself, where it used to live when nothing on the card was
        # clickable in its own right.
        with ui.element("div").classes(f"flex flex-row items-center justify-between gap-{SPACE_TIGHT}"):
            ui.label(meal_type.upper()).classes(
                f"{TEXT_MICRO} font-semibold tracking-widest text-slate-400"
            )
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR}"
            ):
                meal_marks(meal_type, marks)
                with ui.element("div").classes(
                    f"flex items-center gap-{SPACE_HAIR} px-{SPACE_TIGHT} py-[1px] {RADIUS_PILL} "
                    f"{look['badge']}"
                ):
                    ui.icon(look["icon"]).classes(TEXT_MICRO)
                    ui.label(look["label"]).classes(
                        f"{TEXT_MICRO} font-semibold tracking-wide"
                    )

        body = ui.element("div").classes(
            f"flex flex-col gap-{SPACE_TIGHT} min-w-0 {clickable}"
        )
        if view.recipe:
            body.on("click", lambda v=view: cards.open_detail(v))

        with body:
            ui.label(view.title).classes(
                f"{TEXT_HEAD} leading-tight font-bold text-slate-100 line-clamp-2"
            )

            tags = " · ".join(part for part in [view.style, view.cuisine] if part)
            if tags:
                ui.label(tags).classes(f"{TEXT_MICRO} text-slate-400 truncate")

            if view.mode == MODE_LEFTOVER and view.source_label:
                link_line("↩ from", view.source_label, view.chain_colour)

            if view.macros:
                with ui.element("div").classes(
                    f"flex flex-row flex-wrap items-center gap-x-1 mt-0.5 px-{SPACE_TIGHT} py-{SPACE_HAIR} "
                    f"{RADIUS_PILL} bg-slate-950/40 w-fit max-w-full"
                ):
                    ui.label(f"{view.macros['calories']:.0f} kcal").classes(
                        f"{TEXT_MICRO} font-mono text-slate-300"
                    )
                    for key, short, unit in MACRO_LABELS[1:]:
                        ui.label("·").classes(f"{TEXT_MICRO} text-slate-400")
                        ui.label(f"{view.macros[key]:.0f}{unit} {short}").classes(
                            f"{TEXT_MICRO} font-mono {MACRO_TINTS[key]}"
                        )

            if view.mode == MODE_COOK and view.portions:
                ui.label(
                    f"{view.portions} portions · {view.prep_minutes} min"
                    if view.prep_minutes is not None
                    else f"{view.portions} portions"
                ).classes(f"{TEXT_MICRO} text-emerald-300/70 truncate")

            card_context_badges(context, meal_type)


@dataclass
class TodayHandles:
    today_view: Callable
    # The shell hands its `ui.tab` here once it exists. `build_today` runs
    # long before the tabs are created (see `planner_page`'s build order), and
    # the label depends on which day is being browsed — so the tab is injected
    # rather than the label being computed up front and going stale on the
    # first chevron click.
    bind_tab: Callable


def build_today(
    ctx: UIContext, cards: CardHandles, adherence: AdherenceHandles
) -> TodayHandles:
    state = ctx.state
    REPOSITORY = ctx.repository
    refreshables = ctx.refreshables
    tab = None

    # ---- the day picker ----------------------------------------------------

    def tab_label() -> str:
        """What the tab itself says — "Daily View · Sun 23 Aug", or "Mon 24 Aug".

        The "Daily View ·" prefix appears only when the day on screen really
        is today. Dropping it while browsing is what keeps the tab honest:
        its `today` icon and its name would otherwise both still claim
        "today" three days into the week, which is exactly the
        confident-but-wrong rendering `today_in_week` exists to prevent.
        """
        day = state.viewed_day()
        if day is None:
            return "Daily View"
        label = format_day_label(day, state.day_date_iso(day), short=True)
        return f"Daily View · {label}" if state.viewing_today() else label

    def sync_tab_label() -> None:
        if tab is not None:
            tab.set_label(tab_label())

    def bind_tab(element) -> None:
        nonlocal tab
        tab = element
        sync_tab_label()

    async def go(step: int = 0, day: Optional[str] = None, reset: bool = False) -> None:
        """Every day change goes through here, so none can forget the label.

        `today_view` is a single refreshable covering the whole panel, so one
        refresh repaints the picker, the strip, the bar and the cards
        together — there is no narrower topic worth carving out, since a day
        change genuinely invalidates all four.

        Async because two of the three branches can now cross into the other
        cached week, which is a disk read. When they do they also change
        `week_selection`, so the refresh widens to `"plan"` — the header's
        week select, the 28-card canvas, the telemetry row and the shopping
        panel are all reading the week that just changed underneath them, and
        `"today"` would leave every one of them describing the old one. That
        is the "second control free to disagree with the header's week
        selector" objection, answered by refreshing the first control rather
        than by adding a second.
        """
        week_before = state.week_selection
        if reset:
            await state.go_to_today(REPOSITORY)
        elif day is not None:
            state.select_day(day)
        elif step:
            await state.step_viewed_day(REPOSITORY, step)
        sync_tab_label()
        refreshables.refresh(
            "plan" if state.week_selection != week_before else "today"
        )

    def chevron(icon: str, step: int) -> None:
        """One end of the picker, disabled exactly when it cannot move.

        `step_target` is asked rather than the day's index compared against
        the ends: the answer now depends on whether the *adjacent week* is
        cached, and a chevron deciding that for itself would be a second copy
        of the rule free to disagree with the one that acts. It returns None
        for "would not move", which is the same thing a disabled chevron says.

        An edge step announces where it goes. Crossing a week changes what the
        whole page is showing — and, like the header select it drives, drops
        unsaved grid edits — so it must not be the one gesture in the app that
        does that without saying so.
        """
        target = state.step_target(step)
        button = ui.button(
            icon=icon, on_click=lambda: go(step=step)
        ).props(
            f"dense flat size=sm {'disable' if target is None else ''}"
        ).classes("text-slate-400")
        if target is not None and target[0] != state.week_selection:
            with button:
                # The weekday name alone, deliberately: the other week's
                # `week_start_date` is not in hand here — `scan_cached_weeks`
                # read that plan to answer whether it exists, not to keep it —
                # and a tooltip is the last place to print a plausible-looking
                # wrong date. `format_day_label` degrades to exactly this for
                # a plan that has no start date either, so the two cases spell
                # the same and neither invents one.
                ui.tooltip(
                    f"{WEEK_SELECTION_LABELS[target[0]]} · "
                    f"{format_day_label(target[1], None, short=True)}"
                )

    def day_nav(day: str) -> None:
        days = state.days
        today = state.today_day()

        with ui.element("div").classes(f"flex flex-row flex-wrap items-center gap-{SPACE_TIGHT}"):
            # Steps into the adjacent cached week rather than clamping at the
            # ends of this one — see `PlannerState.step_target`. Still clamped
            # at the outer ends of the timeline, and still never wrapping: the
            # last cached week has genuinely nothing after it, and looping
            # Sunday back to Monday would pretend the calendar is a ring.
            chevron("chevron_left", -1)

            for name in days:
                selected = name == day
                is_today = name == today
                # Cheap by construction: `training_for` reads only the drawer's
                # schedule, so marking all seven pills costs seven list scans
                # rather than seven `planning_config()` passes over the week.
                sessions = [s for s in state.training_for(name) if not s.is_rest]
                # Deduped by icon, so two gym sessions show one dumbbell while
                # Saturday's gym-plus-HIIT shows two distinct glyphs.
                icons = list(dict.fromkeys(training_icon(s.type) for s in sessions))

                # Split across the button and its label on purpose. Quasar
                # paints a flat button's *content* with the primary colour, so
                # a text class on the button alone reached the pill while its
                # label was the button's own text and stopped the moment the
                # label became a child element — the day names silently went
                # Quasar blue. Background and ring belong to the button;
                # anything about the text has to be set on the text.
                fill = "bg-slate-700" if selected else ""
                tone = (
                    "text-slate-100 font-semibold"
                    if selected
                    else ("text-slate-300" if is_today else "text-slate-400")
                )
                # Today is a ring, not the dot it used to be: the dot slot now
                # belongs to the workout marks, and two different dots on one
                # pill would be two meanings competing for the same glyph.
                ring = " ring-1 ring-inset ring-slate-500" if is_today else ""
                button = ui.button(on_click=lambda n=name: go(day=n)).props(
                    "dense flat no-caps size=sm"
                ).classes(f"min-h-0 px-{SPACE_BASE} py-{SPACE_TIGHT} {RADIUS_CARD} {fill}{ring}")

                with button:
                    with ui.element("div").classes(
                        f"flex flex-col items-center gap-{SPACE_HAIR} leading-none"
                    ):
                        ui.label(name[:3].upper()).classes(
                            f"{TEXT_BODY} tracking-wide {tone}"
                        )
                        # Fixed height whether or not the day trains, so the
                        # pills keep one baseline down the row instead of the
                        # rest days sitting a few pixels taller.
                        with ui.element("div").classes(
                            "flex flex-row flex-nowrap items-center justify-center "
                            "gap-px h-[10px]"
                        ):
                            for icon in icons:
                                ui.icon(icon).classes(f"{TEXT_MICRO} text-slate-300")

                    tip = ([f"{s.time} {s.label.title()}" for s in sessions])
                    if is_today:
                        tip = ["Today"] + tip
                    if tip:
                        ui.tooltip(" · ".join(tip))

            chevron("chevron_right", 1)

            # Only offered when it would actually do something — the same test
            # as before, widened from the loaded week to the whole timeline.
            # It has to read disk rather than the plan on screen: step forward
            # into next week and the loaded plan has no today in it at all,
            # which is exactly when a way back is most wanted.
            if state.today_is_reachable():
                ui.button(
                    "Today", icon="today", on_click=lambda: go(reset=True)
                ).props("dense flat no-caps size=sm").classes("text-sky-300")

    # ---- the workout detail dialog (design-06, Task 5.1) --------------------
    # design-06 §9: "Today / Adaptive Workout: the detailed session,
    # applied-constraint notes, feedback buttons, and progression proposals."
    # Not built on `ui_cards.recipe_detail` — a workout is a different shape
    # entirely (exercises, not ingredients/instructions) — but it copies that
    # dialog's structure: a `ui.dialog()` wrapping one `@ui.refreshable` body
    # that reads a small mutable target dict, exactly `ui_cards.py`'s "send to
    # freezer"/"eaten out" modals. `workout_target` holds identity only (day +
    # the declared `TrainingView`); the body re-derives `WorkoutSessionView`
    # from state on every refresh rather than caching one, so a feedback click
    # or an accepted proposal is never one repaint stale.

    workout_target: Dict[str, object] = {"day": "", "session": None}

    def open_workout(day: str, session: TrainingView) -> None:
        workout_target["day"] = day
        workout_target["session"] = session
        workout_dialog_body.refresh()
        workout_dialog.open()

    async def generate_workouts() -> None:
        """Whole-week generation (design-06 §4) — explicit, never on page
        load. Guarded the same way a whole-week meal run is
        (`state.generating`/`state.regenerating_day`): a second click while
        one call is already in flight does nothing.
        """
        if state.generating_workout or state.regenerating_workout_session:
            return
        state.generating_workout = True
        today_view.refresh()
        try:
            error = await state.generate_workout_plan(REPOSITORY)
        finally:
            state.generating_workout = False
        if error:
            ui.notify(error, type="warning", multi_line=True, close_button=True, timeout=0)
        today_view.refresh()

    async def generate_and_open(day: str, session: TrainingView) -> None:
        """The day strip's "Generate this week's workouts" affordance —
        generates, then opens the very session that was clicked if the run
        produced one for it. A failed run leaves the previous file (nothing,
        the first time) untouched and reports through the same toast
        `generate_workouts` already shows; there is nothing to open then.
        """
        await generate_workouts()
        if workout_session_view(state, day, session) is not None:
            open_workout(day, session)

    async def regenerate_session() -> None:
        """The dialog's own "Regenerate this session" — design-06 §4's
        single-session regeneration, narrowed to whichever session the
        dialog is currently open on. A failed regeneration leaves the
        previously stored plan exactly as it was and reports the failure;
        the dialog stays open on the (unchanged) session either way.
        """
        day = str(workout_target["day"] or "")
        session = workout_target["session"]
        if not day or session is None or state.regenerating_workout_session:
            return
        view = workout_session_view(state, day, session)
        if view is None:
            return
        state.regenerating_workout_session = view.session_id
        workout_dialog_body.refresh()
        try:
            error = await state.regenerate_workout_session(REPOSITORY, day, view.session_id)
        finally:
            state.regenerating_workout_session = None
        if error:
            ui.notify(error, type="warning", multi_line=True, close_button=True, timeout=0)
        workout_dialog_body.refresh()
        today_view.refresh()

    async def set_feedback(
        view: WorkoutSessionView, exercise: WorkoutExerciseView, constraint_id: str, response: str
    ) -> None:
        """Record one exercise/constraint's limitation response (design-06
        §7) and repaint the dialog immediately — feedback persists on click
        and never stages, the same rule adherence marks follow.
        """
        error = await state.record_workout_feedback(
            REPOSITORY,
            date=view.date,
            session_id=view.session_id,
            exercise_id=exercise.exercise_id,
            constraint_id=constraint_id,
            response=response,
        )
        if error:
            ui.notify(error, type="warning")
            return
        workout_dialog_body.refresh()

    async def accept_proposal(view: WorkoutSessionView, exercise: WorkoutExerciseView) -> None:
        """Explicit acceptance — design-06 §6: "requires an Accept action
        before the next stored plan changes." Passes the exact proposal the
        dialog is currently showing, built moments ago from real state.
        """
        if exercise.proposal is None:
            return
        error = await state.accept_progression_proposal(
            REPOSITORY, view.session_id, exercise.proposal
        )
        if error:
            ui.notify(error, type="warning")
            return
        ui.notify("Progression accepted.", type="positive")
        workout_dialog_body.refresh()

    def constraint_feedback_row(view: WorkoutSessionView, exercise: WorkoutExerciseView, note) -> None:
        """One applied-constraint note, visible beside the exercise it binds,
        plus the three feedback buttons — offered only here, per design-06
        §7: "Surface the response only on exercises to which a personal
        constraint applied."
        """
        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_TIGHT} mt-{SPACE_TIGHT} px-{SPACE_SECTION} py-{SPACE_TIGHT} "
            f"{RADIUS_CARD} border border-slate-700 bg-slate-800/40"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-start gap-{SPACE_TIGHT}"
            ):
                ui.icon("rule").classes(f"shrink-0 {TEXT_BODY} text-slate-300 mt-[2px]")
                ui.label(f"{note.target}: {note.instruction}").classes(
                    f"min-w-0 {TEXT_MICRO} leading-snug text-slate-300"
                )
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT}"
            ):
                for response in WORKOUT_FEEDBACK_RESPONSES:
                    selected = note.response == response
                    button = ui.button(
                        icon=WORKOUT_FEEDBACK_ICONS[response],
                        on_click=lambda v=view, e=exercise, c=note.constraint_id, r=response: set_feedback(
                            v, e, c, r
                        ),
                    )
                    button.props("dense flat no-caps size=xs").classes(
                        f"min-h-0 p-{SPACE_HAIR} "
                        + (
                            "text-slate-100 bg-slate-700"
                            if selected
                            else "text-slate-400 hover:text-slate-300"
                        )
                    )
                    with button:
                        ui.tooltip(WORKOUT_FEEDBACK_LABELS[response])

    def exercise_card(view: WorkoutSessionView, exercise: WorkoutExerciseView) -> None:
        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_SECTION} {RADIUS_CARD} border "
            "border-slate-800 bg-slate-800/30"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-wrap items-baseline justify-between gap-{SPACE_BASE}"
            ):
                ui.label(exercise.name).classes(
                    f"{TEXT_HEAD} font-semibold text-slate-100"
                )
                ui.label(f"{exercise.movement_pattern} · {exercise.role}").classes(
                    f"{TEXT_MICRO} text-slate-400"
                )

            ui.label(
                f"{exercise.sets} x {exercise.rep_min}-{exercise.rep_max} reps @ RIR "
                f"{exercise.target_rir} · rest {exercise.rest_seconds}s"
            ).classes(f"{TEXT_BODY} font-mono text-slate-300")

            ui.label(
                f"{exercise.target_load_kg:g} kg" if exercise.target_load_kg is not None
                else "No history yet — choose a load that reaches the target RIR."
            ).classes(f"{TEXT_MICRO} text-slate-400")

            if exercise.execution_notes:
                ui.label(exercise.execution_notes).classes(
                    f"{TEXT_MICRO} text-slate-400 leading-snug"
                )

            for note in exercise.constraint_feedback:
                constraint_feedback_row(view, exercise, note)

            # An explicit proposal, named and attributed — design-06 §6:
            # "every proposal names its evidence and requires acceptance."
            # Absent whenever there is nothing to propose (no evidence, no
            # history-established load, or feedback holding the block), which
            # is the ordinary state until strength history exists.
            if exercise.proposal is not None:
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_TIGHT} mt-{SPACE_TIGHT} px-{SPACE_SECTION} "
                    f"py-{SPACE_TIGHT} {RADIUS_CARD} border border-emerald-900/60 "
                    "bg-emerald-500/[0.07]"
                ):
                    ui.label(exercise.proposal.evidence_summary).classes(
                        f"{TEXT_MICRO} text-emerald-200/90 leading-snug"
                    )
                    ui.button(
                        "Accept",
                        on_click=lambda v=view, e=exercise: accept_proposal(v, e),
                    ).props("dense unelevated no-caps size=xs").classes(
                        "self-end bg-emerald-500/20 text-emerald-200"
                    )

    @ui.refreshable
    def workout_dialog_body() -> None:
        day = str(workout_target["day"] or "")
        session = workout_target["session"]
        if not day or session is None:
            ui.label("Nothing to show.").classes(f"{TEXT_BODY} text-slate-400")
            return
        view = workout_session_view(state, day, session)
        if view is None:
            ui.label("Not generated yet.").classes(f"{TEXT_BODY} text-slate-400")
            return

        with ui.element("div").classes(
            f"flex flex-row items-center justify-between gap-{SPACE_SECTION}"
        ):
            ui.label(f"{format_day_label(day, state.day_date_iso(day))} · {view.program_label}").classes(
                f"{TEXT_MICRO} font-mono uppercase tracking-widest text-slate-400"
            )
            # Completion source rides on the same adherence view the strip
            # itself reads — "whether it happened" is never a second answer
            # here (CLAUDE.md's three-stores rule).
            completion_mark(view.mark, build_day_marks(state, day, adherence))

        with ui.element("div").classes(
            f"flex flex-row flex-wrap items-baseline justify-between gap-{SPACE_BASE} mt-1"
        ):
            ui.label(view.name).classes(
                f"{TEXT_DISPLAY} font-semibold text-slate-100"
            )
            ui.label(f"{view.planned_duration_minutes} min").classes(
                f"{TEXT_BODY} text-slate-400"
            )

        with ui.element("div").classes(f"flex flex-col gap-{SPACE_BASE} mt-{SPACE_SECTION}"):
            for exercise in view.exercises:
                exercise_card(view, exercise)

        with ui.element("div").classes(f"flex flex-row justify-end mt-{SPACE_SECTION}"):
            ui.button(
                "Regenerate session",
                icon="refresh",
                on_click=regenerate_session,
            ).props(
                "flat dense no-caps size=sm"
                + (
                    " loading disable"
                    if state.regenerating_workout_session == view.session_id
                    else ""
                )
            ).classes("text-slate-400")

    with ui.dialog() as workout_dialog:
        with ui.element("div").classes(
            f"bg-slate-900 {RADIUS_PANEL} border border-slate-800 p-{SPACE_PAGE} "
            "w-[40rem] max-w-full max-h-[85vh] overflow-y-auto"
        ):
            workout_dialog_body()

    workout_handles = WorkoutHandles(
        state=state, open=open_workout, generate=generate_and_open
    )

    @ui.refreshable
    def today_view() -> None:
        sync_tab_label()
        day = state.viewed_day()
        if day is None:
            ui.label(
                "Nothing generated yet — use \"Generate Current Week\", or "
                "switch the header's week selector to a cached week."
            ).classes(f"{TEXT_HEAD} text-slate-400 p-{SPACE_PAGE}")
            return

        target = state.targets_for(day)
        totals = state.totals_for(day)
        bar_scale_limit = state.config["ui_settings"]["bar_scale_limit"]
        # Once per repaint, not once per card: `day_context` runs
        # `planning_config()`, which applies the training adjustments across
        # the whole week, and four cards asking for it would be four copies of
        # that work for one day's answer.
        context = day_context(state, day)
        # Same once-per-repaint rule as `day_context` directly above, and for
        # the same reason — see `build_day_marks`.
        marks = build_day_marks(state, day, adherence)

        with ui.element("div").classes(f"flex flex-col gap-{SPACE_SECTION} p-{SPACE_SECTION}"):
            day_nav(day)

            with ui.element("div").classes(f"flex flex-row flex-wrap items-baseline gap-{SPACE_BASE}"):
                ui.label(format_day_label(day, state.day_date_iso(day))).classes(
                    f"{TEXT_DISPLAY} font-semibold text-slate-200"
                )
                # A note, not a refusal: before the picker existed this case
                # replaced the whole panel, because a lone "today" view has
                # nothing to show if today isn't in the week. A browsable week
                # is still perfectly readable — it just isn't current, and
                # saying so once is enough.
                if not state.week_covers_today():
                    ui.label("this cached week doesn't cover today").classes(
                        f"{TEXT_BODY} text-amber-300/80 italic"
                    )

            context_strip(context, marks, workout_handles)

            with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT} max-w-md"):
                telemetry_bar(
                    totals["calories"],
                    float(target["calories"]),
                    height="10px",
                    bar_scale_limit=bar_scale_limit,
                )
                with ui.element("div").classes(
                    f"flex flex-row flex-wrap items-baseline gap-{SPACE_BASE}"
                ):
                    ui.label(
                        f"{totals['calories']:.0f} / {float(target['calories']):.0f} kcal"
                    ).classes(f"{TEXT_BODY} text-slate-400")
                    # Silent until something on the day is marked — see
                    # `MealAdherenceView.summary`. It sits beside the calorie
                    # figure rather than under it because the two are the
                    # same kind of statement about the day, one planned and
                    # one observed, which is the placement rule the fibre
                    # readout already follows in the header.
                    if marks.meals.summary:
                        ui.label(marks.meals.summary).classes(
                            f"{TEXT_MICRO} text-slate-400"
                        )

            views = state.slot_views()
            with ui.element("div").classes(f"flex flex-row flex-wrap gap-{SPACE_BASE}"):
                for meal_type in state.meal_types:
                    today_card(
                        views.get(slot_id(day, meal_type)),
                        meal_type,
                        context,
                        cards,
                        marks,
                    )

    return TodayHandles(today_view=today_view, bind_tab=bind_tab)

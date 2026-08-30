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
from typing import Callable, List, Optional

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
    WorkoutMarkView,
    day_context,
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
    link_line,
    telemetry_bar,
)
from week import MODE_COOK, MODE_LEFTOVER, humanize, slot_id

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


def training_row(context: DayContext, marks: Optional[DayMarks] = None) -> None:
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

        # Only worth printing once there are two sessions to add up —
        # under one it would just restate the chip beside it. This is the
        # figure `apply_training_adjustments` put onto the day's calorie
        # budget, which is the denominator of the bar directly above.
        if len(context.active_sessions) > 1:
            ui.label(f"+{context.total_burn_kcal:.0f} kcal on today's budget").classes(
                f"{TEXT_MICRO} text-slate-400 italic"
            )


def context_strip(context: DayContext, marks: Optional[DayMarks] = None) -> None:
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
            training_row(context, marks)


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

    def go(step: int = 0, day: Optional[str] = None, reset: bool = False) -> None:
        """Every day change goes through here, so none can forget the label.

        `today_view` is a single refreshable covering the whole panel, so one
        refresh repaints the picker, the strip, the bar and the cards
        together — there is no narrower topic worth carving out, since a day
        change genuinely invalidates all four.
        """
        if reset:
            state.select_day(None)
        elif day is not None:
            state.select_day(day)
        elif step:
            state.step_viewed_day(step)
        sync_tab_label()
        refreshables.refresh("today")

    def day_nav(day: str) -> None:
        days = state.days
        index = days.index(day) if day in days else 0
        today = state.today_day()

        with ui.element("div").classes(f"flex flex-row flex-wrap items-center gap-{SPACE_TIGHT}"):
            # Clamped, not wrapping — `step_viewed_day` stops at both ends
            # because the loaded plan holds exactly these seven days. A
            # disabled chevron is the honest edge; wrapping Sunday round to
            # Monday would silently pretend the week is a loop.
            ui.button(icon="chevron_left", on_click=lambda: go(step=-1)).props(
                f"dense flat size=sm {'disable' if index == 0 else ''}"
            ).classes("text-slate-400")

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

            ui.button(icon="chevron_right", on_click=lambda: go(step=1)).props(
                f"dense flat size=sm {'disable' if index == len(days) - 1 else ''}"
            ).classes("text-slate-400")

            # Only offered when it would actually do something: there is a
            # today in this week, and you are not on it.
            if state.week_covers_today() and not state.viewing_today():
                ui.button(
                    "Today", icon="today", on_click=lambda: go(reset=True)
                ).props("dense flat no-caps size=sm").classes("text-sky-300")

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

            context_strip(context, marks)

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

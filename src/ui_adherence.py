"""Marking what actually happened — the write half of adherence tracking.

CHANGE-QUEUE.md's adherence item (`future-ideas.md` 5b). Nothing in this app
observed whether a planned meal was eaten, skipped or swapped; the nearest
thing was the swap-with-favourite flow, which changes the *plan* rather than
recording a deviation from it.

**One concern, one factory**, per the `ui-work` skill's module rule. It is
its own module rather than more handles on `ui_cards.CardHandles` because
two unrelated surfaces raise these marks — the Daily View's meal cards and
its training strip — and only one of them is a card. Everything here is a
click handler plus its repaint; every decision about *what* a mark means
lives in `ui_state` (`mark_meal`, `mark_workout`, `meal_adherence_view`,
`workout_marks_view`), which is the module with tests.

**Marks persist on click, and do not stage.** Every grid edit in this app
waits for Save because it is an input to the next generation; a mark is not —
nothing generates differently because Thursday's lunch was skipped — so
there is nothing for the staged bar to hold, and a tick that vanished on
reload would be a control with no effect. Same test `set_target_mode` and
`accept_training_proposal` pass, from the storage side rather than the
config one.

**The repaint topic is `"adherence"`, its own**, rather than `"plan"`. The
two surfaces that draw a mark are `today.today_view` and `inspector.panel`,
and `"plan"` additionally rebuilds the 28-card canvas, the telemetry header
and the shopping panel — none of which show a mark, and all of which would
repaint on every click of a tick.
"""

from dataclasses import dataclass
from typing import Callable

from ui_context import UIContext
from ui_state import WorkoutMarkView


@dataclass
class AdherenceHandles:
    mark_meal: Callable
    mark_workout: Callable


def build_adherence(ctx: UIContext) -> AdherenceHandles:
    state = ctx.state
    repository = ctx.repository
    refreshables = ctx.refreshables

    async def mark_meal(day: str, meal_type: str, status: str) -> None:
        """Record (or clear) one meal's mark and repaint the two surfaces.

        `await`ed directly, never through `repository.run_sync()` — this runs
        on the event loop, and the write is a single small file rewrite in a
        worker thread, so there is nothing here to dispatch.
        """
        await state.mark_meal(repository, day, meal_type, status)
        refreshables.refresh("adherence")

    async def mark_workout(day: str, mark: WorkoutMarkView) -> None:
        """Toggle the manual completion mark for one declared session.

        The caller passes the whole `WorkoutMarkView` rather than an id
        because `mark_workout` needs to know whether the watch already
        recorded it — a session Garmin saw is refused rather than stored
        twice, and the view is what carries that answer.
        """
        await state.mark_workout(repository, day, mark)
        refreshables.refresh("adherence")

    return AdherenceHandles(mark_meal=mark_meal, mark_workout=mark_workout)

"""A small pub/sub registry for `@ui.refreshable` sections, plus the context
object that carries it (and `PlannerState`/the repository) across the
per-concern UI modules (`ui_cards`, `ui_telemetry`, `ui_shopping`,
`ui_plan`, `ui_review`, `ui_generation`).

`ui_app.py` is now a page *shell*: it builds a `UIContext`, hands it to each
module's `build_*(ctx)` factory, and wires the returned refreshables into one
topic registry. Every extracted function starts with the same three-line
shim — `state = ctx.state`, `REPOSITORY = ctx.repository`, `refreshables =
ctx.refreshables` — so a function's *body*, copied out of the old monolithic
`planner_page()`, reads exactly as it did there; only the `def` line and
those three aliases are new.

A call site says *what changed* ("plan", "targets", "catalog") instead of
naming every section that currently happens to depend on it. Adding a new
plan-dependent section is then one `on("plan", ...)` call at registration
time (now in `ui_app.py`, once every module's built), not one more
`.refresh()` line at every existing call site that already changes the plan
— which is exactly the maintenance burden a hand-written `refresh_all()`
used to be.

Multiple topics can and do share a section — `telemetry` is both its own
topic (a single-field edit that must not disturb the review dialog's focused
target input, see `ui_review.day_target_row`) and part of
`plan`/`targets`/`training` (edits that legitimately repaint more) — so
`refresh()` de-dupes rather than repainting a shared section twice per call.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List

from generation_jobs import GenerationJobs
from repository import LocalJSONRepository
from ui_state import PlannerState


class Refreshables:
    def __init__(self) -> None:
        self._topics: Dict[str, List[Callable[[], None]]] = {}

    def on(self, topic: str, *sections: Callable[[], None]) -> None:
        """Register `sections` (each an `@ui.refreshable`-wrapped function)
        under `topic`."""
        self._topics.setdefault(topic, []).extend(sections)

    def refresh(self, *topics: str) -> None:
        """Refresh every section registered under any of `topics`, each at
        most once."""
        seen = set()
        for topic in topics:
            for section in self._topics.get(topic, ()):
                if id(section) not in seen:
                    seen.add(id(section))
                    section.refresh()


@dataclass
class UIContext:
    """Everything one browser tab's widgets need, in one object.

    Created once per page load (`ui_app.planner_page`) and threaded through
    every `build_*(ctx)` factory. Field names match the module-level names
    the original monolith closed over (`state`, `REPOSITORY`) on purpose —
    each extracted function opens with `state = ctx.state; REPOSITORY =
    ctx.repository; refreshables = ctx.refreshables`, so its body needs no
    further rewriting to keep reading as it always did.
    """

    state: PlannerState
    repository: LocalJSONRepository
    refreshables: Refreshables
    # The one field here that is *not* per-tab, and deliberately so: the
    # registry is one object for the whole process, threaded through the same
    # context only because that is how a `build_*(ctx)` factory reaches
    # anything. `ui_generation.run_generation` claims against it so a browser
    # tab and an API client cannot both be generating over one
    # `week_plan.json` — `PlannerState.generating` is per-client and can see
    # neither the other tab nor the route.
    jobs: GenerationJobs

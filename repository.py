"""Storage boundary for everything the planner reads and writes.

The planner used to `open()` and `json.load()` its own files inline, which
meant the file layout was baked into the business logic in a dozen places. All
of that now goes through a `PlanRepository`, so swapping local JSON for a
backend service is a matter of writing one more subclass — no caller changes.

**Every method is `async` on purpose, including the local file
implementation.** The interface is written for the future backend (which will
receive asynchronous webhook pushes and must not block an event loop), not for
today's filesystem: making the local implementation sync "because files are
fast" would put an `await` boundary in exactly the wrong place and force every
caller to change again later. `LocalJSONRepository` therefore does its blocking
`open()`/`json` work in a worker thread via `asyncio.to_thread`, so awaiting it
genuinely yields to the loop rather than just wrapping a blocking call in a
coroutine.

The repository deliberately deals in plain dicts/lists, never in `WeekPlan` or
`WeekSpec`: `planner` imports this module, so importing planner's models back
here would be a cycle. Callers do their own `WeekPlan.model_validate(...)`,
which also keeps schema validation a business-logic concern rather than a
storage one.
"""

import abc
import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, List, Optional, TypeVar

DEFAULT_CONFIG_FILE = "config.json"
DEFAULT_HISTORY_FILE = "meal_history.json"
DEFAULT_WEEK_PLAN_FILE = "week_plan.json"

T = TypeVar("T")


class PlanRepository(abc.ABC):
    """Everything the planner needs to persist, as an async interface.

    Implementations must tolerate missing data the way the local files always
    have: an absent history is an empty list, an absent week plan is None. A
    missing *config* is an error — there is no sensible default for it, and
    silently planning against `{}` would be worse than failing loudly.
    """

    @abc.abstractmethod
    async def load_config(self) -> dict:
        """Targets, dietary rules, styles, cuisines. Raises if unavailable."""

    @abc.abstractmethod
    async def load_history(self) -> List[dict]:
        """Past days, oldest first. Empty when nothing has been generated yet.

        This is what seeds style/cuisine rotation and the recent-protein list,
        so an empty result is a valid cold start, not a failure.
        """

    @abc.abstractmethod
    async def save_history(self, history: List[dict]) -> None:
        """Replace the stored history with `history` (already trimmed by the
        caller to its max length)."""

    @abc.abstractmethod
    async def load_week_plan(self) -> Optional[dict]:
        """The last generated week as a raw dict, or None if there isn't one.

        Returned unvalidated so this module stays independent of planner's
        Pydantic models; callers run `WeekPlan.model_validate` themselves.
        """

    @abc.abstractmethod
    async def save_week_plan(self, week_plan: dict) -> None:
        """Store the generated week, replacing any previous one.

        Not in the original four methods, but the cached week plan is the other
        half of `load_week_plan` and the CLI's `--use-cached-plan` flag reads
        what this writes — leaving it out would have left a bare `json.dump`
        behind in planner.py, which is the thing this module exists to remove.
        """


class LocalJSONRepository(PlanRepository):
    """The current on-disk layout: three JSON files next to the code.

    Paths are constructor arguments rather than module constants so tests (and
    a second week in another directory) don't have to chdir. Writes go to a
    temporary file and are then renamed: a crash mid-write would otherwise
    leave truncated JSON where meal_history.json used to be, and history is not
    reproducible once lost.
    """

    def __init__(
        self,
        config_path: str = DEFAULT_CONFIG_FILE,
        history_path: str = DEFAULT_HISTORY_FILE,
        week_plan_path: str = DEFAULT_WEEK_PLAN_FILE,
    ) -> None:
        self.config_path = config_path
        self.history_path = history_path
        self.week_plan_path = week_plan_path

    # -- PlanRepository ----------------------------------------------------

    async def load_config(self) -> dict:
        config = await asyncio.to_thread(self._read_json, self.config_path)
        if config is None:
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        return config

    async def load_history(self) -> List[dict]:
        return await asyncio.to_thread(self._read_json, self.history_path) or []

    async def save_history(self, history: List[dict]) -> None:
        await asyncio.to_thread(self._write_json, self.history_path, history)

    async def load_week_plan(self) -> Optional[dict]:
        return await asyncio.to_thread(self._read_json, self.week_plan_path)

    async def save_week_plan(self, week_plan: dict) -> None:
        await asyncio.to_thread(self._write_json, self.week_plan_path, week_plan)

    # -- blocking helpers, only ever called in a worker thread --------------

    @staticmethod
    def _read_json(path: str) -> Any:
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: str, payload: Any) -> None:
        temporary = f"{path}.tmp"
        with open(temporary, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(temporary, path)


def run_sync(awaitable: Awaitable[T]) -> T:
    """Run one coroutine to completion from synchronous code.

    The bridge for callers that are not (yet) async themselves — today the
    CLI's `main()`, which runs top-to-bottom in a plain thread with no loop.
    `asyncio.run` covers that, but it raises if a loop is already running in
    this thread — so when there is one, the coroutine is handed to a scratch
    thread with a loop of its own. That path costs a thread, and exists only so
    an embedded caller never deadlocks; the normal case is the first branch.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)  # type: ignore[arg-type]

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, awaitable).result()  # type: ignore[arg-type]

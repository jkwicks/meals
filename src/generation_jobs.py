"""Generation runs in flight: the single-flight guard, and the job records.

A week takes 30s-3min *per meal type* to generate, so the API cannot answer
`POST /api/weeks/current/generate` with the finished week the way every `GET`
in `api.py` answers with its record — nothing survives a request open that
long. This module is the other half of that route: the run happens on a
background task and the caller polls a job id.

**Polling rather than SSE or a WebSocket, and the event rate is why.**
`generate_week_plan` fires `progress_callback` once per *meal type*
(`planner.py`, in the stage loop) — at most four times a run, and three on
the shipped config, since `week_defaults.snack` is `skip` — plus a handful of
`note_callback` strings. That is on the order of a dozen events across a
quarter of an hour, which is not a streaming problem: a client polling every
few seconds sees every one of them, from `curl`, a shell script or a phone
shortcut, and a dropped connection costs nothing because the record is here
rather than on the wire.

The deciding argument is that the other two designs need this module anyway.
SSE and a WebSocket both lose the run's history when the connection drops
while the run keeps going, so both have to buffer events server-side and
support resuming from an offset — which is this registry with a stream in
front of it. Polling is the substrate, not a third peer, and a streaming
route added later reads the same records.

**The finished week is deliberately not on the job.** `run_generation` saves
it through the repository before the job completes, so `GET /api/weeks/…`
already answers for it — a copy on the job would be a second answer to one
question, free to disagree with the file the moment anything else writes a
week. The job carries what the *run* knew and the stored plan does not: which
stages started, the portion-adjustment notes, and why it stopped.

**The guard is the second thing here and the reason `ui_generation.py`
imports this module.** `PlannerState.generating` is per-client — its own
comment says two tabs generating at once "would race to overwrite the same
week_plan.json" — so it cannot see an API run at all, and an API run cannot
see it. `GenerationJobs.claim()` is the one flag both consult. It is a plain
field rather than an `asyncio.Lock` because claiming must *fail* rather than
queue: a second Generate is a mistake to report, not work to line up behind.
No lock primitive is needed for correctness either — `claim` reaches its
assignment with no `await` in between, and a single event loop cannot
interleave anywhere else.

That single loop is also this module's one real limit, and it is worth stating
rather than discovering: the registry lives in memory in one process, which is
exactly what NiceGUI serves today (`ui_app.py` runs `ui.run(..., reload=False)`
on one Uvicorn worker, and `api.py`'s router mounts onto that same app). Put
the app behind two workers, or on the future backend `repository.py` was shaped
for, and this guard stops being one — the claim would have to move to where the
plan lives, through the repository, the same way every other piece of shared
state in this app already does.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"

# Finished jobs are kept so a client that polls late still gets its answer,
# and dropped oldest-first so a long-lived server doesn't accumulate them.
# Twenty is generous for a single-user app generating at most two weeks at a
# time; the number matters only in that there is one.
MAX_RETAINED_JOBS = 20

SOURCE_API = "api"
SOURCE_UI = "ui"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class GenerationJob:
    """One generation run, as a client polling `/api/jobs/{id}` sees it."""

    id: str
    week_identifier: str
    source: str
    started_at: str
    status: str = JOB_RUNNING
    # Every meal type this run will ask for, known before the first call
    # (`planner.meal_type_order`), so a client can render "2 of 4" rather than
    # a spinner with no denominator.
    stages: List[str] = field(default_factory=list)
    # Stages *started*, not banked — `progress_callback` fires before each
    # call, which is the same off-by-one the UI's own stage checklist turns
    # on: nothing marks the last stage complete but the run returning. So
    # `stages_started` reaching `len(stages)` is not completion; `status` is.
    stages_started: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # Copied off the returned WeekPlan. A per-meal-type failure does not fail
    # the run (see CLAUDE.md's "A failed meal must not fail the week"), so a
    # succeeded job can still carry these and a client has to read both.
    failures: Dict[str, str] = field(default_factory=dict)
    # Set only when the whole run came apart. `validation_errors` is the one
    # failure worth separating: it means `validate_week` rejected the grid
    # before a single API call was paid for, so it is the caller's config to
    # fix rather than a provider to retry against.
    error: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)
    finished_at: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "week_identifier": self.week_identifier,
            "source": self.source,
            "status": self.status,
            "stages": list(self.stages),
            "stages_started": list(self.stages_started),
            "notes": list(self.notes),
            "failures": dict(self.failures),
            "error": self.error,
            "validation_errors": list(self.validation_errors),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class GenerationJobs:
    """The process's generation runs, and the flag saying one is in flight.

    Constructed once per process (`ui_app.GENERATION_JOBS`) and handed to
    `build_api_router`, the same way `REPOSITORY` is — explicit, so a test can
    build a throwaway registry with no shared state to reset between cases.
    """

    def __init__(self, max_retained: int = MAX_RETAINED_JOBS) -> None:
        self._jobs: Dict[str, GenerationJob] = {}
        self._order: List[str] = []
        self._active: Optional[GenerationJob] = None
        self._max_retained = max_retained
        # Strong references to the spawned tasks. `asyncio.create_task` keeps
        # only a weak one, so a task nobody holds can be collected mid-run —
        # per-instance, like everything else here, because a class attribute
        # would be shared by every registry a test constructs.
        self._tasks: set = set()

    # ---- the guard ------------------------------------------------------

    @property
    def active(self) -> Optional[GenerationJob]:
        """The run in flight, whoever started it, or None."""
        return self._active

    def claim(self, source: str, week_identifier: str = "current") -> Optional[GenerationJob]:
        """Take the single-flight claim, or None if something already holds it.

        Returns a job either way it is used: the API polls it, and the UI
        simply holds it and reports through its own progress dialog. Both are
        recorded, so `GET /api/jobs` describes every run in the process rather
        than only the ones it started — a browser tab generating is exactly
        what a 409 needs to be able to name.
        """
        if self._active is not None:
            return None
        job = GenerationJob(
            id=uuid.uuid4().hex[:12],
            week_identifier=week_identifier,
            source=source,
            started_at=_now(),
        )
        self._active = job
        self._remember(job)
        return job

    def release(self, job: GenerationJob, status: str = JOB_SUCCEEDED) -> None:
        """Drop the claim and stamp the job finished, if it is still running.

        Idempotent, and it never demotes a job the run already marked: a UI
        run reports its own outcome through `ui.notify` and releases with the
        default, so `fail()` having already been called must win over that.
        """
        if job.status == JOB_RUNNING:
            job.status = status
            job.finished_at = _now()
        if self._active is job:
            self._active = None

    # ---- what a run reports back ---------------------------------------

    def note(self, job: GenerationJob, message: str) -> None:
        job.notes.append(message)

    def stage_started(self, job: GenerationJob, meal_type: str) -> None:
        job.stages_started.append(meal_type)

    def fail(
        self,
        job: GenerationJob,
        error: str,
        validation_errors: Optional[List[str]] = None,
    ) -> None:
        job.status = JOB_FAILED
        job.error = error
        job.validation_errors = list(validation_errors or [])
        job.finished_at = _now()

    # ---- reading ---------------------------------------------------------

    def get(self, job_id: str) -> Optional[GenerationJob]:
        return self._jobs.get(job_id)

    def recent(self, limit: Optional[int] = None) -> List[GenerationJob]:
        """Newest first, which is the order a client asking "what happened"
        wants and the reverse of insertion order."""
        jobs = [self._jobs[job_id] for job_id in reversed(self._order)]
        return jobs[:limit] if limit else jobs

    # ---- running one -----------------------------------------------------

    async def run(
        self,
        job: GenerationJob,
        work: Callable[[], Awaitable[Any]],
        on_result: Optional[Callable[[GenerationJob, Any], None]] = None,
    ) -> None:
        """Await `work()` on behalf of `job`, recording however it ends.

        Split from `start` so the whole path is testable without a task: a
        test awaits this directly and asserts on the record, where asserting
        on a spawned task means waiting for a scheduler.
        """
        try:
            result = await work()
        except asyncio.CancelledError:
            self.fail(job, "Generation was cancelled.")
            self.release(job, JOB_FAILED)
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            # `planner.WeekNotValidError.errors` is the list `validate_week`
            # returned. Duck-typed rather than imported, to keep this module
            # free of `planner` — but checked for being a list, because
            # pydantic's own `ValidationError.errors` is a *method*, and
            # `list()` of a bound method raises inside the handler that exists
            # to stop an exception escaping. instructor surfaces exactly that
            # exception when a model can't satisfy the schema, so it is a
            # reachable path and not a hypothetical.
            errors = getattr(exc, "errors", None)
            if not isinstance(errors, list):
                errors = []
            self.fail(job, f"{type(exc).__name__}: {exc}", errors)
            self.release(job, JOB_FAILED)
            return
        if on_result is not None:
            on_result(job, result)
        self.release(job, JOB_SUCCEEDED)

    def start(
        self,
        job: GenerationJob,
        work: Callable[[], Awaitable[Any]],
        on_result: Optional[Callable[[GenerationJob, Any], None]] = None,
    ) -> "asyncio.Task[None]":
        """Spawn `run` as a background task and hand the caller back the task.

        The route returns the moment this is called — that is the whole point
        of the job id — so the task is deliberately not awaited. It is
        returned rather than dropped because a bare `create_task` reference
        can be garbage-collected mid-run.
        """
        task = asyncio.create_task(self.run(job, work, on_result))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ---- internals -------------------------------------------------------

    def _remember(self, job: GenerationJob) -> None:
        self._jobs[job.id] = job
        self._order.append(job.id)
        while len(self._order) > self._max_retained:
            # Oldest *finished* job first. The active one is skipped rather
            # than evicted — a client polling a run still in flight must not
            # be told it never existed — and skipped in place rather than
            # re-appended, which would move a running job to the front of
            # `recent()`.
            evictable = next(
                (
                    index
                    for index, job_id in enumerate(self._order)
                    if self._jobs.get(job_id) is not self._active
                ),
                None,
            )
            if evictable is None:
                break
            self._jobs.pop(self._order.pop(evictable), None)

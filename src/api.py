"""The read-only API boundary — phase 5 of `ui-redesign.md`.

`build_api_router(repository)` returns a plain `fastapi.APIRouter` mounted
onto NiceGUI's own FastAPI app (`nicegui.app` *is* a `FastAPI` instance, so
no second server or port is involved — see `ui_app.py`'s `include_router`
call). It is a thin read layer over `repository.py` and `planner.py`: every
route calls an existing async repository method or an existing pure
function and returns the answer, never computes one — see CLAUDE.md's "The
API boundary (read-only)" section for what it deliberately does not expose
and why.

`build_api_router` takes a `repository` argument rather than reading a
module-level singleton, the same reason every `ui_*.py` `build_*(ctx)`
factory takes its dependencies explicitly: it makes the router constructible
against a throwaway repository in a test, with no shared state to reset
between tests.
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from generation_jobs import SOURCE_API, GenerationJob, GenerationJobs
from planner import (
    WeekPlan,
    generate_and_store_week,
    hydrate_config,
    load_config_with_models,
    meal_type_order,
)
from repository import PlanRepository, catalog_matches


class CatalogRecipe(BaseModel):
    id: str
    content_key: str
    recipe: Dict[str, Any]
    is_favorite: bool
    source: str
    added_at: str
    updated_at: str


class BiometricsResponse(BaseModel):
    weigh_ins: List[Dict[str, Any]]
    daily_actuals: List[Dict[str, Any]]
    # Mirrors the third stored list rather than computing anything from it —
    # `readiness_log` is sleep and HRV, which nothing in this app turns into a
    # target, and a route that summarised it would be a route free to disagree
    # with the Settings page that reads the same rows.
    readiness_log: List[Dict[str, Any]]
    # And the fourth, on the same terms. `activity_log` is what the schedule
    # proposal reads (`nutrition_engine.propose_training_schedule`), and this
    # route deliberately does not run it: a proposal is a diff against a
    # *staged* schedule and an accept/dismiss state, which is a session
    # concept — the same line every other route here already draws around
    # `PlannerState`.
    activity_log: List[Dict[str, Any]]
    latest: Optional[Dict[str, Any]] = None


class TargetsResponse(BaseModel):
    weekly_schedule: Dict[str, Dict[str, Any]]
    dynamic_basis: Optional[Dict[str, Any]] = None


class GenerateWeekRequest(BaseModel):
    """The two things `default_week_spec` accepts beyond config, and nothing
    else. Everything that reshapes a *grid* — batch toggles, leftover links,
    skip estimates, target overrides — is a staged session edit living on
    `PlannerState`, which is the same line every read route here already draws
    around it: a route may name what the CLI's flags name, because those are
    arguments to a fresh week rather than edits to somebody's open tab."""

    week_start: Optional[str] = None
    servings: Optional[int] = None


class GenerationJobResponse(BaseModel):
    """A run in flight or finished, exactly as `GenerationJob` records it.

    Mirrors the dataclass rather than summarising it, the same terms
    `BiometricsResponse` mirrors its four stored lists on: a field computed
    here would be a field free to disagree with the progress dialog reading
    the same run.
    """

    id: str
    week_identifier: str
    source: str
    status: str
    stages: List[str]
    stages_started: List[str]
    notes: List[str]
    failures: Dict[str, str]
    error: Optional[str] = None
    validation_errors: List[str]
    started_at: str
    finished_at: Optional[str] = None


def build_api_router(repository: PlanRepository, jobs: GenerationJobs) -> APIRouter:
    """The `/api` routes.

    `jobs` is required rather than defaulted for the same reason `repository`
    is, and one more: it carries the single-flight claim `ui_generation.py`
    also takes, so a router handed its own fresh registry would be a guard
    that silently guards nothing. `ui_app.py` passes the process's one
    instance; a test passes a throwaway.
    """
    router = APIRouter(prefix="/api")

    @router.get("/weeks/{week_identifier}", response_model=WeekPlan)
    async def get_week(week_identifier: Literal["current", "next"]) -> WeekPlan:
        raw = await repository.load_week_plan(week_identifier)
        if raw is None:
            raise HTTPException(
                status_code=404, detail=f"No '{week_identifier}' week plan saved."
            )
        return WeekPlan.model_validate(raw)

    @router.get("/recipes", response_model=List[CatalogRecipe])
    async def get_recipes(
        favorite: Optional[bool] = None,
        meal_type: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[CatalogRecipe]:
        catalog = await repository.load_recipe_catalog()
        # `repository.catalog_matches`, not a copy of it — the Library
        # destination's grid filters the same three ways over the same
        # records, and two inline copies of that would disagree silently
        # (a differently-filtered list, never an error). Closed in v0.31.0;
        # CHANGE-QUEUE.md's "Verified closed" table has the reasoning.
        matches = [
            entry
            for entry in catalog
            if catalog_matches(
                entry,
                favorites_only=bool(favorite),
                meal_type=meal_type,
                search=search,
            )
        ]
        return [CatalogRecipe.model_validate(entry) for entry in matches]

    @router.get("/history", response_model=List[Dict[str, Any]])
    async def get_history() -> List[Dict[str, Any]]:
        return await repository.load_history()

    @router.get("/biometrics", response_model=BiometricsResponse)
    async def get_biometrics() -> BiometricsResponse:
        data = await repository.load_biometrics()
        latest = await repository.get_latest_biometrics()
        return BiometricsResponse(
            weigh_ins=data["weigh_ins"],
            daily_actuals=data["daily_actuals"],
            readiness_log=data["readiness_log"],
            activity_log=data["activity_log"],
            latest=latest,
        )

    @router.get("/targets", response_model=TargetsResponse)
    async def get_targets() -> TargetsResponse:
        config = await load_config_with_models(repository)
        config = await hydrate_config(config, repository)
        return TargetsResponse(
            weekly_schedule=config["weekly_schedule"],
            dynamic_basis=config.get("dynamic_basis"),
        )

    # ---- generation: the one write, and it answers with a job -----------
    #
    # The first non-GET route here, and the reason the read-only boundary
    # ends rather than bends: a week takes 30s-3min per meal type, so this
    # cannot answer with the thing it makes. It starts the run, hands back a
    # job id and returns; `GET /api/jobs/{id}` is where the answer arrives,
    # and the finished week itself comes back through `GET /api/weeks/{id}`
    # above, which already reads what the run stored. See `generation_jobs`
    # for why polling rather than SSE or a WebSocket.

    def _job_response(job: GenerationJob) -> GenerationJobResponse:
        return GenerationJobResponse.model_validate(job.as_dict())

    @router.post(
        "/weeks/{week_identifier}/generate",
        response_model=GenerationJobResponse,
        status_code=202,
    )
    async def start_week_generation(
        week_identifier: Literal["current", "next"],
        request: Optional[GenerateWeekRequest] = None,
    ) -> GenerationJobResponse:
        options = request or GenerateWeekRequest()

        # Claimed before anything is awaited, the same rule
        # `ui_generation.run_generation` states for its own flag: every await
        # below is a point where a second request gets its turn.
        job = jobs.claim(SOURCE_API, week_identifier)
        if job is None:
            active = jobs.active
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A generation started by the {active.source} at "
                    f"{active.started_at} is already running"
                    f" (job {active.id})."
                    if active
                    else "A generation is already running."
                ),
            )

        # Loaded here rather than inside the task so the 202 already names the
        # stages a client is about to watch — `meal_type_order` is what
        # `generate_week_plan` itself loops over, not a second opinion about
        # which meal types this week has.
        try:
            config = await load_config_with_models(repository)
            job.stages = meal_type_order(config)
        except Exception as exc:  # noqa: BLE001 - recorded on the job, then raised
            jobs.fail(job, f"{type(exc).__name__}: {exc}")
            jobs.release(job, job.status)
            raise HTTPException(status_code=500, detail=f"Could not load config: {exc}")

        async def work() -> WeekPlan:
            return await generate_and_store_week(
                repository,
                config,
                week_identifier=week_identifier,
                week_start=options.week_start,
                servings=options.servings,
                # `progress_callback` is (meal_type, cooks) and fires *before*
                # each stage's call; `cooks` is already implied by the week the
                # run stores, so only the stage name is kept.
                progress_callback=lambda meal_type, cooks: jobs.stage_started(job, meal_type),
                note_callback=lambda message: jobs.note(job, message),
            )

        def adopt(finished: GenerationJob, week_plan: WeekPlan) -> None:
            # A per-meal-type failure does not fail the run, so these ride on
            # a *succeeded* job — same contract `WeekPlan.failures` already
            # has with the UI's own warning list.
            finished.failures = dict(week_plan.failures)

        jobs.start(job, work, adopt)
        return _job_response(job)

    @router.get("/jobs", response_model=List[GenerationJobResponse])
    async def list_generation_jobs(limit: Optional[int] = None) -> List[GenerationJobResponse]:
        return [_job_response(job) for job in jobs.recent(limit)]

    @router.get("/jobs/{job_id}", response_model=GenerationJobResponse)
    async def get_generation_job(job_id: str) -> GenerationJobResponse:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"No job '{job_id}'.")
        return _job_response(job)

    return router

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

from planner import WeekPlan, hydrate_config, load_config_with_models
from repository import PlanRepository


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
    latest: Optional[Dict[str, Any]] = None


class TargetsResponse(BaseModel):
    weekly_schedule: Dict[str, Dict[str, Any]]
    dynamic_basis: Optional[Dict[str, Any]] = None


def build_api_router(repository: PlanRepository) -> APIRouter:
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
        needle = (search or "").strip().lower()
        matches = [
            entry
            for entry in catalog
            if (not favorite or entry.get("is_favorite"))
            and (not meal_type or entry.get("recipe", {}).get("meal_type") == meal_type)
            and (not needle or needle in entry.get("recipe", {}).get("name", "").lower())
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

    return router

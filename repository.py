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
import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Awaitable, List, Optional, TypeVar

DEFAULT_CONFIG_FILE = "config.json"
DEFAULT_HISTORY_FILE = "meal_history.json"
DEFAULT_WEEK_PLAN_FILE = "week_plan.json"
DEFAULT_RECIPE_CATALOG_FILE = "recipes_master.json"

T = TypeVar("T")


def recipe_content_key(recipe: dict) -> str:
    """Identity for "is this the same recipe" purposes: name + ingredient
    composition, not object identity or a generated id.

    This is what makes favoriting idempotent across regenerations — cooking
    the same dish again next month (same name, same grams of each
    ingredient) resolves to the same catalog entry rather than a duplicate,
    while a same-named dish with different ingredients is treated as a
    genuinely different recipe. Quantities are rounded to 2dp before hashing
    so float noise from portion trimming can't split one recipe into two
    entries.
    """
    name = (recipe.get("name") or "").strip().lower()
    ingredients = sorted(
        (
            (ingredient.get("name") or "").strip().lower(),
            round(float(ingredient.get("quantity_g") or 0), 2),
        )
        for ingredient in recipe.get("ingredients", [])
    )
    payload = json.dumps([name, ingredients], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


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
    async def load_week_plan(self, week_identifier: str = "current") -> Optional[dict]:
        """The last generated week as a raw dict, or None if there isn't one.

        `week_identifier` picks which cached week — `"current"` (the default,
        and the only one that existed before multi-week support) or `"next"`
        — since the app now keeps two weeks on disk at once rather than
        overwriting a single file. Returned unvalidated so this module stays
        independent of planner's Pydantic models; callers run
        `WeekPlan.model_validate` themselves.
        """

    @abc.abstractmethod
    async def save_week_plan(self, week_plan: dict, week_identifier: str = "current") -> None:
        """Store the generated week under `week_identifier`, replacing any
        previous plan stored under that same identifier.

        Not in the original four methods, but the cached week plan is the other
        half of `load_week_plan` and the CLI's `--use-cached-plan` flag reads
        what this writes — leaving it out would have left a bare `json.dump`
        behind in planner.py, which is the thing this module exists to remove.
        """

    @abc.abstractmethod
    async def load_recipe_catalog(self) -> List[dict]:
        """Every recipe ever favorited or imported, oldest first — the single
        store recipe content lives in outside of the current `week_plan.json`.

        Records look like `{id, content_key, recipe, is_favorite, source,
        added_at, updated_at}`. `week_plan.json` and `meal_history.json` are
        not this store: the former is overwritten every generation and the
        latter keeps only lean per-day summaries, so a recipe that isn't
        favorited or imported has no life beyond the week it was cooked in.
        """

    @abc.abstractmethod
    async def get_favorites(self) -> List[dict]:
        """The subset of the catalog with `is_favorite` true."""

    @abc.abstractmethod
    async def toggle_favorite(self, recipe: dict) -> bool:
        """Flip favorite status for the catalog entry matching `recipe`'s
        name + ingredients (see `recipe_content_key`).

        If no matching entry exists yet, one is created (`source="favorited"`,
        already favorited) — the first click on a card's bookmark is both
        "add to catalog" and "favorite it" in one step. An existing entry is
        never removed by this call, only its flag flipped, so un-favoriting a
        recipe never drops it from the catalog (see `delete_catalog_recipe`
        for actual removal). Returns the new `is_favorite` state.
        """

    @abc.abstractmethod
    async def import_recipe(self, recipe: dict, favorite: bool = False) -> dict:
        """Add `recipe` to the catalog (`source="imported"`), or fold into a
        matching existing entry (see `recipe_content_key`) if one exists.

        `favorite` can only ever turn an existing entry's flag on, never off
        — an import is not how a recipe gets un-favorited. Returns the
        stored record.
        """

    @abc.abstractmethod
    async def rename_catalog_recipe(self, recipe_id: str, name: str) -> Optional[dict]:
        """Rename a catalog entry's recipe in place. Returns the updated
        record, or None if `recipe_id` isn't in the catalog — a favorite
        deleted in another tab is a no-op here, not an error, the same
        tolerance `load_history`/`load_week_plan` extend to a missing file.
        """

    @abc.abstractmethod
    async def delete_catalog_recipe(self, recipe_id: str) -> None:
        """Remove an entry from the catalog outright. A no-op if already
        gone. Distinct from `toggle_favorite`'s un-favorite, which keeps the
        entry — this is for discarding a bad import or a mistaken save."""


class LocalJSONRepository(PlanRepository):
    """The current on-disk layout: four JSON files next to the code.

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
        recipe_catalog_path: str = DEFAULT_RECIPE_CATALOG_FILE,
    ) -> None:
        self.config_path = config_path
        self.history_path = history_path
        self.week_plan_path = week_plan_path
        self.recipe_catalog_path = recipe_catalog_path

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

    async def load_week_plan(self, week_identifier: str = "current") -> Optional[dict]:
        return await asyncio.to_thread(
            self._read_json, self._week_plan_path(week_identifier)
        )

    async def save_week_plan(self, week_plan: dict, week_identifier: str = "current") -> None:
        await asyncio.to_thread(
            self._write_json, self._week_plan_path(week_identifier), week_plan
        )

    async def load_recipe_catalog(self) -> List[dict]:
        return await asyncio.to_thread(self._read_json, self.recipe_catalog_path) or []

    async def get_favorites(self) -> List[dict]:
        catalog = await self.load_recipe_catalog()
        return [record for record in catalog if record.get("is_favorite")]

    async def toggle_favorite(self, recipe: dict) -> bool:
        return await asyncio.to_thread(self._toggle_favorite, recipe)

    async def import_recipe(self, recipe: dict, favorite: bool = False) -> dict:
        return await asyncio.to_thread(self._import_recipe, recipe, favorite)

    async def rename_catalog_recipe(self, recipe_id: str, name: str) -> Optional[dict]:
        return await asyncio.to_thread(self._rename_catalog_recipe, recipe_id, name)

    async def delete_catalog_recipe(self, recipe_id: str) -> None:
        await asyncio.to_thread(self._delete_catalog_recipe, recipe_id)

    def _week_plan_path(self, week_identifier: str) -> str:
        """File for one named week.

        `"current"` maps to the original single-file layout
        (`self.week_plan_path`, i.e. `week_plan.json`) rather than
        `week_plan_current.json`, so an existing install with a cached week
        already on disk needs no migration and no data movement the first
        time this runs. Every other identifier — `"next"`, or a week-start
        date — gets its own `week_plan_<identifier>.json` alongside it.
        """
        if week_identifier == "current":
            return self.week_plan_path
        return f"week_plan_{week_identifier}.json"

    # -- blocking helpers, only ever called in a worker thread --------------

    def _find_catalog_entry(self, catalog: List[dict], recipe: dict) -> Optional[dict]:
        key = recipe_content_key(recipe)
        return next((r for r in catalog if r.get("content_key") == key), None)

    def _toggle_favorite(self, recipe: dict) -> bool:
        catalog = self._read_json(self.recipe_catalog_path) or []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = self._find_catalog_entry(catalog, recipe)
        if existing is not None:
            existing["is_favorite"] = not existing.get("is_favorite", False)
            existing["updated_at"] = now
            new_state = existing["is_favorite"]
        else:
            catalog.append(
                {
                    "id": uuid.uuid4().hex,
                    "content_key": recipe_content_key(recipe),
                    "recipe": recipe,
                    "is_favorite": True,
                    "source": "favorited",
                    "added_at": now,
                    "updated_at": now,
                }
            )
            new_state = True
        self._write_json(self.recipe_catalog_path, catalog)
        return new_state

    def _import_recipe(self, recipe: dict, favorite: bool) -> dict:
        catalog = self._read_json(self.recipe_catalog_path) or []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = self._find_catalog_entry(catalog, recipe)
        if existing is not None:
            if favorite and not existing.get("is_favorite", False):
                existing["is_favorite"] = True
                existing["updated_at"] = now
            record = existing
        else:
            record = {
                "id": uuid.uuid4().hex,
                "content_key": recipe_content_key(recipe),
                "recipe": recipe,
                "is_favorite": favorite,
                "source": "imported",
                "added_at": now,
                "updated_at": now,
            }
            catalog.append(record)
        self._write_json(self.recipe_catalog_path, catalog)
        return record

    def _rename_catalog_recipe(self, recipe_id: str, name: str) -> Optional[dict]:
        catalog = self._read_json(self.recipe_catalog_path) or []
        updated = None
        for record in catalog:
            if record.get("id") == recipe_id:
                record["recipe"]["name"] = name
                record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                updated = record
                break
        if updated is not None:
            self._write_json(self.recipe_catalog_path, catalog)
        return updated

    def _delete_catalog_recipe(self, recipe_id: str) -> None:
        catalog = self._read_json(self.recipe_catalog_path) or []
        remaining = [record for record in catalog if record.get("id") != recipe_id]
        if len(remaining) != len(catalog):
            self._write_json(self.recipe_catalog_path, remaining)

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

"""Tests for `src/api.py` — the read-only API boundary (phase 5 of
`ui-redesign.md`).

Same no-network/no-model/no-clock discipline as the rest of the suite:
`fastapi.testclient.TestClient` runs entirely in-process over ASGI against a
throwaway `FastAPI()` app (never NiceGUI's own), and `LocalJSONRepository`
points `data_dir`/`biometrics_path` at a temp directory while leaving
`config_dir` at its default — the real shipped `config/`, already proven
valid by `test_config_layout.py` — so there's no hand-built `AppConfig`
fixture to keep in sync with the real schema. Fixture data is seeded through
the repository's own `run_sync`-wrapped save methods, the same convention
`test_sync_service.py`/`test_history.py` use, rather than hand-written JSON.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402
from api import build_api_router  # noqa: E402
from generation_jobs import (  # noqa: E402
    JOB_FAILED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    SOURCE_API,
    SOURCE_UI,
    GenerationJobs,
)
from planner import (  # noqa: E402
    CookEvent,
    Ingredient,
    Recipe,
    WeekNotValidError,
    WeekPlan,
)
from repository import (  # noqa: E402
    CATALOG_MEAL_TYPE_ANY,
    LocalJSONRepository,
    catalog_matches,
    run_sync,
)
from week import MODE_COOK, SlotSpec  # noqa: E402

DAYS = ["Monday", "Tuesday", "Wednesday"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]


def make_recipe(name="Green Chicken Curry", meal_type="dinner", servings=2, grams=200.0):
    return Recipe(
        name=name,
        meal_type=meal_type,
        ingredients=[
            Ingredient(
                name="Chicken breast", quantity_g=grams, nova_group=1,
                calories=grams * 1.65, protein_g=grams * 0.31,
                net_carbs_g=0.0, fat_g=grams * 0.036,
            )
        ],
        instructions=["Cook it."],
        prep_time_minutes=30,
        servings=servings,
    )


def make_week_plan_dict() -> dict:
    slots = [
        SlotSpec(day=day, meal_type=meal_type, mode=MODE_COOK)
        for day in DAYS
        for meal_type in MEAL_TYPES
    ]
    event = CookEvent(
        slot_id="Monday:dinner", day="Monday", meal_type="dinner",
        portions=2, eaten_by=["Monday:dinner"], recipe=make_recipe(),
    )
    return {
        "days": DAYS,
        "servings_per_meal": 2,
        "generated_at": "2026-08-18T09:00:00",
        "week_start_date": "2026-08-17",
        "cook_events": [event.model_dump()],
        "slots": [slot.model_dump() for slot in slots],
        "targets": {
            day: {"calories": 2000, "protein_g": 144, "net_carbs_g": 120, "fat_g": 89}
            for day in DAYS
        },
        "failures": {},
    }


class APITestCase(unittest.TestCase):
    """Base class: a temp-data-dir repository, a throwaway FastAPI app
    wrapping `build_api_router`, and a `TestClient` against it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = LocalJSONRepository(
            data_dir=self.tmp.name,
            biometrics_path=str(Path(self.tmp.name) / "biometrics.json"),
        )
        # A throwaway registry per case, the same reason the repository is
        # a temp directory: the single-flight claim is process-wide state, so
        # a shared one would make these order-dependent.
        self.jobs = GenerationJobs()
        app = FastAPI()
        app.include_router(build_api_router(self.repo, self.jobs))
        self.client = TestClient(app)


class TestWeekPlanRoute(APITestCase):
    def test_get_current_week_plan(self):
        run_sync(self.repo.save_week_plan(make_week_plan_dict(), "current"))
        response = self.client.get("/api/weeks/current")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["days"], DAYS)
        self.assertEqual(len(body["cook_events"]), 1)
        self.assertEqual(body["cook_events"][0]["recipe"]["name"], "Green Chicken Curry")
        self.assertEqual(len(body["slots"]), len(DAYS) * len(MEAL_TYPES))

    def test_get_next_week_plan_absent_returns_404(self):
        response = self.client.get("/api/weeks/next")
        self.assertEqual(response.status_code, 404)

    def test_unknown_week_identifier_rejected(self):
        response = self.client.get("/api/weeks/bogus")
        self.assertEqual(response.status_code, 422)


class TestRecipesRoute(APITestCase):
    def setUp(self):
        super().setUp()
        run_sync(
            self.repo.import_recipe(
                make_recipe(name="Beef Stew", meal_type="dinner").model_dump(),
                favorite=True,
            )
        )
        run_sync(
            self.repo.import_recipe(
                make_recipe(name="Berry Smoothie", meal_type="breakfast").model_dump(),
                favorite=False,
            )
        )

    def test_no_filters_returns_everything(self):
        response = self.client.get("/api/recipes")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

    def test_favorite_filter(self):
        response = self.client.get("/api/recipes", params={"favorite": True})
        names = [entry["recipe"]["name"] for entry in response.json()]
        self.assertEqual(names, ["Beef Stew"])

    def test_meal_type_filter(self):
        response = self.client.get("/api/recipes", params={"meal_type": "breakfast"})
        names = [entry["recipe"]["name"] for entry in response.json()]
        self.assertEqual(names, ["Berry Smoothie"])

    def test_search_filter_is_case_insensitive_substring(self):
        response = self.client.get("/api/recipes", params={"search": "stew"})
        names = [entry["recipe"]["name"] for entry in response.json()]
        self.assertEqual(names, ["Beef Stew"])


class TestCatalogFilter(unittest.TestCase):
    """`repository.catalog_matches` — the one filter `/api/recipes` and the
    Library destination's grid now share (CHANGE-QUEUE.md item 1).

    Written because the two used to carry a copy each and had already
    drifted: `ui_catalog_browser._matches` treated `"All"` as the no-filter
    meal type, the route treated `None` as it, and neither knew the other's
    spelling. A drift like that returns a differently-filtered list rather
    than an error, so nothing would have caught a third filter landing on
    one side only. Both spellings are pinned here.
    """

    ENTRY = {
        "is_favorite": True,
        "recipe": {"name": "Beef Stew", "meal_type": "dinner"},
    }

    def test_both_no_meal_type_spellings_mean_no_filter(self):
        self.assertTrue(catalog_matches(self.ENTRY, meal_type=None))
        self.assertTrue(catalog_matches(self.ENTRY, meal_type=CATALOG_MEAL_TYPE_ANY))
        self.assertFalse(catalog_matches(self.ENTRY, meal_type="breakfast"))

    def test_search_is_case_insensitive_and_stripped(self):
        self.assertTrue(catalog_matches(self.ENTRY, search="  sTeW "))
        self.assertTrue(catalog_matches(self.ENTRY, search="   "))
        self.assertFalse(catalog_matches(self.ENTRY, search="curry"))

    def test_favorites_only(self):
        plain = {"is_favorite": False, "recipe": {"name": "Berry Smoothie"}}
        self.assertTrue(catalog_matches(plain))
        self.assertFalse(catalog_matches(plain, favorites_only=True))
        self.assertTrue(catalog_matches(self.ENTRY, favorites_only=True))

    def test_a_record_missing_its_recipe_is_filtered_not_a_crash(self):
        """The grid's copy read `entry["recipe"]` outright. A hand-edited
        catalog is exactly the input this app tolerates elsewhere (see
        `_detail_view`'s own fallback for a stored recipe that no longer
        validates), so the shared helper reads it the tolerant way."""
        self.assertTrue(catalog_matches({}))
        self.assertFalse(catalog_matches({}, meal_type="dinner"))
        self.assertFalse(catalog_matches({}, search="stew"))


class TestHistoryRoute(APITestCase):
    def test_history_round_trips(self):
        entries = [{"date": "2026-08-20", "day": "Thursday", "styles": {}}]
        run_sync(self.repo.save_history(entries))
        response = self.client.get("/api/history")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), entries)


class TestBiometricsRoute(APITestCase):
    def test_biometrics_shape_and_latest(self):
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-10", "weight_kg": 91.0}))
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-20", "weight_kg": 90.0}))
        run_sync(self.repo.save_daily_actuals({
            "date": "2026-08-20", "calories": 1900, "protein_g": 140,
            "net_carbs_g": 110, "fat_g": 80,
        }))
        response = self.client.get("/api/biometrics")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["weigh_ins"]), 2)
        self.assertEqual(len(body["daily_actuals"]), 1)
        self.assertEqual(body["latest"]["date"], "2026-08-20")

    def test_readiness_is_mirrored_not_summarised(self):
        """The third stored list reaches the route as rows.

        A route that reduced it to a "last night's readiness" figure would be
        a second answer to what the Settings sync page reads off the same
        file, which is the duplication the `/api/recipes` finding records.
        """
        run_sync(self.repo.save_readiness_entry({
            "date": "2026-08-20", "sleep_score": 83.0, "sleep_hours": 7.33,
            "hrv_ms": 42.0, "readiness_label": "excellent", "source": "garmin",
        }))
        body = self.client.get("/api/biometrics").json()
        self.assertEqual(body["readiness_log"], [{
            "date": "2026-08-20", "sleep_score": 83.0, "sleep_hours": 7.33,
            "hrv_ms": 42.0, "readiness_label": "excellent", "source": "garmin",
        }])
        # It is not folded into the weigh-in, and `latest` still means the
        # scale — a watch reading must never answer "what do you weigh".
        self.assertEqual(body["weigh_ins"], [])
        self.assertIsNone(body["latest"])

    def test_recorded_activity_is_mirrored_not_turned_into_a_proposal(self):
        """The fourth stored list reaches the route as rows.

        Running `propose_training_schedule` here would be the same mistake:
        a proposal is a diff against a *staged* schedule plus what one tab
        has dismissed, which is a session concept — and a route answering it
        would be free to disagree with the review dialog reading the same
        rows.
        """
        session = {
            "date": "2026-08-19", "activity_id": 1, "name": "Morning lift",
            "type": "strength_training", "session_type": "gym_hypertrophy",
            "start_time": "05:31", "duration_min": 45.0,
            "gross_calories": 300, "net_calories": 150, "source": "garmin",
        }
        run_sync(self.repo.save_activity_entries("2026-08-19", [session]))
        body = self.client.get("/api/biometrics").json()
        self.assertEqual(body["activity_log"], [session])


class TestTargetsRoute(APITestCase):
    def test_falls_back_to_file_schedule_without_biometrics(self):
        response = self.client.get("/api/targets")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body["dynamic_basis"])
        # The real config/profile.json's Monday, unhydrated.
        self.assertEqual(body["weekly_schedule"]["Monday"]["calories"], 1500)

    def test_computes_dynamic_basis_from_a_single_weigh_in(self):
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-20", "weight_kg": 90.0}))
        response = self.client.get("/api/targets")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNotNone(body["dynamic_basis"])
        # A single weigh-in can't feed calculate_adaptive_tdee (needs >= 2
        # spanning MIN_TREND_SPAN_DAYS), so this always reconciles to the
        # plain formula (nutrition_engine.reconcile_adaptive_tdee).
        self.assertEqual(body["dynamic_basis"]["tdee_source"], "formula")
        # Protein is locked to target_weight_kg (80.0) x protein_multiplier
        # (1.8) from the real profile, not to the weigh-in — deterministic
        # regardless of the weight logged.
        for day_targets in body["weekly_schedule"].values():
            self.assertEqual(day_targets["protein_g"], 144.0)

    def test_the_fibre_target_is_reported_with_or_without_a_weigh_in(self):
        """Derived from the day's calories rather than from the body, so a
        failed engine call does not cost it — and must not, or this route
        would omit a figure the telemetry header prints either way."""
        without = self.client.get("/api/targets").json()
        self.assertEqual(without["weekly_schedule"]["Monday"]["fiber_g"], 30.0)
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-20", "weight_kg": 90.0}))
        with_weigh_in = self.client.get("/api/targets").json()
        self.assertEqual(with_weigh_in["weekly_schedule"]["Monday"]["fiber_g"], 30.0)


# --------------------------------------------------------------------------
# Generation: the one write route, and the job it answers with
# --------------------------------------------------------------------------


def stub_generation(recorder, failures=None, raises=None):
    """A `generate_and_store_week` that stores a week without a model call.

    Substituted at `api.generate_and_store_week` — the module-level import is
    the seam, the same swap-and-restore `test_meal_selection.py` uses for
    `planner._generate_meal_type_events`. It *does* save through the
    repository, because "the finished week comes back through GET
    /api/weeks/{id} rather than on the job" is a contract worth testing end to
    end rather than asserting about in a comment.
    """

    async def stub(
        repository,
        config=None,
        *,
        week_identifier="current",
        week_start=None,
        servings=None,
        spec_transform=None,
        on_ready=None,
        progress_callback=None,
        note_callback=None,
    ):
        recorder["config"] = config
        recorder["week_identifier"] = week_identifier
        recorder["week_start"] = week_start
        recorder["servings"] = servings
        if raises is not None:
            raise raises
        if progress_callback:
            progress_callback("breakfast", 2)
            progress_callback("dinner", 3)
        if note_callback:
            note_callback("Trimmed Monday dinner to budget")
        plan = make_week_plan_dict()
        plan["failures"] = dict(failures or {})
        await repository.save_week_plan(plan, week_identifier)
        return WeekPlan.model_validate(plan)

    return stub


class TestGenerationJobRegistry(unittest.TestCase):
    """The registry alone — no FastAPI, no repository.

    `GenerationJobs.run` is awaited directly rather than reached through
    `start`, which is what that split exists for: asserting on a spawned task
    means asserting on a scheduler.
    """

    def setUp(self):
        self.jobs = GenerationJobs()

    def test_only_one_run_may_be_claimed_at_a_time(self):
        first = self.jobs.claim(SOURCE_API, "current")
        self.assertIsNotNone(first)
        self.assertIsNone(self.jobs.claim(SOURCE_UI, "next"))
        self.jobs.release(first)
        self.assertIsNotNone(self.jobs.claim(SOURCE_UI, "next"))

    def test_the_claim_names_who_holds_it(self):
        """A 409 has to be able to say "a browser tab is generating", which is
        the whole reason a UI run is recorded here and not only an API one."""
        self.jobs.claim(SOURCE_UI, "current")
        self.assertEqual(self.jobs.active.source, SOURCE_UI)

    def test_a_finished_run_records_what_it_saw(self):
        job = self.jobs.claim(SOURCE_API, "current")
        self.jobs.stage_started(job, "breakfast")
        self.jobs.note(job, "Trimmed Monday dinner")

        async def work():
            return "done"

        asyncio.run(self.jobs.run(job, work))
        self.assertEqual(job.status, JOB_SUCCEEDED)
        self.assertEqual(job.stages_started, ["breakfast"])
        self.assertEqual(job.notes, ["Trimmed Monday dinner"])
        self.assertIsNotNone(job.finished_at)
        self.assertIsNone(self.jobs.active)

    def test_a_failed_run_releases_the_claim(self):
        """The claim must not survive its run — a guard that leaks is worse
        than no guard, because nothing can generate again until a restart."""
        job = self.jobs.claim(SOURCE_API, "current")

        async def work():
            raise RuntimeError("provider fell over")

        asyncio.run(self.jobs.run(job, work))
        self.assertEqual(job.status, JOB_FAILED)
        self.assertIn("provider fell over", job.error)
        self.assertIsNone(self.jobs.active)

    def test_a_rejected_grid_is_reported_apart_from_a_failed_call(self):
        """`WeekNotValidError` means no API call was ever paid for, so a
        client can tell "fix your config" from "retry" without parsing a
        message."""
        job = self.jobs.claim(SOURCE_API, "current")

        async def work():
            raise WeekNotValidError(["Monday:lunch leftover has no source"])

        asyncio.run(self.jobs.run(job, work))
        self.assertEqual(job.status, JOB_FAILED)
        self.assertEqual(
            job.validation_errors, ["Monday:lunch leftover has no source"]
        )

    def test_a_pydantic_error_does_not_crash_the_handler(self):
        """`WeekNotValidError.errors` is a list and pydantic's own `errors` is
        a *method*, so the duck-typed read has to check. instructor raises
        exactly that when a model cannot satisfy the schema, so a bare
        `list(exc.errors)` would fail inside the handler that exists to stop
        an exception escaping — taking the claim with it."""
        from pydantic import BaseModel, ValidationError

        class Model(BaseModel):
            x: int

        with self.assertRaises(ValidationError) as caught:
            Model(x="not an int")

        job = self.jobs.claim(SOURCE_API, "current")

        async def work():
            raise caught.exception

        asyncio.run(self.jobs.run(job, work))
        self.assertEqual(job.status, JOB_FAILED)
        self.assertEqual(job.validation_errors, [])
        self.assertIsNone(self.jobs.active)

    def test_release_never_demotes_a_run_that_already_failed(self):
        job = self.jobs.claim(SOURCE_API, "current")
        self.jobs.fail(job, "boom")
        self.jobs.release(job, JOB_SUCCEEDED)
        self.assertEqual(job.status, JOB_FAILED)

    def test_recent_is_newest_first_and_bounded(self):
        jobs = GenerationJobs(max_retained=3)
        for _ in range(5):
            job = jobs.claim(SOURCE_API, "current")
            jobs.release(job)
        self.assertEqual(len(jobs.recent()), 3)
        self.assertGreater(jobs.recent()[0].started_at, "")
        self.assertEqual(len(jobs.recent(limit=2)), 2)

    def test_the_running_job_is_never_evicted(self):
        """A client polling a run still in flight must not be told it never
        existed, however many finished runs queue up behind it."""
        jobs = GenerationJobs(max_retained=2)
        running = jobs.claim(SOURCE_API, "current")
        for _ in range(4):
            # Nothing else can claim while `running` holds it, so these are
            # recorded by hand — the eviction rule is what is under test, not
            # the claim.
            jobs._remember(
                type(running)(
                    id=f"finished-{_}",
                    week_identifier="current",
                    source=SOURCE_API,
                    started_at="2026-08-31T00:00:00+00:00",
                    status=JOB_SUCCEEDED,
                )
            )
        self.assertIsNotNone(jobs.get(running.id))
        self.assertIs(jobs.active, running)


class TestGenerationRoute(APITestCase):
    def setUp(self):
        super().setUp()
        self._real = api.generate_and_store_week
        self.addCleanup(setattr, api, "generate_and_store_week", self._real)
        self.recorder = {}

    def _await_job(self, job_id, tries=200):
        """Poll until the background task lands.

        `TestClient` is used as a context manager by the callers so one portal
        (and one loop) spans every request — without that, the task spawned
        during the POST is abandoned when the per-request portal closes. No
        sleep: each round trip through the portal is itself a loop turn.
        """
        for _ in range(tries):
            body = self.client.get(f"/api/jobs/{job_id}").json()
            if body["status"] != JOB_RUNNING:
                return body
        self.fail(f"job {job_id} never finished")

    def test_generating_returns_a_job_and_stores_the_week(self):
        api.generate_and_store_week = stub_generation(self.recorder)
        with self.client:
            started = self.client.post("/api/weeks/current/generate")
            self.assertEqual(started.status_code, 202)
            job = started.json()
            self.assertEqual(job["status"], JOB_RUNNING)
            self.assertEqual(job["source"], SOURCE_API)
            self.assertEqual(job["week_identifier"], "current")
            # The stages are named on the 202 itself, so a client has a
            # denominator before the first poll.
            self.assertIn("dinner", job["stages"])

            finished = self._await_job(job["id"])

        self.assertEqual(finished["status"], JOB_SUCCEEDED)
        self.assertEqual(finished["stages_started"], ["breakfast", "dinner"])
        self.assertEqual(finished["notes"], ["Trimmed Monday dinner to budget"])
        self.assertIsNotNone(finished["finished_at"])
        # The week itself is not on the job — it comes back through the read
        # route, which is the whole reason the job carries no plan.
        week = self.client.get("/api/weeks/current")
        self.assertEqual(week.status_code, 200)
        self.assertEqual(week.json()["days"], DAYS)

    def test_the_request_body_reaches_the_spec(self):
        api.generate_and_store_week = stub_generation(self.recorder)
        with self.client:
            job = self.client.post(
                "/api/weeks/next/generate",
                json={"week_start": "Sunday", "servings": 4},
            ).json()
            self._await_job(job["id"])
        self.assertEqual(self.recorder["week_identifier"], "next")
        self.assertEqual(self.recorder["week_start"], "Sunday")
        self.assertEqual(self.recorder["servings"], 4)
        # Generating "next" must not touch "current" — the same rule
        # `ui_generation` states about the header's week select.
        self.assertEqual(self.client.get("/api/weeks/current").status_code, 404)
        self.assertEqual(self.client.get("/api/weeks/next").status_code, 200)

    def test_a_second_run_is_refused_while_one_is_in_flight(self):
        api.generate_and_store_week = stub_generation(self.recorder)
        with self.client:
            first = self.client.post("/api/weeks/current/generate").json()
            # Claimed synchronously in the route, so this is refused without
            # depending on where the background task has got to.
            clash = self.client.post("/api/weeks/current/generate")
            self.assertEqual(clash.status_code, 409)
            self.assertIn(first["id"], clash.json()["detail"])
            self.assertIn(SOURCE_API, clash.json()["detail"])
            self._await_job(first["id"])
            # And the claim is released, so the next one is accepted.
            again = self.client.post("/api/weeks/current/generate")
            self.assertEqual(again.status_code, 202)
            self._await_job(again.json()["id"])

    def test_a_browser_tab_holding_the_claim_refuses_the_route(self):
        """The cross-client half — `PlannerState.generating` cannot see the
        route and the route cannot see it, so `ui_generation.run_generation`
        claims here instead."""
        self.jobs.claim(SOURCE_UI, "current")
        clash = self.client.post("/api/weeks/current/generate")
        self.assertEqual(clash.status_code, 409)
        self.assertIn(SOURCE_UI, clash.json()["detail"])

    def test_per_meal_type_failures_ride_on_a_succeeded_run(self):
        """A failed meal must not fail the week, so these are not a failed
        job — a client has to read both fields."""
        api.generate_and_store_week = stub_generation(
            self.recorder, failures={"Monday:dinner": "provider returned nothing"}
        )
        with self.client:
            job = self.client.post("/api/weeks/current/generate").json()
            finished = self._await_job(job["id"])
        self.assertEqual(finished["status"], JOB_SUCCEEDED)
        self.assertEqual(
            finished["failures"], {"Monday:dinner": "provider returned nothing"}
        )

    def test_a_rejected_grid_lands_on_the_job_not_the_response(self):
        """The POST is 202 whatever happens next: validation needs the config
        loaded and the grid built, which is the run's own first step. The
        distinction survives on the job instead."""
        api.generate_and_store_week = stub_generation(
            self.recorder, raises=WeekNotValidError(["Monday:lunch has no source"])
        )
        with self.client:
            started = self.client.post("/api/weeks/current/generate")
            self.assertEqual(started.status_code, 202)
            finished = self._await_job(started.json()["id"])
        self.assertEqual(finished["status"], JOB_FAILED)
        self.assertEqual(finished["validation_errors"], ["Monday:lunch has no source"])
        # Nothing was stored, so the read route still says there is no week.
        self.assertEqual(self.client.get("/api/weeks/current").status_code, 404)

    def test_jobs_are_listed_newest_first(self):
        api.generate_and_store_week = stub_generation(self.recorder)
        with self.client:
            first = self.client.post("/api/weeks/current/generate").json()
            self._await_job(first["id"])
            second = self.client.post("/api/weeks/next/generate").json()
            self._await_job(second["id"])
            listed = self.client.get("/api/jobs").json()
        self.assertEqual([job["id"] for job in listed], [second["id"], first["id"]])

    def test_unknown_job_is_404(self):
        self.assertEqual(self.client.get("/api/jobs/nope").status_code, 404)

    def test_unknown_week_identifier_is_rejected_before_anything_is_claimed(self):
        response = self.client.post("/api/weeks/bogus/generate")
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.jobs.active)


if __name__ == "__main__":
    unittest.main()

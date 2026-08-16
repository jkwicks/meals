# Test Suite & Unit Tests

=== File: tests/test_sync_service.py ===
"""Tests for `src/integrations/sync_service.py`.

`unittest` from the standard library, matching `test_nutrition_engine.py` —
this venv carries no pytest, so `python -m unittest discover tests` runs
everything with no new dependency.

Nothing here touches the network or a real account. The two clients are the
only parts that do, and they are reached through one seam each
(`GarminSyncService.client()`, `CronometerSyncService._rows_*`), so a fake
substituted at that seam exercises every line of mapping below it. The
payload shapes the fakes return are the real ones — grams for Garmin mass,
`Energy (kcal)`-style headers for the Cronometer CSV — because the mapping is
the whole point of the module and a fake speaking the app's own dialect would
test nothing.
"""

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(_SRC / "integrations"))

import sync_service as sync  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402


class FakeGarminClient:
    """The subset of `garminconnect.Garmin` the service actually calls.

    Weights are in grams and sleep in seconds, which is what Garmin really
    sends — the unit conversion is one of the things most likely to regress.
    """

    def __init__(self, weight_list=None, activities=None, sleep=None):
        self._weight_list = weight_list
        self._activities = activities if activities is not None else []
        self._sleep = sleep

    def get_body_composition(self, startdate, enddate=None):
        if self._weight_list is None:
            return {"dateWeightList": [], "totalAverage": {}}
        return {"dateWeightList": self._weight_list}

    def get_activities_by_date(self, startdate, enddate, activitytype=None):
        return self._activities

    def get_sleep_data(self, cdate):
        if self._sleep is None:
            raise RuntimeError("no sleep data")
        return self._sleep


def garmin_service(**kwargs):
    service = sync.GarminSyncService(email="user@example.com", password="pw")
    service._client = FakeGarminClient(**kwargs)
    return service


@contextlib.contextmanager
def replace_garmin_client(fake):
    """Make `sync_garmin` build a service wired to `fake`.

    `sync_garmin` constructs its own `GarminSyncService`, which is the point —
    the write guard being tested lives in that function, not in the service —
    so the seam has to be the class rather than an injected instance.
    """
    original = sync.GarminSyncService

    class Patched(original):
        def __init__(self, *args, **kwargs):
            super().__init__(email="user@example.com", password="pw")
            self._client = fake

    sync.GarminSyncService = Patched
    try:
        yield
    finally:
        sync.GarminSyncService = original


FULL_READING = {
    "weight": 98400,
    "bodyFat": 27.5,
    "muscleMass": 38200,
    "bodyWater": 52.1,
    "bmi": 29.4,
}


class TestBodyComposition(unittest.TestCase):
    def test_grams_are_converted_to_kilograms(self):
        """Garmin sends mass in grams; `weigh_ins` is documented in kg.

        Storing 98400 would not look like an error anywhere downstream — it
        would look like a person, and every derived target would be computed
        from it.
        """
        entry = garmin_service(weight_list=[FULL_READING]).fetch_body_composition(
            "2026-08-16"
        )
        self.assertEqual(entry["weight_kg"], 98.4)
        self.assertEqual(entry["muscle_mass_kg"], 38.2)

    def test_percentages_pass_through_unscaled(self):
        entry = garmin_service(weight_list=[FULL_READING]).fetch_body_composition(
            "2026-08-16"
        )
        self.assertEqual(entry["body_fat_pct"], 27.5)
        self.assertEqual(entry["water_pct"], 52.1)
        self.assertEqual(entry["bmi"], 29.4)

    def test_latest_measurement_wins(self):
        """Two readings in one morning is a normal thing for a smart scale."""
        entry = garmin_service(
            weight_list=[{"weight": 99000}, {"weight": 98400}]
        ).fetch_body_composition("2026-08-16")
        self.assertEqual(entry["weight_kg"], 98.4)

    def test_absent_metrics_are_omitted_not_zeroed(self):
        """A scale reporting only weight must not blank a stored body fat.

        `save_biometric_entry` merges on `date`, so a key present with 0.0
        overwrites while an absent key leaves the earlier reading alone.
        """
        entry = garmin_service(
            weight_list=[{"weight": 98400}]
        ).fetch_body_composition("2026-08-16")
        self.assertEqual(entry["weight_kg"], 98.4)
        for absent in ("body_fat_pct", "muscle_mass_kg", "water_pct", "bmi"):
            self.assertNotIn(absent, entry)

    def test_no_reading_yields_date_only(self):
        """A day the scale wasn't used is a real state, not a zero-weight day."""
        entry = garmin_service(weight_list=None).fetch_body_composition("2026-08-16")
        self.assertEqual(entry, {"date": "2026-08-16"})

    def test_malformed_date_is_rejected(self):
        with self.assertRaises(ValueError):
            garmin_service(weight_list=[FULL_READING]).fetch_body_composition("16/08/2026")


ACTIVITIES = [
    {
        "activityId": 1,
        "activityName": "Elliptical",
        "activityType": {"typeKey": "elliptical"},
        "calories": 640,
        "duration": 3600,
        "averageHR": 132,
    },
    {
        "activityId": 2,
        "activityName": "Morning lift",
        "activityType": {"typeKey": "strength_training"},
        "calories": 300,
        "duration": 2700,
    },
    {
        "activityId": 3,
        "activityName": "Treadmill",
        "activityType": {"typeKey": "treadmill_running"},
        "calories": 500,
        "duration": 1800,
    },
]


class TestCardioActivities(unittest.TestCase):
    def test_only_cardio_modalities_are_returned(self):
        sessions = garmin_service(activities=ACTIVITIES).fetch_cardio_activities(
            "2026-08-16"
        )
        self.assertEqual([s["type"] for s in sessions], ["elliptical", "treadmill_running"])

    def test_net_calories_apply_the_recovery_factor(self):
        """The discount is the reason this method exists — see the constant."""
        sessions = garmin_service(activities=ACTIVITIES).fetch_cardio_activities(
            "2026-08-16"
        )
        self.assertEqual(sessions[0]["net_calories"], 320)
        self.assertEqual(sessions[1]["net_calories"], 250)

    def test_gross_is_kept_alongside_net(self):
        """A silently adjusted number can't be reconciled against the watch."""
        session = garmin_service(activities=ACTIVITIES).fetch_cardio_activities(
            "2026-08-16"
        )[0]
        self.assertEqual(session["gross_calories"], 640)
        self.assertLess(session["net_calories"], session["gross_calories"])

    def test_recovery_factor_is_the_documented_half(self):
        """Pinned: changing it silently re-scales every exercise figure."""
        self.assertEqual(sync.EXERCISE_RECOVERY_FACTOR, 0.50)

    def test_duration_is_reported_in_minutes(self):
        session = garmin_service(activities=ACTIVITIES).fetch_cardio_activities(
            "2026-08-16"
        )[0]
        self.assertEqual(session["duration_min"], 60.0)

    def test_missing_calories_do_not_crash(self):
        sessions = garmin_service(
            activities=[{"activityType": {"typeKey": "elliptical"}, "duration": 600}]
        ).fetch_cardio_activities("2026-08-16")
        self.assertEqual(sessions[0]["net_calories"], 0)

    def test_no_activities_is_an_empty_list(self):
        self.assertEqual(
            garmin_service(activities=[]).fetch_cardio_activities("2026-08-16"), []
        )


SLEEP = {
    "dailySleepDTO": {
        "sleepScores": {"overall": {"value": 83}},
        "sleepTimeSeconds": 26400,
    }
}


class TestReadiness(unittest.TestCase):
    def test_sleep_score_is_reported(self):
        readiness = garmin_service(sleep=SLEEP).fetch_readiness("2026-08-16")
        self.assertEqual(readiness["sleep_score"], 83.0)
        self.assertEqual(readiness["readiness"], "excellent")
        self.assertEqual(readiness["sleep_hours"], 7.33)

    def test_readiness_carries_no_energy_figure(self):
        """The rule this method exists to enforce.

        A sleep score is a unitless index; anything here that looked like
        kcal would be an invitation to add it to a day's energy, and there is
        no conversion that would make that legitimate.
        """
        readiness = garmin_service(sleep=SLEEP).fetch_readiness("2026-08-16")
        for key in readiness:
            self.assertNotIn("calor", key.lower())
            self.assertNotIn("energy", key.lower())
            self.assertNotIn("kcal", key.lower())

    def test_bands_match_garmins_own(self):
        self.assertEqual(sync._readiness_label(80), "excellent")
        self.assertEqual(sync._readiness_label(79), "good")
        self.assertEqual(sync._readiness_label(60), "good")
        self.assertEqual(sync._readiness_label(59), "fair")
        self.assertEqual(sync._readiness_label(40), "fair")
        self.assertEqual(sync._readiness_label(39), "poor")
        self.assertIsNone(sync._readiness_label(None))

    def test_sleep_failure_does_not_propagate(self):
        """Supplementary data must not fail a weigh-in sync that worked."""
        readiness = garmin_service(sleep=None).fetch_readiness("2026-08-16")
        self.assertIsNone(readiness["sleep_score"])
        self.assertIsNone(readiness["readiness"])


class TestCronometerMapping(unittest.TestCase):
    """`_daily_summary_row` — the CSV-to-`daily_actuals` fold.

    Split out of `fetch_daily_summary` precisely so it can be tested without
    an account, a paid tier, or a subprocess.
    """

    ROWS = [
        {
            "Date": "2026-08-15",
            "Energy (kcal)": "1820.4",
            "Protein (g)": "141.2",
            "Net Carbs (g)": "88.0",
            "Fat (g)": "71.5",
        },
        {
            "Date": "2026-08-16",
            "Energy (kcal)": "2010",
            "Protein (g)": "150",
            "Net Carbs (g)": "95",
            "Fat (g)": "80",
        },
    ]

    def test_keys_are_the_repositorys_not_cronometers(self):
        """`nutrition_engine` indexes `protein_g`, not `protein`.

        A row keyed the CSV's way stores and displays perfectly and feeds
        nothing — the adaptive loop would simply never adapt, weeks later.
        """
        entry = sync._daily_summary_row(self.ROWS, "2026-08-16")
        self.assertEqual(
            sorted(k for k in entry if k not in ("date", "source")),
            ["calories", "fat_g", "net_carbs_g", "protein_g"],
        )

    def test_the_matching_day_is_selected(self):
        entry = sync._daily_summary_row(self.ROWS, "2026-08-15")
        self.assertEqual(entry["calories"], 1820.4)
        self.assertEqual(entry["protein_g"], 141.2)

    def test_day_column_variant_is_accepted(self):
        """The servings export names it `Day`; the summary has used both."""
        entry = sync._daily_summary_row(
            [{"Day": "2026-08-16", "Energy (kcal)": "2010"}], "2026-08-16"
        )
        self.assertEqual(entry["calories"], 2010.0)

    def test_nothing_logged_yields_date_only(self):
        self.assertEqual(sync._daily_summary_row([], "2026-08-16"), {"date": "2026-08-16"})

    def test_a_lone_undated_row_is_taken_as_the_day(self):
        entry = sync._daily_summary_row([{"Energy (kcal)": "1500"}], "2026-08-16")
        self.assertEqual(entry["calories"], 1500.0)

    def test_ambiguous_rows_are_not_guessed(self):
        """Guessing here overwrites a good day's entry with another day's."""
        entry = sync._daily_summary_row(
            [{"Energy (kcal)": "1"}, {"Energy (kcal)": "2"}], "2026-08-16"
        )
        self.assertEqual(entry, {"date": "2026-08-16"})


class TestCoercion(unittest.TestCase):
    def test_as_float_rejects_non_numbers(self):
        for junk in ("--", "", None, True, "n/a"):
            self.assertIsNone(sync._as_float(junk))

    def test_as_float_accepts_numeric_strings(self):
        self.assertEqual(sync._as_float(" 3.5 "), 3.5)

    def test_prune_keeps_date_but_drops_nones(self):
        self.assertEqual(
            sync._prune({"date": "2026-08-16", "a": None, "b": 1}),
            {"date": "2026-08-16", "b": 1},
        )

    def test_prune_keeps_a_dateless_none_date(self):
        self.assertIn("date", sync._prune({"date": None}))

    def test_iso_rejects_non_iso(self):
        for bad in ("16-08-2026", "August 16", "", None):
            with self.assertRaises(ValueError):
                sync._iso(bad)

    def test_cardio_matching_is_substring_based(self):
        """Garmin sub-types freely; exact matching would drop real sessions."""
        self.assertTrue(sync._is_cardio("treadmill_running"))
        self.assertTrue(sync._is_cardio("indoor_cycling"))
        self.assertTrue(sync._is_cardio("virtual_ride"))
        self.assertFalse(sync._is_cardio("strength_training"))
        self.assertFalse(sync._is_cardio("walking"))


class TestPersistence(unittest.TestCase):
    """The round trip through a real `LocalJSONRepository` on a temp file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "biometrics.json")
        self.repo = LocalJSONRepository(biometrics_path=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def stored(self):
        with open(self.path) as handle:
            return json.load(handle)

    def test_weigh_in_lands_in_the_weigh_ins_list(self):
        entry = garmin_service(weight_list=[FULL_READING]).fetch_body_composition(
            "2026-08-16"
        )
        run_sync(self.repo.save_biometric_entry(entry))
        self.assertEqual(self.stored()["weigh_ins"][0]["weight_kg"], 98.4)

    def test_actuals_land_in_the_daily_actuals_list(self):
        entry = sync._daily_summary_row(TestCronometerMapping.ROWS, "2026-08-16")
        run_sync(self.repo.save_daily_actuals(entry))
        row = self.stored()["daily_actuals"][0]
        self.assertEqual(row["protein_g"], 150.0)

    def test_resync_updates_rather_than_duplicates(self):
        service = garmin_service(weight_list=[FULL_READING])
        run_sync(self.repo.save_biometric_entry(service.fetch_body_composition("2026-08-16")))
        run_sync(self.repo.save_biometric_entry(service.fetch_body_composition("2026-08-16")))
        self.assertEqual(len(self.stored()["weigh_ins"]), 1)

    def test_sync_garmin_stores_nothing_when_the_scale_was_not_used(self):
        """Regression: `source` alone once passed for a measurement.

        `sync_garmin` decided by `len(entry) > 2`, which the provenance tag
        cleared on its own — so a day the scale never saw was written as a
        weigh-in with no weight, and `get_latest_biometrics` would hand that
        empty row back as the most recent reading.

        Driven through `sync_garmin` rather than the guard directly, because
        the guard was never the broken part: the call site was.
        """
        with replace_garmin_client(FakeGarminClient(weight_list=None)):
            result = sync.sync_garmin("2026-08-16", self.repo)

        self.assertEqual(result["weigh_in"], {"date": "2026-08-16"})
        self.assertEqual(run_sync(self.repo.load_biometrics())["weigh_ins"], [])

    def test_sync_garmin_stores_a_real_reading(self):
        """The other half of the guard: a real weigh-in must still land."""
        with replace_garmin_client(FakeGarminClient(weight_list=[FULL_READING])):
            sync.sync_garmin("2026-08-16", self.repo)

        weigh_ins = run_sync(self.repo.load_biometrics())["weigh_ins"]
        self.assertEqual(len(weigh_ins), 1)
        self.assertEqual(weigh_ins[0]["weight_kg"], 98.4)
        self.assertEqual(weigh_ins[0]["source"], "garmin")

    def test_partial_resync_preserves_earlier_metrics(self):
        """The pay-off of omitting absent keys rather than zeroing them."""
        full = garmin_service(weight_list=[FULL_READING])
        run_sync(self.repo.save_biometric_entry(full.fetch_body_composition("2026-08-16")))

        partial = garmin_service(weight_list=[{"weight": 98100}])
        run_sync(self.repo.save_biometric_entry(partial.fetch_body_composition("2026-08-16")))

        row = self.stored()["weigh_ins"][0]
        self.assertEqual(row["weight_kg"], 98.1)
        self.assertEqual(row["body_fat_pct"], 27.5)


class TestCronometerCredentialGuards(unittest.TestCase):
    def test_missing_credentials_fail_before_any_call(self):
        service = sync.CronometerSyncService(username="", password="")
        with self.assertRaises(RuntimeError) as caught:
            service.fetch_daily_summary("2026-08-16")
        self.assertIn("CRONOMETER_USERNAME", str(caught.exception))

    def test_missing_sidecar_message_says_how_to_fix_it(self):
        """The 3.9-vs-3.11 mismatch is the expected state of this project."""
        service = sync.CronometerSyncService(
            username="u", password="p", python_executable="/nonexistent/python"
        )
        if sys.version_info >= (3, 11):
            self.skipTest("in-process import is available on this interpreter")
        with self.assertRaises(RuntimeError) as caught:
            service.fetch_daily_summary("2026-08-16")
        message = str(caught.exception)
        self.assertIn("venv-cronometer", message)
        self.assertIn("MEALS_CRONOMETER_PYTHON", message)


if __name__ == "__main__":
    unittest.main()
-e 

=== File: tests/test_planner_dynamic_targets.py ===
"""Tests for the biometric-driven half of `src/planner.py`.

Covers the three pieces that turn a measured body into a prompt:
`hydrate_dynamic_targets` (the day's macros come from the scale, not the file),
`apply_protein_floor` (the day's locked protein reaches every meal), and
`logged_intake_for` (today's Cronometer row beats the plan).

All three are pure functions of their arguments — no repository, no event loop,
no API — which is the point of `hydrate_config` being the only async wrapper
around them. `unittest` and the `sys.path` insert match
`test_nutrition_engine.py`; see its docstring for why.
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402  (path setup must precede the import)
from week import MODE_COOK, MODE_LEFTOVER, SlotSpec  # noqa: E402

# data/config.json's own user_profile. Every expected number below is worked
# out by hand from it rather than captured from a run, so a changed constant
# fails loudly instead of re-baselining itself against the code under test.
PROFILE = {
    "birth_date": "1971-01-10",
    "height_cm": 183,
    "gender": "male",
    "target_weight_kg": 80.0,
    "protein_multiplier": 1.8,
    "activity_level": "light_office",
}
# Katch-McArdle on 98.4 kg at 27.5% body fat: LBM 71.34, BMR 1910.9,
# TDEE x1.375 = 2627.5, deficit 718.0 at 18.4 kg to go, so 1910 kcal.
WEIGH_IN = {"date": "2026-08-16", "weight_kg": 98.4, "body_fat_pct": 27.5}
DYNAMIC_KCAL = 1910
LOCKED_PROTEIN_G = 144.0


def config_with(**overrides) -> dict:
    """A minimal config carrying the two sections hydration reads."""
    base = {
        "user_profile": dict(PROFILE),
        "weekly_schedule": {
            "Monday": {"calories": 1500, "protein_g": 120, "net_carbs_g": 130, "fat_g": 55,
                       "meal_overrides": {"breakfast": {"calories": 400}}},
            "Wednesday": {"calories": 1000, "protein_g": 110, "net_carbs_g": 60, "fat_g": 35,
                          "meal_overrides": {}},
        },
    }
    base.update(overrides)
    return base


class TestHydrateDynamicTargets(unittest.TestCase):
    def setUp(self):
        self.hydrated = planner.hydrate_dynamic_targets(config_with(), WEIGH_IN)

    def test_calories_come_from_the_scale_not_the_file(self):
        for day in ("Monday", "Wednesday"):
            self.assertEqual(self.hydrated["weekly_schedule"][day]["calories"], DYNAMIC_KCAL)

    def test_protein_is_locked_to_the_target_weight_every_day(self):
        """80 kg x 1.8, identical on both days, where the file said 120 and 110.

        The floor must not track the scale — that would shrink it exactly as
        the diet started to threaten the lean mass it exists to protect.
        """
        for day in ("Monday", "Wednesday"):
            self.assertEqual(
                self.hydrated["weekly_schedule"][day]["protein_g"], LOCKED_PROTEIN_G
            )

    def test_carb_cycling_survives(self):
        """Each day's own net_carbs_g is passed *into* the engine, not replaced."""
        self.assertEqual(self.hydrated["weekly_schedule"]["Monday"]["net_carbs_g"], 130.0)
        self.assertEqual(self.hydrated["weekly_schedule"]["Wednesday"]["net_carbs_g"], 60.0)

    def test_fat_absorbs_the_difference_between_the_two_days(self):
        """Same energy and protein, different carbs, so only fat moves."""
        monday = self.hydrated["weekly_schedule"]["Monday"]
        wednesday = self.hydrated["weekly_schedule"]["Wednesday"]
        # 70 g of carbs at 4 kcal is 280 kcal, which is 31.1 g of fat at 9.
        # `delta` rather than `places` because each figure is independently
        # rounded to 1 dp before the subtraction sees it.
        self.assertAlmostEqual(wednesday["fat_g"] - monday["fat_g"], 280 / 9, delta=0.1)

    def test_meal_overrides_are_untouched(self):
        self.assertEqual(
            self.hydrated["weekly_schedule"]["Monday"]["meal_overrides"],
            {"breakfast": {"calories": 400}},
        )

    def test_the_basis_is_recorded_for_diagnosis(self):
        basis = self.hydrated["dynamic_basis"]
        self.assertEqual(basis["bmr_method"], "katch_mcardle")
        self.assertEqual(basis["current_weight_kg"], 98.4)


class TestHydrationFallsBack(unittest.TestCase):
    def test_no_weight_anywhere_keeps_the_files_targets(self):
        """The engine raises; the planner must not.

        `weekly_schedule` holds real targets somebody chose, so falling back
        plans a configured week rather than a fabricated body. biometrics.json
        is empty until the first Garmin sync, so this is the normal path on a
        fresh checkout.
        """
        config = config_with()
        with self.assertLogs("meals", level="WARNING"):
            self.assertIs(planner.hydrate_dynamic_targets(config, None), config)

    def test_the_fallback_is_announced(self):
        """One note, not one per day: every day fails identically, because they
        differ only in the carb figure the failure never reaches."""
        notes = []
        with self.assertLogs("meals", level="WARNING"):
            planner.hydrate_dynamic_targets(config_with(), None, notes.append)
        self.assertEqual(len(notes), 1)
        self.assertIn("config.json targets", notes[0])

    def test_an_unfilled_profile_is_left_alone(self):
        config = config_with(user_profile={"protein_multiplier": 1.8, "activity_level": "light_office"})
        self.assertIs(planner.hydrate_dynamic_targets(config, WEIGH_IN), config)


class TestTrainingUpliftSurvivesHydration(unittest.TestCase):
    """A workout expands the day; hydration must not quietly undo that."""

    def setUp(self):
        config = config_with(
            meal_types=["breakfast", "lunch", "dinner", "snack"],
            meal_weights={"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
            training_schedule=[{
                "day": "Monday", "time": "18:00", "type": "gym_hypertrophy",
                "estimated_burn_kcal": 350,
            }],
        )
        self.adjusted = planner.apply_training_adjustments(config)
        self.hydrated = planner.hydrate_dynamic_targets(self.adjusted, WEIGH_IN)

    def test_the_uplift_is_recorded(self):
        self.assertEqual(self.adjusted["training_uplift"]["Monday"]["calories"], 350.0)

    def test_the_burn_is_replayed_onto_the_dynamic_baseline(self):
        self.assertEqual(
            self.hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL + 350
        )

    def test_protein_stays_locked_on_a_training_day(self):
        """`TRAINING_INTENSITY_SPLIT` would add 43.75 g; the lock wins.

        The workout's energy buys back carbs and fat, not protein — 'locked'
        has to mean locked or the 144 g figure is only a resting-day number.
        """
        self.assertEqual(
            self.hydrated["weekly_schedule"]["Monday"]["protein_g"], LOCKED_PROTEIN_G
        )

    def test_the_carb_share_is_not_double_counted(self):
        """apply_training_adjustments already put it in weekly_schedule, and
        hydration passes that figure through the engine untouched."""
        # 130 from the file + 43.75 from the burn's carb share, at hydration's
        # 1 dp. Double-counting would read 217.5.
        self.assertEqual(self.hydrated["weekly_schedule"]["Monday"]["net_carbs_g"], 173.8)


def cook_slot(meal_type: str, mode: str = MODE_COOK) -> SlotSpec:
    return SlotSpec(
        id=planner.slot_id("Monday", meal_type), day="Monday", meal_type=meal_type, mode=mode
    )


class TestProteinFloor(unittest.TestCase):
    """`min_meal_protein_g` moves grams between meals; it never creates any."""

    def setUp(self):
        self.slots = [cook_slot(m) for m in ("lunch", "dinner", "snack")]
        # A weight-only split of 114 g across 0.30/0.30/0.10.
        self.budgets = {
            "Monday:lunch": {"calories": 668.6, "protein_g": 48.857, "net_carbs_g": 15.0, "fat_g": 47.0},
            "Monday:dinner": {"calories": 668.6, "protein_g": 48.857, "net_carbs_g": 15.0, "fat_g": 47.0},
            "Monday:snack": {"calories": 222.9, "protein_g": 16.286, "net_carbs_g": 5.0, "fat_g": 15.7},
        }
        self.before = {k: dict(v) for k, v in self.budgets.items()}
        self.after = planner.apply_protein_floor(
            self.budgets, self.slots, {slot.id: 1 for slot in self.slots}, 35.0
        )

    def test_every_meal_reaches_the_floor(self):
        for slot in self.slots:
            self.assertGreaterEqual(self.after[slot.id]["protein_g"], 35.0)

    def test_the_days_protein_is_conserved(self):
        self.assertAlmostEqual(
            sum(b["protein_g"] for b in self.before.values()),
            sum(b["protein_g"] for b in self.after.values()),
            places=6,
        )

    def test_calories_move_with_the_protein(self):
        """4 kcal per gram transferred, so each budget still reconciles and the
        day's calorie total is conserved as exactly as its protein."""
        for slot in self.slots:
            moved = self.after[slot.id]["protein_g"] - self.before[slot.id]["protein_g"]
            self.assertAlmostEqual(
                self.after[slot.id]["calories"],
                self.before[slot.id]["calories"] + moved * 4,
                places=6,
            )
        self.assertAlmostEqual(
            sum(b["calories"] for b in self.before.values()),
            sum(b["calories"] for b in self.after.values()),
            places=6,
        )

    def test_donors_give_in_proportion_to_their_surplus(self):
        """Lunch and dinner are identical, so they must give identically."""
        self.assertAlmostEqual(
            self.after["Monday:lunch"]["protein_g"],
            self.after["Monday:dinner"]["protein_g"],
            places=6,
        )

    def test_an_unaffordable_floor_changes_nothing(self):
        """Raising some meals and starving others would be an arbitrary choice
        about which meal gets short-changed; a day that can't carry n x 35 g is
        a target problem, not a split problem."""
        budgets = {slot.id: {"calories": 200.0, "protein_g": 12.0,
                             "net_carbs_g": 10.0, "fat_g": 8.0} for slot in self.slots}
        with self.assertLogs("meals", level="WARNING") as logged:
            after = planner.apply_protein_floor(
                budgets, self.slots, {slot.id: 1 for slot in self.slots}, 35.0
            )
        self.assertEqual([b["protein_g"] for b in after.values()], [12.0, 12.0, 12.0])
        # Left visible rather than silently absorbed, same as the
        # overspent-override branch in `split_targets`.
        self.assertIn("protein floor", logged.output[0])

    def test_a_single_slot_is_left_alone(self):
        """It already holds the whole flexible remainder — there is nowhere to
        move grams from."""
        budgets = {"Monday:dinner": {"calories": 400.0, "protein_g": 20.0,
                                     "net_carbs_g": 10.0, "fat_g": 20.0}}
        after = planner.apply_protein_floor(budgets, [cook_slot("dinner")], {}, 35.0)
        self.assertEqual(after["Monday:dinner"]["protein_g"], 20.0)

    def test_a_batch_meal_is_weighed_by_what_it_costs_the_day(self):
        """A lunch eaten twice spends twice its budget, so its shortfall against
        the floor is twice as expensive to fix."""
        slots = [cook_slot("lunch"), cook_slot("dinner")]
        budgets = {
            "Monday:lunch": {"calories": 400.0, "protein_g": 30.0, "net_carbs_g": 10.0, "fat_g": 20.0},
            "Monday:dinner": {"calories": 800.0, "protein_g": 60.0, "net_carbs_g": 20.0, "fat_g": 40.0},
        }
        after = planner.apply_protein_floor(
            budgets, slots, {"Monday:lunch": 2, "Monday:dinner": 1}, 35.0
        )
        self.assertAlmostEqual(after["Monday:lunch"]["protein_g"], 35.0, places=6)
        # 5 g short x 2 servings = 10 g off dinner, which had 25 g to spare.
        self.assertAlmostEqual(after["Monday:dinner"]["protein_g"], 50.0, places=6)


class TestSplitTargetsAppliesTheFloor(unittest.TestCase):
    """The floor is reached through `split_targets`, not called directly."""

    def setUp(self):
        self.config = {
            "meal_weights": {"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
            "planning_rules": dict(planner.DEFAULT_PLANNING_RULES),
        }
        self.slots = [cook_slot(m) for m in ("breakfast", "lunch", "dinner", "snack")]
        self.remaining = {"calories": 1910.0, "protein_g": 144.0,
                          "net_carbs_g": 60.0, "fat_g": 121.6}

    def test_the_snack_is_lifted_off_its_weighted_share(self):
        budgets = planner.split_targets(
            self.remaining, self.slots, {}, self.config, {}
        )
        # 0.10 of 144 g is 14.4 — a snack with no protein source in it.
        self.assertGreaterEqual(budgets["Monday:snack"]["protein_g"], 35.0)

    def test_a_leftover_slot_is_not_floored(self):
        """Its protein comes from the recipe its source cooked, not a budget."""
        slots = [cook_slot("lunch"), cook_slot("dinner"), cook_slot("snack", MODE_LEFTOVER)]
        budgets = planner.split_targets(self.remaining, slots, {}, self.config, {})
        self.assertLess(budgets["Monday:snack"]["protein_g"], 35.0)

    def test_a_pinned_meal_keeps_its_verbatim_budget(self):
        overrides = {"breakfast": {"calories": 350.0, "protein_g": 30.0,
                                   "net_carbs_g": 25.0, "fat_g": 12.0}}
        budgets = planner.split_targets(
            self.remaining, self.slots, {}, self.config, overrides
        )
        self.assertEqual(budgets["Monday:breakfast"]["protein_g"], 30.0)
        self.assertGreaterEqual(budgets["Monday:snack"]["protein_g"], 35.0)


class TestLoggedIntakeFor(unittest.TestCase):
    """Today's Cronometer row, or None — never a guess."""

    # A Sunday, so the weekday check has something to match and mismatch.
    NOW = datetime(2026, 8, 16, 17, 0)
    ROW = {"date": "2026-08-16", "calories": 1120, "protein_g": 98,
           "net_carbs_g": 40, "fat_g": 45}

    def test_todays_row_is_returned(self):
        logged = planner.logged_intake_for("Sunday", {"daily_actuals": [self.ROW]}, self.NOW)
        self.assertEqual(logged["calories"], 1120.0)
        self.assertEqual(logged["protein_g"], 98.0)

    def test_another_weekday_is_not_today(self):
        """A SlotSpec carries only a weekday name, so a Thursday being planned
        ahead is not the Thursday that was logged."""
        self.assertIsNone(
            planner.logged_intake_for("Thursday", {"daily_actuals": [self.ROW]}, self.NOW)
        )

    def test_a_stale_row_is_ignored(self):
        stale = dict(self.ROW, date="2026-08-09")
        self.assertIsNone(
            planner.logged_intake_for("Sunday", {"daily_actuals": [stale]}, self.NOW)
        )

    def test_an_all_zero_row_reads_as_no_data(self):
        """A dated shell from a partial sync must not be read as 'you have
        eaten nothing today', which would hand one meal the whole day."""
        shell = {"date": "2026-08-16", "calories": 0}
        self.assertIsNone(
            planner.logged_intake_for("Sunday", {"daily_actuals": [shell]}, self.NOW)
        )

    def test_missing_macros_are_zero_not_a_dropped_row(self):
        partial = {"date": "2026-08-16", "calories": 900, "protein_g": 70}
        logged = planner.logged_intake_for("Sunday", {"daily_actuals": [partial]}, self.NOW)
        self.assertEqual(logged["calories"], 900.0)
        self.assertEqual(logged["fat_g"], 0.0)

    def test_no_biometrics_at_all(self):
        self.assertIsNone(planner.logged_intake_for("Sunday", None, self.NOW))
        self.assertIsNone(planner.logged_intake_for("Sunday", {}, self.NOW))


if __name__ == "__main__":
    unittest.main()
-e 

=== File: tests/test_nutrition_engine.py ===
"""Tests for `src/nutrition_engine.py`.

Written against `unittest` from the standard library rather than pytest, which
this venv doesn't carry — `python -m unittest discover tests` runs them with no
new dependency. They are plain `TestCase` classes, so they also run untouched
under `python -m pytest` if pytest is ever added to requirements.txt.

`src/` is not a package (see CLAUDE.md), so the import below puts it on
`sys.path` the same way `python src/planner.py` does.
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import nutrition_engine as ne  # noqa: E402  (path setup must precede the import)


# The profile the app actually ships in data/config.json, at the top of the
# journey: 100 kg heading for 80 kg. Every expected number below is worked out
# by hand from these, so a change to a constant fails loudly rather than
# quietly re-baselining itself against the code it is meant to check.
PROFILE = {
    "birth_date": "1971-01-10",
    "height_cm": 183,
    "gender": "male",
    "target_weight_kg": 80.0,
    "protein_multiplier": 1.8,
    "activity_level": "light_office",
}
AGE_55 = date(2026, 8, 16)


class TestAgeFromBirthDate(unittest.TestCase):
    def test_age_on_reference_date(self):
        self.assertEqual(ne.age_from_birth_date("1971-01-10", AGE_55), 55)

    def test_birthday_not_yet_reached_this_year(self):
        """The day before a birthday must still be the younger age."""
        self.assertEqual(ne.age_from_birth_date("1971-01-10", date(2026, 1, 9)), 54)
        self.assertEqual(ne.age_from_birth_date("1971-01-10", date(2026, 1, 10)), 55)

    def test_malformed_date_raises(self):
        with self.assertRaises(ValueError):
            ne.age_from_birth_date("10/01/1971")


class TestBMR(unittest.TestCase):
    def test_mifflin_st_jeor_male(self):
        # 10*100 + 6.25*183 - 5*55 + 5 = 1000 + 1143.75 - 275 + 5
        self.assertAlmostEqual(
            ne.calculate_bmr(weight_kg=100, height_cm=183, age=55, gender="male"),
            1873.75,
        )

    def test_mifflin_st_jeor_female_differs_by_the_sex_constant(self):
        male = ne.calculate_bmr(weight_kg=100, height_cm=183, age=55, gender="male")
        female = ne.calculate_bmr(weight_kg=100, height_cm=183, age=55, gender="female")
        self.assertAlmostEqual(male - female, 166.0)  # +5 vs -161

    def test_katch_mcardle_used_when_body_fat_known(self):
        # LBM = 100 * (1 - 0.25) = 75kg; 370 + 21.6*75 = 370 + 1620
        self.assertAlmostEqual(
            ne.calculate_bmr(
                weight_kg=100, height_cm=183, age=55, body_fat_pct=25.0
            ),
            1990.0,
        )

    def test_katch_ignores_height_age_and_gender(self):
        """Not incidental — lean mass already encodes what they proxy for."""
        baseline = ne.calculate_bmr(
            weight_kg=100, height_cm=183, age=55, gender="male", body_fat_pct=25.0
        )
        self.assertAlmostEqual(
            ne.calculate_bmr(
                weight_kg=100, height_cm=150, age=20, gender="female", body_fat_pct=25.0
            ),
            baseline,
        )

    def test_impossible_body_fat_rejected_rather_than_used(self):
        """A scale reporting 0% would otherwise read as pure lean mass and
        inflate BMR by hundreds of kcal."""
        for bad in (0.0, 2.9, 70.1, 95.0):
            with self.assertRaises(ValueError):
                ne.calculate_bmr(
                    weight_kg=100, height_cm=183, age=55, body_fat_pct=bad
                )

    def test_missing_height_raises_on_the_mifflin_path(self):
        with self.assertRaises(ValueError):
            ne.calculate_bmr(weight_kg=100, height_cm=None, age=55)

    def test_unknown_gender_raises(self):
        with self.assertRaises(ValueError):
            ne.calculate_bmr(weight_kg=100, height_cm=183, age=55, gender="mail")


class TestTDEE(unittest.TestCase):
    def test_activity_factors(self):
        for level, factor in (
            ("sedentary", 1.2),
            ("light_office", 1.375),
            ("moderate", 1.55),
        ):
            self.assertAlmostEqual(ne.calculate_tdee(2000, level), 2000 * factor)

    def test_default_is_light_office(self):
        self.assertAlmostEqual(ne.calculate_tdee(2000), 2750.0)

    def test_unknown_level_raises_rather_than_defaulting(self):
        """A typo'd activity level must not silently cost ~350 kcal/day."""
        with self.assertRaises(ValueError):
            ne.calculate_tdee(2000, "lightly_active")


class TestDynamicDeficit(unittest.TestCase):
    def test_full_deficit_at_and_above_the_ceiling_weight(self):
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(100.0, 80.0), 750.0)
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(120.0, 80.0), 750.0)

    def test_floor_deficit_at_and_below_target(self):
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(80.0, 80.0), 350.0)
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(74.0, 80.0), 350.0)

    def test_midpoint_is_halfway_between_the_anchors(self):
        # 90kg is halfway from 80 -> 100, so halfway from 350 -> 750.
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(90.0, 80.0), 550.0)

    def test_deficit_falls_monotonically_as_weight_falls(self):
        weights = [100.0, 97.5, 95.0, 92.5, 90.0, 87.5, 85.0, 82.5, 80.0]
        deficits = [ne.calculate_dynamic_deficit(w, 80.0) for w in weights]
        self.assertEqual(deficits, sorted(deficits, reverse=True))
        self.assertEqual(deficits[0], 750.0)
        self.assertEqual(deficits[-1], 350.0)

    def test_target_above_the_ceiling_weight_does_not_divide_by_zero(self):
        """A 105kg target leaves no ramp between target and the 100kg anchor."""
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(110.0, 105.0), 750.0)
        self.assertAlmostEqual(ne.calculate_dynamic_deficit(100.0, 105.0), 350.0)


class TestDeriveFatG(unittest.TestCase):
    def test_fat_spends_the_remaining_energy(self):
        # 2000 - (150*4 + 60*4) = 2000 - 840 = 1160; /9
        self.assertAlmostEqual(ne.derive_fat_g(2000, 150, 60), 1160 / 9)

    def test_macros_reconcile_back_to_the_calorie_figure(self):
        fat = ne.derive_fat_g(1826, 144, 60)
        self.assertAlmostEqual(144 * 4 + 60 * 4 + fat * 9, 1826)

    def test_floored_at_zero_when_protein_and_carbs_overspend(self):
        self.assertEqual(ne.derive_fat_g(500, 150, 60), 0.0)


class TestMacroTargets(unittest.TestCase):
    """The headline case: 55 years old, 100 kg, aiming at 80 kg."""

    def setUp(self):
        self.result = ne.calculate_macro_targets(
            PROFILE, {"date": "2026-08-16", "weight_kg": 100.0}
        )

    def test_protein_is_locked_to_target_weight(self):
        # 80 * 1.8 -- the number the whole protein-floor idea rests on.
        self.assertAlmostEqual(self.result["protein_g"], 144.0)

    def test_protein_does_not_move_when_current_weight_does(self):
        """The floor must not sink as the scale does."""
        for weight in (100.0, 95.0, 88.0, 81.0):
            result = ne.calculate_macro_targets(PROFILE, {"weight_kg": weight})
            self.assertAlmostEqual(result["protein_g"], 144.0)

    def test_calories_are_tdee_minus_the_dynamic_deficit(self):
        # BMR 1873.75 -> TDEE *1.375 = 2576.40625 -> less 750 at 100kg
        self.assertAlmostEqual(self.result["basis"]["bmr"], 1873.8, places=1)
        self.assertAlmostEqual(self.result["basis"]["tdee"], 2576.4, places=1)
        self.assertAlmostEqual(self.result["basis"]["deficit_kcal"], 750.0)
        self.assertEqual(self.result["calories"], round(2576.40625 - 750))

    def test_carbs_default_to_the_baseline(self):
        self.assertAlmostEqual(self.result["net_carbs_g"], ne.DEFAULT_NET_CARBS_G)

    def test_schedule_can_override_carbs(self):
        loaded = ne.calculate_macro_targets(
            PROFILE, {"weight_kg": 100.0}, net_carbs_g=130.0
        )
        self.assertAlmostEqual(loaded["net_carbs_g"], 130.0)

    def test_the_four_macros_reconcile(self):
        macros = {key: self.result[key] for key in ne.MACRO_KEYS}
        spent = (
            macros["protein_g"] * 4 + macros["net_carbs_g"] * 4 + macros["fat_g"] * 9
        )
        # Within a kcal, the rounding applied to the reported grams.
        self.assertAlmostEqual(spent, macros["calories"], delta=1.0)

    def test_result_carries_exactly_the_macro_keys_plus_basis(self):
        """`DaySchedule` is extra='forbid', so diagnostics must stay nested."""
        self.assertEqual(set(self.result), set(ne.MACRO_KEYS) | {"basis"})

    def test_body_fat_reading_switches_to_katch_mcardle(self):
        result = ne.calculate_macro_targets(
            PROFILE, {"weight_kg": 100.0, "body_fat_pct": 25.0}
        )
        self.assertEqual(result["basis"]["bmr_method"], "katch_mcardle")
        self.assertAlmostEqual(result["basis"]["bmr"], 1990.0)

    def test_zero_body_fat_reading_falls_back_to_mifflin(self):
        """Scales write 0 rather than omitting the key when they can't read."""
        result = ne.calculate_macro_targets(
            PROFILE, {"weight_kg": 100.0, "body_fat_pct": 0}
        )
        self.assertEqual(result["basis"]["bmr_method"], "mifflin_st_jeor")

    def test_deficit_shrinks_as_the_target_nears(self):
        far = ne.calculate_macro_targets(PROFILE, {"weight_kg": 100.0})
        near = ne.calculate_macro_targets(PROFILE, {"weight_kg": 82.0})
        self.assertGreater(
            far["basis"]["deficit_kcal"], near["basis"]["deficit_kcal"]
        )
        self.assertAlmostEqual(near["basis"]["deficit_kcal"], 390.0)

    def test_missing_weight_raises_rather_than_inventing_a_body(self):
        with self.assertRaises(ValueError):
            ne.calculate_macro_targets(PROFILE, None)

    def test_missing_age_and_body_fat_raises(self):
        profile = dict(PROFILE)
        del profile["birth_date"]
        with self.assertRaises(ValueError):
            ne.calculate_macro_targets(profile, {"weight_kg": 100.0})

    def test_body_fat_covers_for_a_profile_with_no_birth_date(self):
        """Katch needs neither age nor height, so this must not raise."""
        profile = dict(PROFILE)
        del profile["birth_date"]
        del profile["height_cm"]
        result = ne.calculate_macro_targets(
            profile, {"weight_kg": 100.0, "body_fat_pct": 25.0}
        )
        self.assertAlmostEqual(result["basis"]["bmr"], 1990.0)


def _series(start_weight, kg_lost, days, calories, noise=None):
    """A weigh-in/log pair describing a steady `kg_lost` over `days`."""
    first = date(2026, 8, 1)
    noise = noise or [0.0] * (days + 1)
    weigh_ins = [
        {
            "date": str(first + timedelta(days=i)),
            "weight_kg": start_weight - kg_lost * (i / days) + noise[i],
        }
        for i in range(days + 1)
    ]
    logs = [
        {"date": str(first + timedelta(days=i)), "calories": calories}
        for i in range(days + 1)
    ]
    return logs, weigh_ins


class TestAdaptiveTDEE(unittest.TestCase):
    def test_recovers_the_true_expenditure_from_a_clean_series(self):
        """2000 kcal/day while losing 1kg/14 days => 2000 + 7700/14."""
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, weigh_ins), 2550.0, places=1
        )

    def test_is_unbiased_where_smoothed_endpoints_were_not(self):
        """Guards the regression fix: reading the trend off an exponentially
        smoothed series' endpoints returned 2405.7 for this exact input, a
        144 kcal/day understatement on noise-free data."""
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        estimate = ne.calculate_adaptive_tdee(logs, weigh_ins)
        self.assertGreater(estimate, 2500.0)

    def test_gaining_weight_reads_as_expenditure_below_intake(self):
        """Sign check — inverting the delta doubles the error, so this is the
        test that catches it."""
        logs, weigh_ins = _series(100.0, -1.0, 14, 3000)
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, weigh_ins), 3000 - 7700 / 14, places=1
        )

    def test_maintenance_reads_back_as_the_logged_intake(self):
        logs, weigh_ins = _series(100.0, 0.0, 14, 2400)
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, weigh_ins), 2400.0, places=1
        )

    def test_none_when_there_are_too_few_weigh_ins(self):
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        self.assertIsNone(ne.calculate_adaptive_tdee(logs, weigh_ins[:1]))
        self.assertIsNone(ne.calculate_adaptive_tdee(logs, []))

    def test_none_when_the_span_is_too_short_to_read(self):
        """A 3-day span multiplied by 7700 is noise amplification."""
        logs, weigh_ins = _series(100.0, 0.3, 3, 2000)
        self.assertIsNone(ne.calculate_adaptive_tdee(logs, weigh_ins))

    def test_none_when_no_calories_were_logged(self):
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        self.assertIsNone(ne.calculate_adaptive_tdee([], weigh_ins))
        undated = [{"weight_kg": 99.0} for _ in range(5)]
        self.assertIsNone(ne.calculate_adaptive_tdee(logs, undated))

    def test_window_excludes_older_weigh_ins(self):
        """A 60-day series read with window_days=14 must use only the last
        fortnight, so an old, faster stretch cannot inflate the estimate."""
        logs, weigh_ins = _series(110.0, 10.0, 56, 2000)
        narrow = ne.calculate_adaptive_tdee(logs, weigh_ins, window_days=14)
        wide = ne.calculate_adaptive_tdee(logs, weigh_ins, window_days=56)
        # Same underlying rate here, so both land together -- what is being
        # checked is that the narrow window still produces an estimate at all.
        self.assertIsNotNone(narrow)
        self.assertAlmostEqual(narrow, wide, delta=1.0)

    def test_irregular_weighing_gives_the_same_rate_as_daily(self):
        """The regression runs on real elapsed days, so skipping days must not
        change the answer."""
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        sparse = [weigh_ins[i] for i in (0, 3, 4, 9, 13, 14)]
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, sparse),
            ne.calculate_adaptive_tdee(logs, weigh_ins),
            places=1,
        )

    def test_malformed_rows_are_skipped_not_fatal(self):
        """biometrics.json is hand-editable; one bad row must cost that row."""
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000)
        weigh_ins.insert(3, {"date": "not-a-date", "weight_kg": 40.0})
        weigh_ins.insert(6, {"weight_kg": 40.0})
        logs.insert(2, {"date": "2026-08-03", "calories": None})
        self.assertAlmostEqual(
            ne.calculate_adaptive_tdee(logs, weigh_ins), 2550.0, places=1
        )

    def test_scale_noise_is_damped_relative_to_endpoint_differencing(self):
        """The worst case for a fitted trend: both noise spikes land on the
        endpoints, where leverage is highest, and push the same way — a
        dehydrated first morning and a heavy last one. Differencing those two
        weigh-ins sees the whole 1kg loss cancel and reports maintenance, a
        550 kcal/day error. The fit uses all 15 points, so the same input
        costs it ~193.

        Asserted as a ratio against the naive estimator rather than as a fixed
        tolerance, because what the design claims is *relative*: fitting the
        series beats differencing its ends. A bare delta would either encode
        today's arithmetic as a magic number or pass for the wrong reason.
        """
        noise = [0.0] * 15
        noise[0] = -0.5   # dehydrated on the first morning
        noise[14] = 0.5   # heavy on the last
        logs, weigh_ins = _series(100.0, 1.0, 14, 2000, noise=noise)

        true_tdee = 2000 + 7700 / 14
        naive = 2000 + (
            weigh_ins[0]["weight_kg"] - weigh_ins[-1]["weight_kg"]
        ) * 7700 / 14
        estimate = ne.calculate_adaptive_tdee(logs, weigh_ins)

        self.assertAlmostEqual(naive, 2000.0, places=1)  # the trend, erased
        self.assertLess(abs(estimate - true_tdee), abs(naive - true_tdee) / 2)


class TestSmoothSeries(unittest.TestCase):
    def test_smoothing_reduces_variation_but_keeps_the_level(self):
        raw = [100.0, 101.0, 99.0, 100.5, 99.5, 100.0]
        smoothed = ne.smooth_series(raw)
        self.assertEqual(len(smoothed), len(raw))
        spread = max(smoothed) - min(smoothed)
        self.assertLess(spread, max(raw) - min(raw))
        self.assertAlmostEqual(sum(smoothed) / len(smoothed), 100.0, delta=0.5)

    def test_empty_series(self):
        self.assertEqual(ne.smooth_series([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
-e 


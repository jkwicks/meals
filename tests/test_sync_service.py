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
import os
import sys
import tempfile
import unittest
import unittest.mock
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


class TestSyncDateRange(unittest.TestCase):
    """`get_sync_date_range` — the missing-days-between-last-sync-and-target walk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "biometrics.json")
        self.repo = LocalJSONRepository(biometrics_path=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_biometrics_falls_back_to_the_target_date_alone(self):
        """A fresh checkout has nothing to walk back from."""
        self.assertEqual(
            sync.get_sync_date_range(self.repo, "2026-08-21"), ["2026-08-21"]
        )

    def test_missing_days_are_filled_in_between(self):
        """A missed Monday and Tuesday, run on Wednesday, backfills both."""
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-18", "weight_kg": 90.0}))
        self.assertEqual(
            sync.get_sync_date_range(self.repo, "2026-08-21"),
            ["2026-08-19", "2026-08-20", "2026-08-21"],
        )

    def test_the_latest_date_is_taken_across_both_lists(self):
        """A Garmin-stale, Cronometer-fresh database still catches Garmin up.

        `get_latest_biometrics` only reads `weigh_ins`; this has to read both,
        or a checkout that syncs Cronometer daily and Garmin occasionally
        would compute Garmin's gap from Cronometer's more recent date.
        """
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-15", "weight_kg": 90.0}))
        run_sync(self.repo.save_daily_actuals({"date": "2026-08-18", "calories": 2000.0}))
        self.assertEqual(
            sync.get_sync_date_range(self.repo, "2026-08-20"),
            ["2026-08-19", "2026-08-20"],
        )

    def test_already_up_to_date_returns_an_empty_range(self):
        """The latest record equalling the target means nothing is missing."""
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-21", "weight_kg": 90.0}))
        self.assertEqual(sync.get_sync_date_range(self.repo, "2026-08-21"), [])

    def test_a_stale_database_is_capped_at_max_lookback_days(self):
        """A months-old database doesn't queue hundreds of sequential calls."""
        run_sync(self.repo.save_biometric_entry({"date": "2026-01-01", "weight_kg": 90.0}))
        result = sync.get_sync_date_range(self.repo, "2026-08-21", max_lookback_days=5)
        self.assertEqual(
            result,
            ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"],
        )

    def test_malformed_target_date_is_rejected(self):
        with self.assertRaises(ValueError):
            sync.get_sync_date_range(self.repo, "21/08/2026")


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


class CountingGarminClient(FakeGarminClient):
    """A `FakeGarminClient` that records which dates it was asked for."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.body_composition_calls = []

    def get_body_composition(self, startdate, enddate=None):
        self.body_composition_calls.append(startdate)
        return super().get_body_composition(startdate, enddate)


class FlakyGarminClient(FakeGarminClient):
    """A `FakeGarminClient` that raises for one specific date, as a bad API
    call on one day of a multi-day catchup would."""

    def __init__(self, fail_on, **kwargs):
        super().__init__(**kwargs)
        self.fail_on = fail_on

    def get_body_composition(self, startdate, enddate=None):
        if startdate == self.fail_on:
            raise RuntimeError("garmin unavailable")
        return super().get_body_composition(startdate, enddate)


@contextlib.contextmanager
def replace_cronometer_service(fake_fetch):
    """Make `sync_cronometer` build a service whose `fetch_daily_summary` is `fake_fetch`.

    Same reasoning as `replace_garmin_client`: `sync_cronometer` constructs
    its own `CronometerSyncService`, so the seam has to be the class, not an
    injected instance. `fake_fetch` takes the ISO date string and returns the
    `daily_actuals` row `fetch_daily_summary` would have.
    """
    original = sync.CronometerSyncService

    class Patched(original):
        def __init__(self, *args, **kwargs):
            super().__init__(username="user@example.com", password="pw")

        def fetch_daily_summary(self, target_date):
            return fake_fetch(target_date)

    sync.CronometerSyncService = Patched
    try:
        yield
    finally:
        sync.CronometerSyncService = original


class TestGarminRangeSync(unittest.TestCase):
    """`sync_garmin_range` — the sequential per-date catchup walk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "biometrics.json")
        self.repo = LocalJSONRepository(biometrics_path=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_missing_date_is_fetched_and_persisted(self):
        fake = CountingGarminClient(weight_list=[FULL_READING])
        dates = ["2026-08-19", "2026-08-20", "2026-08-21"]

        with replace_garmin_client(fake):
            results = sync.sync_garmin_range(dates, self.repo)

        self.assertEqual(fake.body_composition_calls, dates)
        self.assertEqual([r["date"] for r in results], dates)
        weigh_ins = run_sync(self.repo.load_biometrics())["weigh_ins"]
        self.assertEqual(sorted(row["date"] for row in weigh_ins), dates)

    def test_one_days_failure_does_not_abort_the_rest_of_the_range(self):
        """A network blip on one date must not cost the days around it."""
        fake = FlakyGarminClient(fail_on="2026-08-20", weight_list=[FULL_READING])
        dates = ["2026-08-19", "2026-08-20", "2026-08-21"]

        with replace_garmin_client(fake):
            results = sync.sync_garmin_range(dates, self.repo)

        self.assertNotIn("error", results[0])
        self.assertIn("error", results[1])
        self.assertEqual(results[1]["date"], "2026-08-20")
        self.assertNotIn("error", results[2])

        weigh_ins = run_sync(self.repo.load_biometrics())["weigh_ins"]
        self.assertEqual(
            sorted(row["date"] for row in weigh_ins), ["2026-08-19", "2026-08-21"]
        )


class TestCronometerRangeSync(unittest.TestCase):
    """`sync_cronometer_range` — the sequential per-date catchup walk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "biometrics.json")
        self.repo = LocalJSONRepository(biometrics_path=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_missing_date_is_fetched_and_persisted(self):
        calls = []

        def fake_fetch(day):
            calls.append(day)
            return {
                "date": day,
                "calories": 2000.0,
                "protein_g": 150.0,
                "net_carbs_g": 100.0,
                "fat_g": 70.0,
                "source": "cronometer",
            }

        dates = ["2026-08-19", "2026-08-20"]
        with replace_cronometer_service(fake_fetch):
            results = sync.sync_cronometer_range(dates, self.repo)

        self.assertEqual(calls, dates)
        self.assertEqual([r["date"] for r in results], dates)
        actuals = run_sync(self.repo.load_biometrics())["daily_actuals"]
        self.assertEqual(sorted(row["date"] for row in actuals), dates)

    def test_one_days_failure_does_not_abort_the_rest_of_the_range(self):
        def fake_fetch(day):
            if day == "2026-08-20":
                raise RuntimeError("cronometer unavailable")
            return {"date": day, "calories": 2000.0, "source": "cronometer"}

        dates = ["2026-08-19", "2026-08-20", "2026-08-21"]
        with replace_cronometer_service(fake_fetch):
            results = sync.sync_cronometer_range(dates, self.repo)

        self.assertNotIn("error", results[0])
        self.assertIn("error", results[1])
        self.assertNotIn("error", results[2])

        actuals = run_sync(self.repo.load_biometrics())["daily_actuals"]
        self.assertEqual(
            sorted(row["date"] for row in actuals), ["2026-08-19", "2026-08-21"]
        )


class TestCredentialGuards(unittest.TestCase):
    """An explicitly-empty credential must stay empty.

    These tests all run with a *populated* environment, which is the condition
    the original version of this file got wrong. `CronometerSyncService(
    username="")` used to fall through `username or os.environ.get(...)` to
    the developer's real `.env` credentials, sail past `_require_credentials`
    and issue a genuine authenticated request to cronometer.com — so the one
    test asserting "fails before any call" was the only test in the suite that
    made a network call, and passed only on a machine with no `.env`.

    Patching the environment to hold obviously-fake credentials is what makes
    the assertion mean something: if the `or` ever comes back, these fail
    loudly instead of silently going online.
    """

    def setUp(self):
        self._patched = unittest.mock.patch.dict(
            os.environ,
            {
                "CRONOMETER_USERNAME": "env-user@example.invalid",
                "CRONOMETER_PASSWORD": "env-password",
                "GARMIN_EMAIL": "env-user@example.invalid",
                "GARMIN_PASSWORD": "env-password",
            },
        )
        self._patched.start()
        self.addCleanup(self._patched.stop)

    def test_empty_cronometer_credentials_do_not_fall_back_to_the_environment(self):
        service = sync.CronometerSyncService(username="", password="")
        self.assertEqual(service.username, "")
        self.assertEqual(service.password, "")

        with self.assertRaises(RuntimeError) as caught:
            service.fetch_daily_summary("2026-08-16")
        self.assertIn("CRONOMETER_USERNAME", str(caught.exception))

    def test_empty_garmin_credentials_do_not_fall_back_to_the_environment(self):
        service = sync.GarminSyncService(email="", password="")
        self.assertEqual(service.email, "")
        self.assertEqual(service.password, "")

    def test_omitted_credentials_still_read_the_environment(self):
        """The other half of the guard: `None` still means "use .env"."""
        cronometer = sync.CronometerSyncService()
        self.assertEqual(cronometer.username, "env-user@example.invalid")
        self.assertEqual(cronometer.password, "env-password")

        garmin = sync.GarminSyncService()
        self.assertEqual(garmin.email, "env-user@example.invalid")
        self.assertEqual(garmin.password, "env-password")

    def test_supplied_credentials_win_over_the_environment(self):
        service = sync.CronometerSyncService(username="explicit", password="explicit-pw")
        self.assertEqual(service.username, "explicit")
        self.assertEqual(service.password, "explicit-pw")


if __name__ == "__main__":
    unittest.main()

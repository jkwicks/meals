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
from datetime import date, timedelta
from pathlib import Path

import requests

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(_SRC / "integrations"))

import planner  # noqa: E402
import sync_service as sync  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402


class FakeGarminClient:
    """The subset of `garminconnect.Garmin` the service actually calls.

    Weights are in grams and sleep in seconds, which is what Garmin really
    sends — the unit conversion is one of the things most likely to regress.
    """

    def __init__(self, weight_list=None, activities=None, sleep=None, hrv=None):
        self._weight_list = weight_list
        self._activities = activities if activities is not None else []
        self._sleep = sleep
        self._hrv = hrv

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

    def get_hrv_data(self, cdate):
        """The second readiness endpoint, and a separate failure.

        Sleep and HRV are fetched independently by the service, so the fake
        has to be able to fail one without the other — that isolation is the
        thing being tested, not an incidental detail of the fake.
        """
        if self._hrv is None:
            raise RuntimeError("no hrv data")
        return self._hrv


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


# `startTimeLocal` is the watch's own clock, and the field this module reads;
# `startTimeGMT` sits beside it in the real payload and is here because it is
# what a careless mapping would grab instead — it is silently wrong by the
# timezone offset, which no downstream reader could detect.
ACTIVITIES = [
    {
        "activityId": 1,
        "activityName": "Elliptical",
        "activityType": {"typeKey": "elliptical"},
        "calories": 640,
        "duration": 3600,
        "averageHR": 132,
        "startTimeLocal": "2026-08-16 06:12:33",
        "startTimeGMT": "2026-08-15 20:12:33",
    },
    {
        "activityId": 2,
        "activityName": "Morning lift",
        "activityType": {"typeKey": "strength_training"},
        "calories": 300,
        "duration": 2700,
        "startTimeLocal": "2026-08-16 05:31:00",
    },
    {
        "activityId": 3,
        "activityName": "Treadmill",
        "activityType": {"typeKey": "treadmill_running"},
        "calories": 500,
        "duration": 1800,
        "startTimeLocal": "2026-08-16 17:45:10",
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


class TestActivityMapping(unittest.TestCase):
    """`fetch_activities` and the two derived fields the schedule proposal
    reads. The cardio filter above is a *report*; this is the stored list, and
    it deliberately keeps the strength session that filter drops — a lift is
    the session a proposal most needs, since it is what pins a breakfast
    shake."""

    def sessions(self):
        return garmin_service(activities=ACTIVITIES).fetch_activities("2026-08-16")

    def test_strength_work_is_kept_where_the_cardio_filter_drops_it(self):
        self.assertIn("strength_training", [s["type"] for s in self.sessions()])

    def test_garmin_types_map_to_the_apps_own_vocabulary(self):
        self.assertEqual(
            [s["session_type"] for s in self.sessions()],
            ["cardio_easy", "gym_hypertrophy", "cardio_run"],
        )

    def test_the_longest_matching_key_wins(self):
        """`indoor_cycling` must reach `cardio_ride`, not stop at a shorter
        key that happens to appear in it — the same longest-prefix rule
        `MET_VALUES` and `training_icon` use."""
        self.assertEqual(sync._session_type("indoor_cycling"), "cardio_ride")
        self.assertEqual(sync._session_type("treadmill_running"), "cardio_run")
        self.assertEqual(sync._session_type("virtual_ride"), "cardio_ride")

    def test_an_unknown_modality_maps_to_nothing_rather_than_a_neighbour(self):
        """A yoga class offered as "Cardio Easy, 45 min, 260 kcal" is a wrong
        answer that looks like a right one, and a proposal is a sentence the
        user is asked to agree to."""
        self.assertIsNone(sync._session_type("yoga"))

    def test_start_time_is_the_local_clock_not_gmt(self):
        """A GMT time is silently wrong by the timezone offset — the same
        class of unit error as storing Garmin's grams as kilograms."""
        self.assertEqual(self.sessions()[0]["start_time"], "06:12")

    def test_an_unreadable_timestamp_is_none_rather_than_midnight(self):
        """00:00 is not a neutral default here: `morning_training_days` reads
        a time before 11:00 as a pre-dawn session and would pin a breakfast
        shake to a day nobody trained in the morning."""
        self.assertIsNone(sync._local_start_time(None))
        self.assertIsNone(sync._local_start_time("16 Aug 2026, 6am"))

    def test_the_cardio_report_is_a_filter_over_the_same_fetch(self):
        """One login answering one question twice is a request this account
        does not need to spend, and two fetches would be free to disagree."""
        cardio = garmin_service(activities=ACTIVITIES).fetch_cardio_activities(
            "2026-08-16"
        )
        self.assertEqual(
            cardio, [s for s in self.sessions() if s["type"] != "strength_training"]
        )


SLEEP = {
    "dailySleepDTO": {
        "sleepScores": {"overall": {"value": 83}},
        "sleepTimeSeconds": 26400,
    }
}

# `/hrv-service/hrv/{date}`'s shape, as the installed garminconnect 0.3.10
# returns it. `lastNightAvg` is the figure a morning readiness read is about;
# the other two are here because they are what a careless mapping would grab
# instead — a weekly average would store one number under seven dates.
HRV = {
    "hrvSummary": {
        "lastNightAvg": 42,
        "lastNight5MinHigh": 61,
        "weeklyAvg": 39,
        "status": "BALANCED",
    }
}


class TestReadiness(unittest.TestCase):
    def test_sleep_score_is_reported(self):
        readiness = garmin_service(sleep=SLEEP, hrv=HRV).fetch_readiness("2026-08-16")
        self.assertEqual(readiness["sleep_score"], 83.0)
        self.assertEqual(readiness["readiness_label"], "excellent")
        self.assertEqual(readiness["sleep_hours"], 7.33)

    def test_hrv_is_last_nights_average_not_the_weekly_one(self):
        """`lastNightAvg`, not `weeklyAvg` or `lastNight5MinHigh`.

        The row is keyed by date, so a weekly figure would store the same
        number under seven dates and read as seven measurements; a five-minute
        peak answers a different question from the one a morning readiness
        read asks. All three are in the payload, which is why this is worth
        pinning rather than assuming.
        """
        readiness = garmin_service(sleep=SLEEP, hrv=HRV).fetch_readiness("2026-08-16")
        self.assertEqual(readiness["hrv_ms"], 42.0)

    def test_readiness_carries_no_energy_figure(self):
        """The rule this method exists to enforce, and storing the row does
        not relax it.

        A sleep score is a unitless index and HRV is milliseconds; anything
        here that looked like kcal would be an invitation to add it to a
        day's energy, and there is no conversion that would make that
        legitimate.
        """
        readiness = garmin_service(sleep=SLEEP, hrv=HRV).fetch_readiness("2026-08-16")
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
        readiness = garmin_service(sleep=None, hrv=None).fetch_readiness("2026-08-16")
        self.assertEqual(readiness, {"date": "2026-08-16"})

    def test_a_sleep_failure_keeps_an_hrv_reading_that_worked(self):
        """The two endpoints are caught separately, and this is why.

        One `try` around both would discard a perfectly good HRV reading
        because the sleep call happened to fail — and `save_readiness_entry`
        merges by date, so the half that failed lands on a later re-sync
        rather than being owed forever.
        """
        readiness = garmin_service(sleep=None, hrv=HRV).fetch_readiness("2026-08-16")
        self.assertEqual(readiness["hrv_ms"], 42.0)
        self.assertNotIn("sleep_score", readiness)

    def test_an_hrv_failure_keeps_the_sleep_reading(self):
        readiness = garmin_service(sleep=SLEEP, hrv=None).fetch_readiness("2026-08-16")
        self.assertEqual(readiness["sleep_score"], 83.0)
        self.assertNotIn("hrv_ms", readiness)

    def test_a_night_with_nothing_measured_is_not_a_row(self):
        """`_prune` plus `has_measurements`, the same pair the weigh-in uses.

        A row of Nones tagged `source: garmin` would look like a measured
        night to anything counting rows, which is exactly the failure
        `has_measurements` was written for on the weigh-in side.
        """
        readiness = garmin_service(sleep=None, hrv=None).fetch_readiness("2026-08-16")
        self.assertFalse(sync.has_measurements(readiness))
        self.assertNotIn("source", readiness)


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

    def test_fibre_is_captured_under_the_repositorys_key(self):
        """`fiber_g`, matching `Ingredient.fiber_g` and `NUTRIENT_KEYS` —
        never the CSV's `Fiber`, which is the exact failure the `protein_g`
        test above records. It is what gives the telemetry header's planned
        fibre figure a measured counterpart to sit beside."""
        entry = sync._daily_summary_row(
            [{"Date": "2026-08-16", "Energy (kcal)": "2010", "Fiber (g)": "38.4"}],
            "2026-08-16",
        )
        self.assertEqual(entry["fiber_g"], 38.4)
        self.assertNotIn("fiber", entry)

    def test_an_export_without_the_column_omits_fibre_rather_than_zeroing_it(self):
        """`ROWS` predates the column, like every row already synced. Absent
        means "no news" — a stored 0.0 would read as a day with no fibre in
        it, which is `_prune`'s whole reason for existing."""
        entry = sync._daily_summary_row(self.ROWS, "2026-08-16")
        self.assertNotIn("fiber_g", entry)

    def test_fibre_stays_out_of_the_budgeted_macros(self):
        """The separation CLAUDE.md's "Fibre is reported, never budgeted"
        draws on the planning side has to hold on the measured side too:
        `logged_intake_for` walks `MACRO_KEYS` and every reconciling check
        is `calories ~= 4p + 4c + 9f`, which has no term for fibre."""
        self.assertNotIn("fiber_g", planner.MACRO_KEYS)
        self.assertIn("fiber_g", planner.NUTRIENT_KEYS)
        self.assertEqual(
            sorted(sync.CRONOMETER_MACRO_COLUMNS),
            sorted(planner.NUTRIENT_KEYS),
        )


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

    def test_the_more_behind_lists_own_latest_date_anchors_the_range(self):
        """A Garmin-stale, Cronometer-fresh database still catches Garmin up.

        `get_latest_biometrics` only reads `weigh_ins`; this has to read both
        lists' own latest dates and anchor on the earlier (more behind) of
        the two — anchoring on the fresher one, as an earlier version of this
        function did by taking the max across both lists combined, would
        compute Garmin's gap from Cronometer's more recent date and silently
        conclude there was nothing left to catch up.
        """
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-15", "weight_kg": 90.0}))
        run_sync(self.repo.save_daily_actuals({"date": "2026-08-18", "calories": 2000.0}))
        self.assertEqual(
            sync.get_sync_date_range(self.repo, "2026-08-20"),
            ["2026-08-16", "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"],
        )

    def test_already_up_to_date_returns_an_empty_range(self):
        """The latest record equalling the target means nothing is missing."""
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-21", "weight_kg": 90.0}))
        self.assertEqual(sync.get_sync_date_range(self.repo, "2026-08-21"), [])

    def test_a_source_stuck_behind_by_repeated_failures_is_still_caught_up(self):
        """Regression: real 429s left Cronometer days behind a caught-up Garmin.

        Garmin synced clean through today; Cronometer failed four days in a
        row with 429s and never wrote anything past 2026-08-09 — a failed
        sync never calls `save_daily_actuals`, so those days simply never
        landed. With the old max-across-both-lists anchor, Garmin's fresh
        date made `get_sync_date_range` report nothing missing at all, and
        `--sync-cronometer` on its own printed "Nothing to sync" despite
        16 real days unlogged.
        """
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-11", "weight_kg": 99.12}))
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-24", "weight_kg": 99.71}))
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-25", "weight_kg": 99.60}))
        run_sync(self.repo.save_daily_actuals({"date": "2026-08-09", "calories": 341.0}))

        result = sync.get_sync_date_range(self.repo, "2026-08-25", max_lookback_days=14)

        self.assertIn("2026-08-22", result)
        self.assertIn("2026-08-23", result)
        self.assertIn("2026-08-24", result)
        self.assertIn("2026-08-25", result)

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

    def test_a_checked_but_empty_day_is_not_re_requested(self):
        """The bug this module was written to fix: a forgotten weigh-in or
        an unlogged day must not be retried forever.

        Neither `weigh_ins` nor `daily_actuals` gets a row for a day with
        nothing to report — that is the correct, longstanding behaviour (see
        `test_sync_garmin_stores_nothing_when_the_scale_was_not_used`). Before
        `sync_checkpoints` existed, that meant `get_sync_date_range`'s
        latest-date scan could never advance past such a day, so every run
        after it re-included the same already-checked date. A checkpoint with
        no matching data row is exactly what a real "nothing to report" sync
        leaves behind, and the range must treat it as done.
        """
        run_sync(self.repo.save_sync_checkpoint("garmin", "2026-08-20"))
        run_sync(self.repo.save_sync_checkpoint("cronometer", "2026-08-20"))
        self.assertEqual(
            sync.get_sync_date_range(self.repo, "2026-08-20"), []
        )
        self.assertEqual(
            sync.get_sync_date_range(self.repo, "2026-08-22"),
            ["2026-08-21", "2026-08-22"],
        )

    def test_a_checked_but_empty_source_still_anchors_the_range(self):
        """A checkpoint alone (no data ever recorded for that source) must
        still count as that source's own latest date, the same way a real
        measurement does — otherwise a source that has never once found data
        would look permanently unsynced and re-walk from scratch every run.
        """
        run_sync(self.repo.save_sync_checkpoint("garmin", "2026-08-18"))
        run_sync(self.repo.save_daily_actuals({"date": "2026-08-20", "calories": 2000.0}))
        self.assertEqual(
            sync.get_sync_date_range(self.repo, "2026-08-20"),
            ["2026-08-19", "2026-08-20"],
        )

    def test_a_caught_up_source_is_not_dragged_back_by_a_stale_unrequested_one(self):
        """Regression: a real `--sync-garmin` run kept re-fetching the same
        capped 14-day window on every single invocation.

        Garmin was fully caught up (checkpoint at today); Cronometer had
        never once been synced with `--sync-cronometer`, so it had no
        checkpoint and no data past three weeks prior. Every unscoped call
        anchored on `min(garmin_latest, cronometer_latest)` — Cronometer's
        stale date — which a `--sync-garmin`-only run has no way to advance,
        so the same 14-day range kept coming back forever. Scoping to
        `sources=["garmin"]` is the fix: Garmin's own gap is empty, and
        Cronometer's staleness must not be this call's problem.
        """
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-11", "weight_kg": 99.12}))
        run_sync(self.repo.save_sync_checkpoint("garmin", "2026-08-26"))
        run_sync(self.repo.save_daily_actuals({"date": "2026-08-09", "calories": 341.0}))

        self.assertEqual(
            sync.get_sync_date_range(self.repo, "2026-08-26", sources=["garmin"]), []
        )

    def test_an_unrequested_source_is_ignored_even_when_it_is_the_more_behind_one(self):
        """The other half: a Cronometer-only run must see Cronometer's own
        real gap, not an empty range borrowed from Garmin's freshness, and
        must not need Garmin's date at all to compute it."""
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-18", "weight_kg": 99.12}))
        run_sync(self.repo.save_daily_actuals({"date": "2026-08-15", "calories": 341.0}))

        result = sync.get_sync_date_range(self.repo, "2026-08-18", sources=["cronometer"])

        self.assertEqual(result, ["2026-08-16", "2026-08-17", "2026-08-18"])

    def test_omitting_sources_still_anchors_on_whichever_requested_source_is_behind(self):
        """`sources=None` (the default) is unchanged: every known source is
        considered, so a combined `--sync-garmin --sync-cronometer` run keeps
        catching up whichever one is further behind — the same case
        `test_the_more_behind_lists_own_latest_date_anchors_the_range` covers
        without the parameter at all.
        """
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-18", "weight_kg": 99.12}))
        run_sync(self.repo.save_daily_actuals({"date": "2026-08-15", "calories": 341.0}))

        with_default = sync.get_sync_date_range(self.repo, "2026-08-18")
        with_both = sync.get_sync_date_range(
            self.repo, "2026-08-18", sources=["garmin", "cronometer"]
        )
        self.assertEqual(with_default, with_both)
        self.assertEqual(with_default, ["2026-08-16", "2026-08-17", "2026-08-18"])


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

    def test_sync_garmin_stores_readiness_in_its_own_list(self):
        """The whole of change-queue item 1: this was fetched on every sync
        and printed, and `biometrics.json` kept no record of it."""
        with replace_garmin_client(
            FakeGarminClient(weight_list=[FULL_READING], sleep=SLEEP, hrv=HRV)
        ):
            sync.sync_garmin("2026-08-16", self.repo)

        stored = self.stored()
        self.assertEqual(len(stored["readiness_log"]), 1)
        row = stored["readiness_log"][0]
        self.assertEqual(row["sleep_score"], 83.0)
        self.assertEqual(row["sleep_hours"], 7.33)
        self.assertEqual(row["hrv_ms"], 42.0)
        self.assertEqual(row["readiness_label"], "excellent")
        self.assertEqual(row["source"], "garmin")
        # A third list, not a few more keys on the weigh-in: a scale and a
        # watch both reporting for one date is the collision this split
        # exists to avoid.
        self.assertNotIn("sleep_score", stored["weigh_ins"][0])

    def test_a_second_readiness_sync_updates_rather_than_appends(self):
        with replace_garmin_client(FakeGarminClient(sleep=SLEEP, hrv=HRV)):
            sync.sync_garmin("2026-08-16", self.repo)
            sync.sync_garmin("2026-08-16", self.repo)
        self.assertEqual(len(self.stored()["readiness_log"]), 1)

    def test_a_re_sync_fills_in_the_endpoint_that_failed_last_time(self):
        """Why the row is merged rather than replaced, and why the two
        endpoints are caught separately: a night whose HRV call failed keeps
        its sleep score when a later run gets the HRV."""
        with replace_garmin_client(FakeGarminClient(sleep=SLEEP, hrv=None)):
            sync.sync_garmin("2026-08-16", self.repo)
        with replace_garmin_client(FakeGarminClient(sleep=None, hrv=HRV)):
            sync.sync_garmin("2026-08-16", self.repo)

        rows = self.stored()["readiness_log"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sleep_score"], 83.0)
        self.assertEqual(rows[0]["hrv_ms"], 42.0)

    def test_a_night_the_watch_reported_nothing_for_stores_no_row(self):
        """`has_measurements` at the call site, the same guard the weigh-in
        got after `source` alone once passed for a measurement."""
        with replace_garmin_client(
            FakeGarminClient(weight_list=[FULL_READING], sleep=None, hrv=None)
        ):
            sync.sync_garmin("2026-08-16", self.repo)

        self.assertEqual(self.stored()["readiness_log"], [])
        self.assertEqual(len(self.stored()["weigh_ins"]), 1)

    def test_readiness_rows_do_not_drag_a_caught_up_garmin_backwards(self):
        """`get_sync_date_range` folds a source's lists together rather than
        ranking them independently.

        Garmin fills two lists off one checkpoint, and a fortnight of
        weigh-ins with no readiness beside them (every file written before
        `readiness_log` existed) would otherwise put the empty list into the
        `min` and re-walk days Garmin has already answered for — the same
        re-fetch-forever bug `sources` was added to fix, arriving by a second
        route.
        """
        run_sync(self.repo.save_biometric_entry({"date": "2026-08-18", "weight_kg": 99.1}))
        run_sync(self.repo.save_sync_checkpoint("garmin", "2026-08-18"))
        self.assertEqual(
            sync.get_sync_date_range(self.repo, "2026-08-19", sources=["garmin"]),
            ["2026-08-19"],
        )

    def test_sync_garmin_stores_recorded_activity(self):
        """The list that closed the "fetched every sync, read by nothing"
        shape — the same one v0.29.0 closed for sleep."""
        with replace_garmin_client(FakeGarminClient(activities=ACTIVITIES)):
            sync.sync_garmin("2026-08-16", self.repo)
        stored = self.stored()["activity_log"]
        self.assertEqual(
            [row["session_type"] for row in stored],
            ["gym_hypertrophy", "cardio_easy", "cardio_run"],
        )
        # Sorted by start time, so a day reads in the order it was lived.
        self.assertEqual([row["start_time"] for row in stored], ["05:31", "06:12", "17:45"])

    def test_an_unmapped_or_untimed_activity_is_not_stored(self):
        """A row nothing can read back is the thing this list was added to
        stop writing."""
        with replace_garmin_client(
            FakeGarminClient(
                activities=[
                    {"activityType": {"typeKey": "yoga"}, "duration": 3600,
                     "startTimeLocal": "2026-08-16 07:00:00"},
                    {"activityType": {"typeKey": "running"}, "duration": 1800},
                ]
            )
        ):
            sync.sync_garmin("2026-08-16", self.repo)
        self.assertEqual(self.stored()["activity_log"], [])

    def test_a_re_sync_replaces_a_days_activities_rather_than_appending(self):
        """A day holds any number of activities, so there is no row to
        refine — merging would leave a deleted or re-classified session
        outliving the day it was recorded on."""
        with replace_garmin_client(FakeGarminClient(activities=ACTIVITIES)):
            sync.sync_garmin("2026-08-16", self.repo)
            sync.sync_garmin("2026-08-16", self.repo)
        self.assertEqual(len(self.stored()["activity_log"]), 3)

        with replace_garmin_client(
            FakeGarminClient(activities=[dict(ACTIVITIES[0], calories=100)])
        ):
            sync.sync_garmin("2026-08-16", self.repo)
        stored = self.stored()["activity_log"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["gross_calories"], 100)

    def test_a_row_filed_under_another_date_is_refused(self):
        """The delete half has already run by then, so a mismatched row would
        clear a real day and land somewhere no re-sync of its own date would
        ever clear it."""
        with self.assertRaises(ValueError):
            run_sync(
                self.repo.save_activity_entries(
                    "2026-08-16", [{"date": "2026-08-15", "session_type": "walk"}]
                )
            )

    def test_a_day_with_no_activity_leaves_earlier_days_alone(self):
        with replace_garmin_client(FakeGarminClient(activities=ACTIVITIES)):
            sync.sync_garmin("2026-08-16", self.repo)
        with replace_garmin_client(FakeGarminClient(activities=[])):
            sync.sync_garmin("2026-08-17", self.repo)
        self.assertEqual(len(self.stored()["activity_log"]), 3)

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

    def test_sync_garmin_checkpoints_a_day_with_no_reading(self):
        """The other half of the fix above: an empty day is still a *checked*
        day, so it must not be indistinguishable from one nobody asked about.
        """
        with replace_garmin_client(FakeGarminClient(weight_list=None)):
            sync.sync_garmin("2026-08-16", self.repo)

        self.assertEqual(
            run_sync(self.repo.load_biometrics())["sync_checkpoints"]["garmin"],
            "2026-08-16",
        )

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

    def test_sync_cronometer_checkpoints_a_day_with_nothing_logged(self):
        """Same fix, Cronometer side: a day nobody logged is still a day
        that was genuinely checked, not one still owed a retry."""
        with replace_cronometer_service(lambda day: {"date": day}):
            sync.sync_cronometer("2026-08-16", self.repo)

        self.assertEqual(
            run_sync(self.repo.load_biometrics())["sync_checkpoints"]["cronometer"],
            "2026-08-16",
        )
        self.assertEqual(run_sync(self.repo.load_biometrics())["daily_actuals"], [])


class TestSyncCheckpoints(unittest.TestCase):
    """`save_sync_checkpoint` — the bookkeeping `get_sync_date_range` reads to
    tell a genuinely empty day apart from one nobody has checked yet."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "biometrics.json")
        self.repo = LocalJSONRepository(biometrics_path=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_later_date_advances_the_checkpoint(self):
        run_sync(self.repo.save_sync_checkpoint("garmin", "2026-08-16"))
        run_sync(self.repo.save_sync_checkpoint("garmin", "2026-08-18"))
        self.assertEqual(
            run_sync(self.repo.load_biometrics())["sync_checkpoints"]["garmin"],
            "2026-08-18",
        )

    def test_an_earlier_date_does_not_move_the_checkpoint_backward(self):
        """A manual `--date` re-sync of an older day must not un-teach the
        process what it already confirmed about a more recent one."""
        run_sync(self.repo.save_sync_checkpoint("garmin", "2026-08-18"))
        run_sync(self.repo.save_sync_checkpoint("garmin", "2026-08-16"))
        self.assertEqual(
            run_sync(self.repo.load_biometrics())["sync_checkpoints"]["garmin"],
            "2026-08-18",
        )

    def test_sources_are_tracked_independently(self):
        run_sync(self.repo.save_sync_checkpoint("garmin", "2026-08-18"))
        run_sync(self.repo.save_sync_checkpoint("cronometer", "2026-08-10"))
        checkpoints = run_sync(self.repo.load_biometrics())["sync_checkpoints"]
        self.assertEqual(checkpoints["garmin"], "2026-08-18")
        self.assertEqual(checkpoints["cronometer"], "2026-08-10")

    def test_a_fresh_repository_has_no_checkpoints(self):
        """Same cold-start tolerance as `weigh_ins`/`daily_actuals`: a
        checkout that has never synced must not error, just read as empty."""
        self.assertEqual(run_sync(self.repo.load_biometrics())["sync_checkpoints"], {})


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
    """Make both Cronometer sync paths build a service driven by `fake_fetch`.

    Same reasoning as `replace_garmin_client`: the sync functions construct
    their own `CronometerSyncService`, so the seam has to be the class, not
    an injected instance. `fake_fetch` takes the ISO date string and returns
    the `daily_actuals` row `fetch_daily_summary` would have.

    It stands in for `fetch_range_summaries` too, calling `fake_fetch` once
    per day so a test can still say "this day logged 2000 kcal and that one
    logged nothing" without a CSV fixture. That is emphatically *not* how
    the real one works — one request for the whole span is the entire point
    of it — so a test about what the range actually costs seams one level
    lower, at `_rows_in_process`. See `TestCronometerRequestCost`.
    """
    original = sync.CronometerSyncService

    class Patched(original):
        def __init__(self, *args, **kwargs):
            super().__init__(username="user@example.com", password="pw")

        def fetch_daily_summary(self, target_date):
            return fake_fetch(target_date)

        def fetch_range_summaries(self, dates):
            return {day: fake_fetch(day) for day in sorted(dates)}

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


def make_429(retry_after=None):
    """A real `requests.exceptions.HTTPError` shaped like Cronometer's 429.

    `requests.Response()` is a plain constructible object — no network
    involved — so this stays as self-contained as every other fake in this
    file. The real failure carried no reason phrase (`requests`'s default
    message renders with a blank reason), which `_rate_limit_wait_hint`'s
    docstring leans on, so that shape is reproduced here rather than a
    generic error string.
    """
    response = requests.Response()
    response.status_code = 429
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return requests.exceptions.HTTPError("429 Client Error:  for url: ...", response=response)


class TestRateLimitWaitHint(unittest.TestCase):
    """`_rate_limit_wait_hint` — turning a 429's `Retry-After` into an ETA."""

    def test_numeric_retry_after_yields_a_concrete_eta(self):
        hint = sync._rate_limit_wait_hint(make_429(retry_after="120").response)
        self.assertIn("120s", hint)

    def test_http_date_retry_after_yields_a_concrete_eta(self):
        hint = sync._rate_limit_wait_hint(
            make_429(retry_after="Wed, 21 Oct 2026 07:28:00 GMT").response
        )
        self.assertIn("2026-10-21", hint)

    def test_missing_retry_after_is_reported_honestly_not_guessed(self):
        """No header means no ETA — the message must not invent one."""
        hint = sync._rate_limit_wait_hint(make_429().response)
        self.assertIn("no reliable ETA", hint)
        self.assertNotRegex(hint, r"\d{4}-\d{2}-\d{2}")


class TestCronometerRangeSync(unittest.TestCase):
    """`sync_cronometer_range` — one fetch for the span, persisted per day."""

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

    def test_a_day_with_nothing_logged_is_still_checkpointed(self):
        """The empty day the range fold has to keep distinguishable.

        `_daily_summary_row` returns `{"date": ...}` for a day the CSV has
        no row for, which must not become a row of zeroes — but the date
        was genuinely asked about, so its checkpoint has to advance or
        `get_sync_date_range` re-requests it forever.
        """

        def fake_fetch(day):
            if day == "2026-08-20":
                return {"date": day}
            return {"date": day, "calories": 2000.0, "source": "cronometer"}

        dates = ["2026-08-19", "2026-08-20", "2026-08-21"]
        with replace_cronometer_service(fake_fetch):
            sync.sync_cronometer_range(dates, self.repo)

        biometrics = run_sync(self.repo.load_biometrics())
        self.assertEqual(
            sorted(row["date"] for row in biometrics["daily_actuals"]),
            ["2026-08-19", "2026-08-21"],
        )
        self.assertEqual(biometrics["sync_checkpoints"]["cronometer"], "2026-08-21")

    def test_a_failed_fetch_strands_the_whole_span_and_stores_nothing(self):
        """One request has one outcome — there is no per-day isolation left.

        Deliberate, and the opposite of `sync_garmin_range`: the span is a
        single export call, so a failure means no day was fetched. It is
        reported against the first date alone, and nothing is checkpointed,
        which is what lets `get_sync_date_range` find every one of them
        still missing on the next run.
        """

        def fake_fetch(day):
            raise RuntimeError("cronometer unavailable")

        dates = ["2026-08-19", "2026-08-20", "2026-08-21"]
        with replace_cronometer_service(fake_fetch):
            results = sync.sync_cronometer_range(dates, self.repo)

        self.assertEqual([r["date"] for r in results], ["2026-08-19"])
        self.assertIn("error", results[0])
        self.assertNotIn("rate_limited", results[0])

        biometrics = run_sync(self.repo.load_biometrics())
        self.assertEqual(biometrics["daily_actuals"], [])
        self.assertNotIn("cronometer", biometrics["sync_checkpoints"])

    def test_a_429_is_reported_with_its_wait_hint(self):
        """Regression: a real run turned one 429 into fourteen.

        The original fix was to break the per-date walk on a 429. Fetching
        the span in one request makes that structural — there is no walk to
        break — but the flag and the ETA `main` prints still have to reach
        the caller, so this pins them.
        """

        def fake_fetch(day):
            raise make_429(retry_after="60")

        dates = ["2026-08-19", "2026-08-20", "2026-08-21", "2026-08-22"]
        with replace_cronometer_service(fake_fetch):
            results = sync.sync_cronometer_range(dates, self.repo)

        self.assertEqual([r["date"] for r in results], ["2026-08-19"])
        self.assertTrue(results[0]["rate_limited"])
        self.assertIn("60s", results[0]["wait_hint"])
        # `main` names the tail from what the result list doesn't cover.
        self.assertEqual(dates[len(results):], ["2026-08-20", "2026-08-21", "2026-08-22"])


class TestCronometerRequestCost(unittest.TestCase):
    """A range costs one export request, not one per day.

    Written because the per-day walk was the thing provoking Cronometer's
    429s: `export_raw` re-authenticates and mints a fresh token before every
    export, so six days of catchup was roughly thirty HTTP requests to
    retrieve six CSV rows one call would have returned. Seamed at
    `_rows_in_process` — one level below `replace_cronometer_service`, whose
    fake deliberately still answers per day — because the request count is
    exactly what that helper papers over.
    """

    ROWS = [
        {"Date": "2026-08-19", "Energy (kcal)": "1800", "Protein (g)": "140"},
        {"Date": "2026-08-21", "Energy (kcal)": "2100", "Protein (g)": "160"},
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "biometrics.json")
        self.repo = LocalJSONRepository(biometrics_path=self.path)

    def tearDown(self):
        self.tmp.cleanup()

    @contextlib.contextmanager
    def _counting_service(self, rows):
        spans = []
        original = sync.CronometerSyncService

        class Patched(original):
            def __init__(self, *args, **kwargs):
                super().__init__(username="user@example.com", password="pw")

            def _rows_in_process(self, start, end):
                spans.append((start, end))
                return rows

        sync.CronometerSyncService = Patched
        try:
            yield spans
        finally:
            sync.CronometerSyncService = original

    def test_a_multi_day_range_is_one_export_over_the_whole_span(self):
        dates = ["2026-08-19", "2026-08-20", "2026-08-21"]
        with self._counting_service(self.ROWS) as spans:
            results = sync.sync_cronometer_range(dates, self.repo)

        self.assertEqual(spans, [("2026-08-19", "2026-08-21")])
        self.assertEqual([r["date"] for r in results], dates)

        biometrics = run_sync(self.repo.load_biometrics())
        # 08-20 has no row in the CSV: nothing logged, so nothing stored —
        # but every date was asked about, so the checkpoint covers them all.
        self.assertEqual(
            sorted(row["date"] for row in biometrics["daily_actuals"]),
            ["2026-08-19", "2026-08-21"],
        )
        self.assertEqual(biometrics["sync_checkpoints"]["cronometer"], "2026-08-21")

    def test_an_undated_row_is_not_smeared_across_a_span(self):
        """The fallback that is only sound for a single day.

        `_daily_summary_row` takes a lone undated row as the day asked for,
        which is unambiguous for a one-day export and a silent corruption
        over a range — the same figures would be written to every date in
        it. `single_day_request` is what keeps them apart.
        """
        undated = [{"Energy (kcal)": "1500", "Protein (g)": "120"}]
        with self._counting_service(undated):
            sync.sync_cronometer_range(["2026-08-19", "2026-08-20"], self.repo)

        self.assertEqual(run_sync(self.repo.load_biometrics())["daily_actuals"], [])

        with self._counting_service(undated):
            sync.sync_cronometer_range(["2026-08-19"], self.repo)

        actuals = run_sync(self.repo.load_biometrics())["daily_actuals"]
        self.assertEqual([row["date"] for row in actuals], ["2026-08-19"])
        self.assertEqual(actuals[0]["calories"], 1500.0)


class TestCLIDateSelection(unittest.TestCase):
    """Which dates `main` actually asks for.

    Written because `--date 2026-08-26` used to announce "Catching up 6
    missing day(s)" and fetch the five days around it as well. Catchup
    defaulted to on and `--date` defaulted to today, so nothing downstream
    could tell a named day from an unnamed one — and against a
    rate-limited Cronometer the difference is one export request or a
    throttled account.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.synced = []

        repo_path = str(Path(self.tmp.name) / "biometrics.json")
        patches = [
            unittest.mock.patch.object(
                sync, "LocalJSONRepository", lambda: LocalJSONRepository(biometrics_path=repo_path)
            ),
            unittest.mock.patch.object(
                sync,
                "get_sync_date_range",
                lambda repo, target, lookback, sources=None: ["catchup-ran", target],
            ),
            unittest.mock.patch.object(
                sync,
                "sync_cronometer_range",
                lambda dates, repo: self.synced.extend(dates) or [],
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_named_date_syncs_only_that_date(self):
        sync.main(["--sync-cronometer", "--date", "2026-08-26"])
        self.assertEqual(self.synced, ["2026-08-26"])

    def test_a_bare_run_still_catches_up(self):
        """The scheduled-sync shape, where a missed day must not be lost."""
        sync.main(["--sync-cronometer"])
        self.assertEqual(self.synced[0], "catchup-ran")

    def test_an_explicit_catchup_wins_over_a_named_date(self):
        sync.main(["--sync-cronometer", "--date", "2026-08-26", "--catchup"])
        self.assertEqual(self.synced, ["catchup-ran", "2026-08-26"])

    def test_no_catchup_on_a_bare_run_is_still_yesterday_only(self):
        """A bare default targets yesterday: today's logging is never complete,
        and a same-day checkpoint would strand anything logged after it."""
        sync.main(["--sync-cronometer", "--no-catchup"])
        self.assertEqual(self.synced, [(date.today() - timedelta(days=1)).isoformat()])


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

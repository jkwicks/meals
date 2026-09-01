"""Pull measured reality in from Garmin Connect and Cronometer.

The input side of the adaptive loop. `config.json`'s `user_profile` says what
the body is aiming at and `nutrition_engine.py` does the arithmetic between
them, but both are useless without today's numbers — this module is what puts
those numbers in `biometrics.json` without a phone in the loop. It writes the
three lists `PlanRepository.load_biometrics` already promises and invents no
storage of its own:

    weigh_ins     <- GarminSyncService.fetch_body_composition
    daily_actuals <- CronometerSyncService.fetch_daily_summary
    readiness_log <- GarminSyncService.fetch_readiness
    activity_log  <- GarminSyncService.fetch_activities

Eight things here are decisions rather than detail, and each is load-bearing.

**This file lives in a subdirectory, which the rest of `src/` deliberately
does not.** CLAUDE.md's flat-sibling rule works because `python src/planner.py`
puts `src/` on `sys.path[0]`; `python src/integrations/sync_service.py` puts
`src/integrations/` there instead, and `from repository import ...` then fails
with the project's own storage module sitting one directory up. The
`sys.path` insert below is what buys the subdirectory back. It is not
boilerplate — delete it and the CLI stops working while every editor and
linter still resolves the import fine.

**Macro keys are the repository's, not Cronometer's.** The export column is
`Protein (g)` and the obvious dict key is `protein`, but `daily_actuals` rows
are read by `nutrition_engine.calculate_macro_targets`, which indexes
`protein_g`/`net_carbs_g`/`fat_g`. A row keyed `protein` would store, sort and
display perfectly and contribute nothing to a single target — the failure
would surface weeks later as an adaptive loop that never adapts. The mapping
happens once, in `_daily_summary_row`. `fiber_g` follows the same rule and
the same spelling, and is captured for the same reason every other key here
is: something already reads it (see `CRONOMETER_MACRO_COLUMNS`).

**Exercise energy is discounted, never taken at face value.** Garmin reports
an activity's *gross* calories, which include the BMR the body would have
burned lying still for that hour — and TDEE already counts that hour. Adding
gross exercise calories to a TDEE estimate double-counts the overlap and
quietly inflates the day's allowance by a few hundred kcal, which is most of
a deficit. `EXERCISE_RECOVERY_FACTOR` halves it. See the constant.

**Sleep and HRV are readiness, not energy.** `fetch_readiness` returns a
sleep score, sleep hours and an HRV figure, and nothing in it reaches a
calorie figure. A sleep score is a 0-100 index with no energy units behind it
and HRV is milliseconds, so there is no arithmetic that could legitimately
turn either into kcal — the separation is enforced by these being different
methods writing a different list, not by a comment asking the next caller
nicely. It is stored now (`readiness_log`) where it used to be printed and
thrown away, and storing it changes nothing about that rule: whether a
readiness figure should *adjust* a target is a separate, larger question, and
`apply_training_adjustments` still never sees this data.

**Activity is stored now, and only what can be read back.** It was fetched
on every sync and printed for months — the same shape as the sleep data
above, and closed the same way: `activity_log` exists because something
reads it (`nutrition_engine.propose_training_schedule`, which turns a few
weeks of recorded sessions into a proposed `training_schedule` for a human
to accept), and `net_calories` is finally load-bearing as that proposal's
default burn. Two filters keep an unreadable row out — a modality
`GARMIN_SESSION_TYPES` cannot name, and an activity with no local start
time — because a proposal is a sentence the user is asked to agree to, and
neither a guessed modality nor a midnight that never happened is one this
module is entitled to write. It is also the one section holding *several*
rows per date, so it is replaced per day rather than merged; see
`PlanRepository.save_activity_entries`.

**Cronometer runs in-process.** `cronometer-mcp` requires Python >= 3.11,
which this project's Homebrew 3.14 venv satisfies, so `CronometerSyncService`
just imports and calls it directly — no sidecar interpreter involved.

**Cronometer is fetched a span at a time, Garmin a day at a time.** One
Cronometer day is not one request: `export_raw` re-authenticates and mints a
fresh auth token before every export, so a day costs about five and a
six-day catchup cost around thirty — against an account that rate-limits,
which is how the 429s below were being provoked. The export endpoint takes a
real date range and returns a row per day, so `fetch_range_summaries` asks
once for the whole span and `_daily_summary_row` folds the one CSV into each
day. Garmin has no comparable limit and keeps its per-day loop, which buys
it per-day failure isolation the Cronometer path gives up.

**A day with nothing to report is still a day that was checked.** Neither
list gets a row when the scale wasn't stepped on or nothing was logged (a day
of zero calories would drag every average that reads the series), but
`get_sync_date_range`'s catchup walk works by finding the latest recorded
date — so without a separate record of what was actually *asked about*, an
empty day looks identical to one nobody has synced yet, and every run after
it would re-request the exact same date forever. `PlanRepository.
save_sync_checkpoint` is that separate record: `sync_garmin`/`sync_cronometer`
advance it on every date the source genuinely answered, whether or not the
answer was "nothing", and `get_sync_date_range` folds it into each source's
own latest date. See `save_sync_checkpoint` and `get_sync_date_range`.
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# See the module docstring: `src/integrations/` is one level below the flat
# module layout the rest of the app relies on, so `src/` has to be put on the
# path by hand before any sibling import. Insert rather than append — a
# stray `repository.py` elsewhere on the path must not win over the project's.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from repository import (  # noqa: E402
    BIOMETRIC_SECTION_SOURCES,
    PROJECT_ROOT,
    LocalJSONRepository,
    run_sync,
)

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is in requirements.txt
    load_dotenv = None


# Fraction of an activity's *gross* reported calories that counts as genuinely
# additional expenditure. Garmin's per-activity `calories` includes the resting
# metabolism that hour would have cost anyway, and every TDEE figure this app
# computes (`nutrition_engine.calculate_tdee`, and the adaptive estimate that
# supersedes it) already contains that resting cost for all 24 hours. Adding
# the gross number therefore counts the overlap twice.
#
# 0.50 is deliberately blunt rather than precise. The exact overlap is
# `bmr_per_hour x duration`, and some Garmin activities do carry a
# `bmrCalories` field that would let it be subtracted properly — but it is
# absent often enough that a code path depending on it would silently switch
# between two definitions of "net" from one activity to the next. One constant
# that is always slightly wrong beats two that disagree, and erring low is the
# safe direction: it under-credits exercise rather than inflating the day's
# allowance.
EXERCISE_RECOVERY_FACTOR = 0.50

# Garmin `activityType.typeKey` values that count as cardio for this purpose.
# Matched as substrings (see `_is_cardio`) because Garmin sub-types freely —
# `indoor_cycling` also arrives as `virtual_ride`, treadmill work as
# `treadmill_running` — and an exact-match set would silently drop sessions.
CARDIO_ACTIVITY_KEYS = (
    "elliptical",
    "treadmill",
    "indoor_cycling",
    "indoor_cardio",
    "virtual_ride",
    "stair_climbing",
    "rowing",
)

# Garmin `activityType.typeKey` -> the `training_schedule` type the app speaks
# (`planner.TRAINING_INTENSITY_SPLIT`'s keys). Matched as substrings and
# **longest key first**, the same rule `nutrition_engine.MET_VALUES` and
# `ui_theme.training_icon` already use, because Garmin sub-types freely:
# `treadmill_running` has to reach `cardio_run` without an entry of its own,
# and `indoor_cycling` has to reach `cardio_ride` rather than stopping at the
# generic `cycling`.
#
# **There is deliberately no catch-all.** An unrecognised type is dropped
# rather than mapped to a plausible neighbour, because the only thing that
# reads this is a *proposal* the user is asked to accept, and a yoga class
# offered as "Cardio Easy, 45 min, 260 kcal" is a wrong answer that looks
# like a right one. `MET_FALLBACK` can afford a guess — it is refining a
# number for a session the user already declared; this is inventing the
# session itself. A modality that turns out to matter belongs in this table
# by name.
GARMIN_SESSION_TYPES = {
    "strength_training": "gym_hypertrophy",
    "strength": "gym_hypertrophy",
    "hiit": "cardio_hiit",
    "running": "cardio_run",
    "treadmill": "cardio_run",
    "cycling": "cardio_ride",
    "ride": "cardio_ride",
    "walking": "walk",
    "hiking": "walk",
    "elliptical": "cardio_easy",
    "rowing": "cardio_easy",
    "stair_climbing": "cardio_easy",
    "indoor_cardio": "cardio_easy",
    "swimming": "cardio_easy",
}

# Where garth caches its OAuth tokens. Overridable so a test never writes to
# the real one; the default matches garminconnect's own documented location.
GARMIN_TOKEN_DIR = os.path.expanduser(os.environ.get("GARMINTOKENS", "~/.garminconnect"))

# Cronometer's daily-summary CSV column headers, mapped to the keys
# `daily_actuals` rows use. Several spellings per macro because the export has
# renamed columns across Cronometer releases and an older CSV must not parse
# into a row of zeroes — first header present wins.
#
# **An entry here has to assert that something reads it.** The export also
# carries sodium, potassium and a long tail of micronutrients, and capturing
# one of those would reproduce exactly the shape v0.29.0 closed for Garmin's
# sleep data: a field written on every sync, paying its fetch cost, read by
# nothing. `fiber_g` earns its place because the telemetry header already
# prints the *planned* figure and had no measured counterpart to sit beside
# it — see `ui_state.fibre_view`.
#
# `fiber_g` is the repository's key, matching `Ingredient.fiber_g` and
# `planner.NUTRIENT_KEYS`, never the CSV's `Fiber` — the same failure this
# module's `protein_g` note already records. It rides on `NUTRIENT_KEYS` and
# stays out of `MACRO_KEYS`, so `logged_intake_for`'s budget arithmetic and
# every `calories ~= 4p + 4c + 9f` check are untouched by its arrival.
CRONOMETER_MACRO_COLUMNS = {
    "calories": ("Energy (kcal)", "Energy (Calories)", "Calories"),
    "protein_g": ("Protein (g)", "Protein"),
    "net_carbs_g": ("Net Carbs (g)", "Net Carbs"),
    "fat_g": ("Fat (g)", "Fat"),
    "fiber_g": ("Fiber (g)", "Fiber"),
}

# Same tolerance for the date column: the servings export calls it `Day`, the
# daily summary has used both.
CRONOMETER_DATE_COLUMNS = ("Date", "Day")


def _load_env() -> None:
    """Read `.env` if python-dotenv is available.

    Called from the service constructors rather than at import time so that
    importing this module from a test or the UI never reaches out to the
    filesystem for credentials it isn't going to use.
    """
    if load_dotenv is not None:
        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def _from_env(supplied: Optional[str], variable: str) -> str:
    """`supplied`, or the environment's value when nothing was supplied at all.

    The distinction between `None` and `""` is the whole point, and the
    obvious `supplied or os.environ.get(variable, "")` erases it: `""` is
    falsy, so a caller explicitly passing *no credential* silently gets the
    real one out of `.env` instead.

    That is not a theoretical difference. It is what let
    `test_missing_credentials_fail_before_any_call` — a test whose entire
    purpose is to prove the guard fires before any network call — construct a
    service with `username=""`, receive the developer's live Cronometer
    credentials, sail past `_require_credentials`, and issue a genuine
    authenticated request to cronometer.com on every run of the suite. It
    surfaced as a `429` only once the account had been rate-limited enough to
    start refusing; until then the test passed for the wrong reason.

    Passing `None` (or omitting the argument) still means "read the
    environment", which is what every real caller does.
    """
    if supplied is not None:
        return supplied
    return os.environ.get(variable, "")


def _iso(target_date: str) -> str:
    """Validate an ISO `YYYY-MM-DD` string and hand it back.

    Strict where `nutrition_engine._parse_iso_date` is tolerant, and for the
    opposite reason: that function reads dates a human may have typed into
    biometrics.json, where one bad row should cost one row. This one guards
    the *key* an upsert is about to write under. A malformed date here would
    be stored as a row that `get_latest_biometrics` can never rank and no
    later sync can ever correct, because the correcting write would target a
    different key.
    """
    try:
        return datetime.strptime(target_date.strip()[:10], "%Y-%m-%d").date().isoformat()
    except (ValueError, AttributeError):
        raise ValueError(f"target_date must be ISO YYYY-MM-DD: got {target_date!r}")


def _as_float(value: Any) -> Optional[float]:
    """A number, or None if `value` isn't one.

    Both upstreams send `None`, `""` and occasionally `"--"` for a metric the
    device or the log simply doesn't have. None must survive as None all the
    way to storage: `save_biometric_entry` merges rather than replaces, so a
    key omitted keeps yesterday's reading while a key coerced to 0.0 would
    overwrite a real body-fat percentage with a lie.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _prune(row: Dict[str, Any]) -> Dict[str, Any]:
    """Drop keys whose value is None, keeping `date`.

    The other half of the merge semantics `_as_float` protects. An entry is
    upserted with `dict.update`, so sending `{"body_fat_pct": None}` for a
    scale that didn't measure it would blank the reading a previous sync
    stored. Absent means "no news", which is what a partial reading is.
    """
    return {key: value for key, value in row.items() if value is not None or key == "date"}


# Keys that say where a row came from rather than what was measured. Excluded
# from `has_measurements`, which is what decides whether a row is worth
# storing at all — a `source` tag is not a reading.
_PROVENANCE_KEYS = ("date", "source")


def has_measurements(entry: Dict[str, Any]) -> bool:
    """Whether `entry` carries any measured value, not just provenance.

    The guard against writing an empty row. It counts keys rather than
    trusting `len(entry)`, which an earlier version did and which was wrong
    the moment `source` was added: a day the scale never saw came back as
    `{"date": ..., "source": "garmin"}`, cleared a `len > 2` test, and would
    have been stored as a weigh-in with no weight in it — precisely the row
    `get_latest_biometrics` would then return as the most recent reading.
    """
    return any(key not in _PROVENANCE_KEYS for key in entry)


class GarminSyncService:
    """Reads scale and cardio data out of Garmin Connect.

    Auth resumes from a cached token directory (`~/.garminconnect` by default)
    and only falls back to email/password when that fails. This is not just a
    speed optimisation: Garmin rate-limits and occasionally MFA-challenges
    repeated password logins, so a sync running on a timer that authenticated
    from scratch every run would start failing after a few days of working
    fine. Credentials are needed for the first run and after a token expiry.
    """

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        token_dir: str = GARMIN_TOKEN_DIR,
        recovery_factor: float = EXERCISE_RECOVERY_FACTOR,
    ):
        _load_env()
        # `is None`, not `or` — see `_from_env`. An explicitly empty credential
        # means "no credential", and must not be quietly refilled from .env.
        self.email = _from_env(email, "GARMIN_EMAIL")
        self.password = _from_env(password, "GARMIN_PASSWORD")
        self.token_dir = os.path.expanduser(token_dir)
        # Injected rather than read from the module constant at the point of
        # use, so the value that discounted a session is the one this service
        # was built with — a config read happening halfway down a fetch would
        # be a second source of truth for a number the caller already chose.
        self.recovery_factor = recovery_factor
        self._client = None

    def client(self):
        """An authenticated `Garmin`, logged in once and reused.

        `Garmin.login(tokenstore)` either loads that directory or raises — it
        does not fall back to credentials on its own, so the try/except is
        what makes a missing or expired token store a first run rather than a
        crash. A successful credential login dumps fresh tokens, which is what
        keeps the *next* run on the cheap path.

        The dump is conditional because the two garminconnect lines disagree
        about who does it. 0.2.x exposes the underlying garth client as
        `.garth` and leaves persistence to the caller; 0.3.x dropped the
        attribute entirely and `login(tokenstore)` now writes the tokens
        itself. Calling `.garth` unconditionally is an `AttributeError` on
        0.3.x — which is not hypothetical, because the version pip resolves
        is decided by the interpreter: 3.9 caps at 0.2.8, 3.10+ gets 0.3.x.
        """
        if self._client is not None:
            return self._client

        try:
            from garminconnect import Garmin
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise RuntimeError(
                "garminconnect is not installed. Run: pip install -r requirements.txt"
            ) from exc

        client = Garmin(self.email, self.password)
        try:
            client.login(self.token_dir)
        except Exception:
            # No cached tokens, or they have expired. Fall back to a full
            # credential login — which is the one path that actually needs
            # GARMIN_EMAIL/GARMIN_PASSWORD to be set.
            if not self.email or not self.password:
                raise RuntimeError(
                    "No usable Garmin token cache at "
                    f"{self.token_dir} and no credentials to log in with. "
                    "Set GARMIN_EMAIL and GARMIN_PASSWORD in .env."
                )
            client.login()
            # 0.2.x only: see the docstring. On 0.3.x the login above has
            # already persisted the tokens and there is no `.garth` to call.
            if hasattr(client, "garth"):
                os.makedirs(self.token_dir, exist_ok=True)
                client.garth.dump(self.token_dir)

        self._client = client
        return client

    def fetch_body_composition(self, target_date: str) -> dict:
        """The scale's reading for `target_date`, as a `weigh_ins` row.

        Returns `{"date": ...}` alone when the scale reported nothing that
        day, rather than raising or inventing a weight. A day with no weigh-in
        is the normal case — the scale is stepped on most mornings, not all —
        and `nutrition_engine` already treats the series as sparse.

        Garmin reports mass in **grams**; `weigh_ins` is in kilograms.
        """
        day = _iso(target_date)
        payload = self.client().get_body_composition(day) or {}

        # `dateWeightList` is the per-measurement list; `totalAverage` is
        # Garmin's own aggregate for the range. Prefer the measurement, fall
        # back to the average, because a single-day range makes them
        # equivalent and only the former exists on some accounts.
        measurements = payload.get("dateWeightList") or []
        reading = measurements[-1] if measurements else (payload.get("totalAverage") or {})

        weight_g = _as_float(reading.get("weight"))
        muscle_g = _as_float(reading.get("muscleMass"))

        entry = _prune(
            {
                "date": day,
                "weight_kg": round(weight_g / 1000.0, 2) if weight_g is not None else None,
                "body_fat_pct": _as_float(reading.get("bodyFat")),
                "muscle_mass_kg": round(muscle_g / 1000.0, 2) if muscle_g is not None else None,
                "water_pct": _as_float(reading.get("bodyWater")),
                "bmi": _as_float(reading.get("bmi")),
            }
        )
        # Tagged only once there is something to attribute. Stamping every
        # result would make a day with no reading indistinguishable from one
        # with a reading, to `has_measurements` and to a human reading the file.
        if has_measurements(entry):
            entry["source"] = "garmin"
        return entry

    def fetch_activities(self, target_date: str) -> list:
        """Everything the watch recorded on `target_date`, as `activity_log` rows.

        One row per activity — a Saturday with a lift and a ride is two —
        which is what makes `activity_log` the one biometric section that is
        not one row per date. Each carries both `gross_calories` (what Garmin
        said) and `net_calories` (what this app is willing to add to a day),
        because a number that has been silently adjusted is impossible to
        reconcile against the watch when the two disagree;
        `EXERCISE_RECOVERY_FACTOR` explains the discount.

        Two derived fields sit beside Garmin's own, and both exist for
        `nutrition_engine.propose_training_schedule`, which is the only thing
        that reads this list:

        - `session_type` translates `activityType.typeKey` into the
          `training_schedule` vocabulary (`GARMIN_SESSION_TYPES`), or None
          for a modality that table has never heard of.
        - `start_time` is `startTimeLocal`'s "HH:MM". **Local, never GMT** —
          a proposal says "Wednesday 06:10" to a human reading a clock, and
          the GMT field would be silently wrong by the timezone offset,
          which is the same class of unit error as storing Garmin's grams as
          kilograms.

        Unfiltered on purpose, unlike `fetch_cardio_activities` below: a
        `strength_training` session is exactly the one a schedule proposal
        most needs (it is what `WORKOUT_BREAKFAST_TYPES` pins a shake to) and
        the cardio filter drops it. What may not be *stored* is decided in
        `sync_garmin`, not here.
        """
        day = _iso(target_date)
        activities = self.client().get_activities_by_date(day, day) or []

        sessions = []
        for activity in activities:
            type_key = ((activity.get("activityType") or {}).get("typeKey") or "").lower()
            gross = _as_float(activity.get("calories")) or 0.0
            duration_s = _as_float(activity.get("duration")) or 0.0
            sessions.append(
                {
                    "date": day,
                    "activity_id": activity.get("activityId"),
                    "name": activity.get("activityName"),
                    "type": type_key,
                    "session_type": _session_type(type_key),
                    "start_time": _local_start_time(activity.get("startTimeLocal")),
                    "duration_min": round(duration_s / 60.0, 1),
                    "gross_calories": round(gross),
                    "net_calories": round(gross * self.recovery_factor),
                    "average_hr": _as_float(activity.get("averageHR")),
                    "source": "garmin",
                }
            )
        return sessions

    def fetch_cardio_activities(self, target_date: str) -> list:
        """The cardio subset of `fetch_activities`, for the CLI's own report.

        Strength work, walks and anything else non-cardio is filtered out —
        see `CARDIO_ACTIVITY_KEYS`. A filter over one fetch rather than a
        second fetch of its own: the two would otherwise be free to disagree
        about a session's duration or its discount, and one login answering
        one question twice is a request this account does not need to spend.
        """
        return [
            session
            for session in self.fetch_activities(target_date)
            if _is_cardio(session["type"])
        ]

    def fetch_readiness(self, target_date: str) -> dict:
        """Sleep and HRV for `target_date`, as a `readiness_log` row.

        Deliberately separate from every other method here, and deliberately
        not summed into anything. A sleep score is a unitless 0-100 index and
        HRV is milliseconds — there is no conversion from either to kcal, so
        any energy equation that consumed one would be inventing it. Their
        legitimate use is deciding whether today is a day to train hard,
        which is a human's call.

        **HRV used to be withheld outright**, on the reasoning that it is the
        metric most likely to be mistaken for a recovery *cost* by a future
        caller looking for one. Withholding it turned out to protect nothing
        the list separation doesn't already protect — `readiness_log` is not
        an input to any target — while costing the one number a readiness
        read is actually about. It is fetched now; the rule it was protecting
        is unchanged and is stated above.

        Returns `{"date": ...}` alone when the watch reported neither, rather
        than a row of Nones — the same `_prune`/`has_measurements` pair the
        weigh-in uses, so a night nobody wore the watch stores nothing
        instead of a readiness row with no readiness in it.

        **Sleep and HRV are two endpoints and are caught separately.** They
        fail independently (a watch worn but with HRV still baselining is a
        real state, and Garmin has moved either endpoint before), and one
        `try` around both would let a sleep failure silently discard an HRV
        reading that arrived fine. `save_readiness_entry` merges by date, so
        the half that failed lands on a later re-sync without disturbing the
        half that didn't.
        """
        day = _iso(target_date)
        try:
            payload = self.client().get_sleep_data(day) or {}
        except Exception:
            # Sleep is supplementary — a watch not worn overnight, or an
            # endpoint change, must not fail a weigh-in sync that succeeded.
            payload = {}

        daily = payload.get("dailySleepDTO") or {}
        scores = daily.get("sleepScores") or {}
        overall = scores.get("overall") or {}
        score = _as_float(overall.get("value"))
        sleep_seconds = _as_float(daily.get("sleepTimeSeconds"))

        entry = _prune(
            {
                "date": day,
                "sleep_score": score,
                "sleep_hours": round(sleep_seconds / 3600.0, 2) if sleep_seconds else None,
                "hrv_ms": self._fetch_hrv(day),
                # A label, not a coefficient. Nothing multiplies by this.
                "readiness_label": _readiness_label(score),
            }
        )
        # Tagged only once there is something to attribute, exactly as the
        # weigh-in is: a `source` on an otherwise empty row would make a night
        # with no data look like a night with data to `has_measurements`.
        if has_measurements(entry):
            entry["source"] = "garmin"
        return entry

    def _fetch_hrv(self, day: str) -> Optional[float]:
        """Last night's average HRV in milliseconds, or None.

        `get_hrv_data` is what the installed garminconnect (0.3.10) calls it —
        checked against the installed package rather than copied from an
        example, per the project's standing rule about this dependency, which
        has already changed shape once between 0.2.8 and 0.3.x.

        `lastNightAvg` rather than `weeklyAvg` or `lastNight5MinHigh`: the row
        is keyed by date, so a weekly figure would store the same number under
        seven dates and a five-minute peak answers a different question from
        the one a morning readiness read asks.
        """
        try:
            payload = self.client().get_hrv_data(day) or {}
        except Exception:
            return None
        summary = payload.get("hrvSummary") or {}
        return _as_float(summary.get("lastNightAvg"))


def _is_cardio(type_key: str) -> bool:
    """Whether a Garmin `typeKey` is one of the cardio modalities tracked."""
    return any(key in type_key for key in CARDIO_ACTIVITY_KEYS)


def _session_type(type_key: str) -> Optional[str]:
    """`type_key` in the app's `training_schedule` vocabulary, or None.

    Longest match wins, so `treadmill_running` resolves through `treadmill`
    and `running` identically and `indoor_cycling` never stops at a shorter
    key that happens to appear in it. None means "not a modality this app
    can propose" — see `GARMIN_SESSION_TYPES` for why that is a drop rather
    than a fallback.
    """
    matches = [key for key in GARMIN_SESSION_TYPES if key in type_key]
    return GARMIN_SESSION_TYPES[max(matches, key=len)] if matches else None


def _local_start_time(value: Any) -> Optional[str]:
    """The "HH:MM" out of Garmin's `startTimeLocal`, or None.

    Garmin sends `"2026-08-24 06:12:33"`. Tolerant rather than raising, like
    `nutrition_engine._parse_iso_date` and for the same reason: one activity
    with an unreadable timestamp should cost that activity, not the day's
    sync. A row with no time is not stored (see `sync_garmin`) — a session
    filed at a made-up midnight would be read by
    `planner.morning_training_days` as a pre-dawn workout and could pin a
    breakfast shake to a day nobody trained in the morning.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:19], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
    except ValueError:
        return None


def _readiness_label(score: Optional[float]) -> Optional[str]:
    """A sleep score bucketed into a word.

    Buckets match Garmin's own published bands so the app never disagrees
    with the watch face the number came from.
    """
    if score is None:
        return None
    if score >= 80:
        return "excellent"
    if score >= 60:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


class CronometerSyncService:
    """Reads logged macro intake out of Cronometer.

    Cronometer publishes no API for individual accounts; `cronometer-mcp`
    drives the same GWT-RPC protocol the web app uses, and re-discovers the
    protocol's build hashes on each login rather than pinning them, which is
    what makes it survive a Cronometer web release. It needs a paid tier that
    supports web login.

    **It needs Python >= 3.11**, which this project's Homebrew 3.14 venv
    satisfies, so `cronometer_mcp` is imported in-process — no sidecar
    interpreter involved.
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        _load_env()
        # See `_from_env`, and `GarminSyncService.__init__` for the same pair.
        self.username = _from_env(username, "CRONOMETER_USERNAME")
        self.password = _from_env(password, "CRONOMETER_PASSWORD")

    def _require_credentials(self) -> None:
        if not self.username or not self.password:
            raise RuntimeError(
                "Cronometer credentials missing. Set CRONOMETER_USERNAME and "
                "CRONOMETER_PASSWORD in .env."
            )

    def _rows_in_process(self, start: str, end: str) -> List[dict]:
        from cronometer_mcp import CronometerClient  # type: ignore[import-not-found]

        client = CronometerClient(username=self.username, password=self.password)
        return client.get_daily_summary(
            start=datetime.strptime(start, "%Y-%m-%d").date(),
            end=datetime.strptime(end, "%Y-%m-%d").date(),
        )

    def _session_cache_path(self) -> Optional[Path]:
        """Where `cronometer_mcp` keeps the session this service would resume.

        Asked of a constructed client rather than spelled out here, so the two
        cannot drift: `CronometerClient` reads `CRONOMETER_DATA_DIR` with its
        own default beside it, and a second copy of that rule would go stale
        the moment upstream moved either half. Constructing a client issues no
        requests, so asking costs nothing.

        `None` when the attribute has gone, which is the same shape
        `GarminSyncService.client()` uses across garminconnect's two lines and
        for a sharper version of the same reason: a guessed path here would
        not fail to persist a token, it would delete somebody's file.
        """
        from cronometer_mcp import CronometerClient  # type: ignore[import-not-found]

        client = CronometerClient(username=self.username, password=self.password)
        return getattr(client, "_cookie_path", None)

    def _clear_session_cache(self) -> bool:
        """Delete the cached session. True only if one was actually there.

        The return value is the guard on retrying, not a courtesy. A run that
        had no cached session already logged in fresh, so its failure is a
        real one - wrong credentials, a lapsed subscription - and retrying
        would double the request cost of every genuine auth failure against an
        endpoint that throttles.
        """
        path = self._session_cache_path()
        if path is None or not path.exists():
            return False
        path.unlink(missing_ok=True)
        return True

    def _fetch_rows(self, start: str, end: str) -> List[dict]:
        """`_rows_in_process`, retried **once** against a cleared session.

        `cronometer_mcp` resumes a saved session and validates it by minting an
        auth token - but GWT-RPC answers an expired session with HTTP **200**
        and a serialized exception in the body, so `raise_for_status` passes
        and the token regex (`re.search(r'"([^"]+)"', ...)`) returns the first
        quoted string in that payload: the exception's own class name and type
        hash, `com.cronometer.shared.user.exceptions.NotLoggedInException/
        844385496`. Non-empty, so the restore is judged a success, the login is
        skipped, and that string is sent as the export nonce - which is the 403
        seen in the wild, quoting the exception back at itself in the URL.

        Nothing below this line can tell that token from a real one and nothing
        above it can see the token at all, so the failure is only recognisable
        once the 403 has happened. Clearing the file and letting a fresh
        instance log in properly is the whole recovery, and it is worth doing
        here rather than by hand because the session expires on Cronometer's
        clock, not ours: every sync eventually meets this.

        Exactly one retry, and only when there was a session to clear. A second
        attempt is another full login - about seven requests - against an
        endpoint that rate-limits, so a retry loop here would turn one stale
        cookie into the account-level throttle `_is_rate_limited` exists to
        survive.
        """
        try:
            return self._rows_in_process(start, end)
        except Exception as exc:
            if not _is_stale_session(exc) or not self._clear_session_cache():
                raise
        # Outside the `except` block on purpose: a failure on the retry is
        # about the fresh login, and chaining the stale-session 403 onto it
        # would present the symptom this just cleared as the cause.
        return self._rows_in_process(start, end)

    def fetch_daily_summary(self, target_date: str) -> dict:
        """What was actually eaten on `target_date`, as a `daily_actuals` row.

        Returns `{"date": ...}` alone for a day with nothing logged, on the
        same reasoning as `fetch_body_composition`: an unlogged day is a real
        state, and storing it as a day of zero calories would drag every
        average that reads the series.
        """
        day = _iso(target_date)
        self._require_credentials()
        return _daily_summary_row(self._fetch_rows(day, day), day)

    def fetch_range_summaries(self, dates: List[str]) -> Dict[str, dict]:
        """Every day in `dates`, fetched in **one** export request.

        Cronometer's export endpoint already takes a real `start`/`end` span
        and answers with one CSV row per day, so a seven-day catchup costs
        one HTTP request rather than seven — and, because `CronometerClient`
        authenticates per instance, one login rather than seven.

        That matters more than it looks, because a single `fetch_daily_
        summary` was never one request either. `CronometerClient.export_raw`
        calls `authenticate()` (a restored session still re-discovers the GWT
        build hashes and re-mints an auth token; a cold one adds the CSRF
        fetch, the login POST and the GWT handshake) and then mints a second
        token before the export GET — roughly five requests warm, seven cold.
        The per-day walk this replaces therefore spent around thirty requests
        on a six-day backfill against an account that rate-limits, which is
        how the 429s `_is_rate_limited` exists to survive were being
        provoked in the first place.

        Days inside the span that aren't in `dates` come back in the CSV and
        are simply not looked up: asking for the span costs no more than
        asking for its two ends.
        """
        days = sorted({_iso(day) for day in dates})
        if not days:
            return {}
        self._require_credentials()
        rows = self._fetch_rows(days[0], days[-1])
        # `_daily_summary_row`'s undated-row fallback is only sound for a
        # one-day request — over a span it would hand the same row to every
        # day in it. See `single_day_request` there.
        return {
            day: _daily_summary_row(rows, day, single_day_request=len(days) == 1)
            for day in days
        }


def _daily_summary_row(
    rows: List[dict], day: str, *, single_day_request: bool = True
) -> dict:
    """Fold Cronometer's parsed CSV into one `daily_actuals` entry.

    Split out from `fetch_daily_summary` so the mapping — the part with the
    column-name guesswork in it — is testable without a Cronometer account.
    The export is one row per day; the row whose date column matches wins,
    and a single-row export is taken as that day regardless, because a range
    of one has nothing else it could be.
    """
    if not rows:
        return {"date": day}

    match = None
    for row in rows:
        for column in CRONOMETER_DATE_COLUMNS:
            value = (row.get(column) or "").strip()[:10]
            if value == day:
                match = row
                break
        if match:
            break

    if match is None:
        # No date column matched. A one-row export *of a single day* is
        # unambiguous; anything else would be a guess, and a wrong day
        # silently overwrites a good entry, so it is left unwritten instead.
        # `single_day_request` is what keeps that sound now `fetch_range_
        # summaries` folds one CSV into many days: over a span, an undated
        # row could belong to any day in it, and taking it would copy the
        # same figures onto every one of them.
        if not single_day_request or len(rows) != 1:
            return {"date": day}
        match = rows[0]

    entry: Dict[str, Any] = {"date": day}
    for key, columns in CRONOMETER_MACRO_COLUMNS.items():
        for column in columns:
            if column in match:
                entry[key] = _as_float(match[column])
                break

    entry = _prune(entry)
    # Same rule as the weigh-in: a row of nothing but provenance is not a
    # logged day, and must not be stored as one.
    if has_measurements(entry):
        entry["source"] = "cronometer"
    return entry


def get_sync_date_range(
    repository: LocalJSONRepository,
    target_end_date: str,
    max_lookback_days: int = 14,
    sources: Optional[List[str]] = None,
) -> List[str]:
    """Missing ISO dates between the latest recorded biometric and `target_end_date`.

    A sync that only ever runs on demand misses days: a missed Monday and
    Tuesday, run on Wednesday, must not silently leave Monday and Tuesday
    empty forever just because nobody ran it on time. This looks at both
    every list a requested source writes for its latest `date` — not just
    `get_latest_biometrics`, which only reads `weigh_ins`.

    **`sources` scopes that lookup to the sync(es) actually about to run, and
    is the reason a genuinely caught-up Garmin doesn't get dragged back into a
    14-day re-fetch by a Cronometer nobody has synced.** Pass `["garmin"]` for
    a `--sync-garmin`-only run and only `weigh_ins` is consulted; the default,
    `None`, means "every known source", for a combined run. This was a real
    bug, not a hypothetical: Garmin was fully caught up (checkpoint at today)
    while Cronometer had gone three weeks unsynced (nobody had ever passed
    `--sync-cronometer`, so it had neither data nor a checkpoint past
    2026-08-09). Every unscoped call anchored on `min(garmin_latest,
    cronometer_latest)` — Cronometer's stale date — so a `--sync-garmin`-only
    run kept computing the same capped 14-day range and re-fetching 14 already
    -current days from Garmin on every single run, forever, since nothing run
    was ever going to advance Cronometer's side of that `min`.

    Anchoring on whichever *requested* source is further behind is still
    right for a combined run: a checkout that syncs Cronometer daily but
    Garmin only occasionally must still catch Garmin up from Garmin's own
    last date, not from Cronometer's more recent one. The reverse failed for
    real too, before `sources` existed: Garmin succeeded through today while
    Cronometer was rate-limited for four days straight, and anchoring on
    Garmin's fresher date (the max of the two, an even older version of this
    function) made every source — including a `--sync-cronometer` run on its
    own — believe there was nothing left to catch up, silently stranding
    Cronometer's actual gap. `sources=["cronometer"]` fixes that case more
    directly than the old min-anchor did: a Cronometer-only run now never
    looks at Garmin's date at all.

    **A genuinely empty day is not a gap.** `weigh_ins`/`daily_actuals` only
    ever hold a *measured* day (see `save_biometric_entry`), so a forgotten
    weigh-in or an unlogged day never becomes a row — and without more
    information, the date this function's own latest-date scan would compute
    never advances past it either, so every run after would re-request that
    same date forever, on the same reasoning `_is_rate_limited` already
    applies to a throttled account: retrying something guaranteed not to
    change wastes a real call for no gain. `sync_checkpoints` (see
    `save_sync_checkpoint`) is folded into each section's latest date for
    that reason — it advances on every date a source was actually asked
    about, whether or not the answer was "nothing".

    No prior data at all for any requested source (a fresh `biometrics.json`,
    or the first-ever run of a source) has nothing to walk back from, so the
    range is just `target_end_date` alone — the same single-day sync this
    module always did before catchup existed.

    Capped at `max_lookback_days` so a database that hasn't synced in months
    doesn't queue hundreds of sequential API calls against a rate-limited
    account; the cap keeps the days closest to `target_end_date` and drops
    the older ones, since the most recent gap is the one actually missing
    from a meal plan's targets right now.
    """
    end_date = datetime.strptime(_iso(target_end_date), "%Y-%m-%d").date()
    requested = set(sources) if sources else set(BIOMETRIC_SECTION_SOURCES.values())

    biometrics = run_sync(repository.load_biometrics())
    checkpoints = biometrics.get("sync_checkpoints") or {}
    # Folded per *source*, not per section, because the mapping is one-to-many:
    # one Garmin sync fills `weigh_ins` and `readiness_log` off a single login
    # and a single checkpoint. Ranking sections independently would put the
    # emptier of the two into the `min` below and walk a fully caught-up
    # Garmin back through days it has already answered for — the same
    # re-fetch-forever bug `sources` was added to fix, arriving by a second
    # route. A source's latest known date is the latest across everything it
    # writes plus its checkpoint.
    source_dates: Dict[str, List[str]] = {}
    for section, source in BIOMETRIC_SECTION_SOURCES.items():
        if source not in requested:
            continue
        dates = source_dates.setdefault(source, [])
        dates.extend(row["date"] for row in biometrics.get(section, []) if row.get("date"))
    for source in list(source_dates):
        checkpoint = checkpoints.get(source)
        if checkpoint:
            source_dates[source].append(checkpoint)

    source_latest_dates = [max(dates) for dates in source_dates.values() if dates]
    if not source_latest_dates:
        return [end_date.isoformat()]

    latest = datetime.strptime(min(source_latest_dates), "%Y-%m-%d").date()
    start_date = latest + timedelta(days=1)
    if start_date > end_date:
        return []

    if (end_date - start_date).days + 1 > max_lookback_days:
        start_date = end_date - timedelta(days=max_lookback_days - 1)

    span = (end_date - start_date).days + 1
    return [(start_date + timedelta(days=offset)).isoformat() for offset in range(span)]


def sync_garmin(target_date: str, repository: LocalJSONRepository) -> dict:
    """Fetch a day from Garmin and persist what it found.

    All three parts become stored rows — the weigh-in into `weigh_ins`, the
    sleep/HRV reading into `readiness_log`, and the recorded activity into
    `activity_log`. Each goes into its own list, because a scale and a watch
    both reporting for one date is precisely the collision
    `BIOMETRIC_SECTIONS` keeps them apart to avoid.

    **Activity used to be fetched, printed and stored nowhere**, on the
    honest reasoning that nothing consumed it and a list whose only reader is
    a `print` is a schema with no purpose. That was the same shape v0.29.0
    closed for sleep, and it closes here the same way: something reads it now
    (`nutrition_engine.propose_training_schedule`), and the discount those
    figures carry is finally load-bearing — `net_calories` is what a proposed
    session's `estimated_burn_kcal` defaults to.

    **Only mapped, timed activities are stored.** An activity whose modality
    `GARMIN_SESSION_TYPES` has never heard of, or one with no readable local
    start time, cannot become a proposal (see `_session_type` and
    `_local_start_time`), and a row nothing can read is the thing this list
    was added to stop writing. The full list is still returned for the
    caller to print, so nothing is hidden — only unstored.
    """
    garmin_config = run_sync(repository.load_integrations_config()).get("garmin") or {}
    service = GarminSyncService(
        recovery_factor=garmin_config.get(
            "exercise_recovery_factor", EXERCISE_RECOVERY_FACTOR
        )
    )
    weigh_in = service.fetch_body_composition(target_date)
    activities = service.fetch_activities(target_date)
    cardio = [session for session in activities if _is_cardio(session["type"])]
    readiness = service.fetch_readiness(target_date)

    # Only write when the scale actually reported. `_prune` leaves a bare
    # `{"date": ...}` for a day with no reading, and storing that would create
    # a weigh-in row with no weight in it for `get_latest_biometrics` to find.
    if has_measurements(weigh_in):
        run_sync(repository.save_biometric_entry(weigh_in))

    # Same guard, same reason, one list over: a night the watch wasn't worn
    # produces a bare `{"date": ...}`, and storing that would be a readiness
    # row with no readiness in it.
    if has_measurements(readiness):
        run_sync(repository.save_readiness_entry(readiness))

    # Replaced wholesale rather than merged, and written even when the day
    # has no activity in it — see `save_activity_entries`. That is what makes
    # a deleted or re-classified activity disappear on a re-sync instead of
    # outliving the day it was recorded on.
    run_sync(
        repository.save_activity_entries(
            _iso(target_date), [session for session in activities if _storable(session)]
        )
    )

    # Checkpointed regardless of whether the scale reported anything.
    # `target_date` reaching this line at all means Garmin was genuinely
    # asked and answered — a forgotten weigh-in is a real, checked outcome,
    # and get_sync_date_range needs to be able to tell it apart from a date
    # nobody has asked about yet. See `save_sync_checkpoint`.
    run_sync(repository.save_sync_checkpoint("garmin", target_date))

    return {
        "weigh_in": weigh_in,
        "cardio": cardio,
        "activities": activities,
        "readiness": readiness,
    }


def _storable(session: dict) -> bool:
    """Whether an activity row can be read back by anything.

    Its own predicate rather than an inline comprehension condition because
    it states the contract `save_activity_entries` documents: the only reader
    is the schedule proposal, and it needs a modality it can name and a clock
    time it can put on a weekday. Either missing makes the row unreadable,
    not merely incomplete.
    """
    return bool(session.get("session_type")) and bool(session.get("start_time"))


def sync_garmin_range(dates: List[str], repository: LocalJSONRepository) -> List[dict]:
    """`sync_garmin` for every date in `dates`, in order.

    Sequential, not concurrent — the same reasoning `GarminSyncService`
    already gives for reusing a cached login rather than re-authenticating:
    a burst of concurrent requests against a rate-limited account is the
    reliable way to turn a working catchup into a wall of failures partway
    through.

    One date's exception is caught here and reported as
    `{"date": ..., "error": ...}` rather than raised, the same "a failed
    meal must not fail the week" policy `generate_week_plan` already applies
    to a bad meal type — a network blip on Tuesday must not cost Wednesday
    through Friday the rest of a backfill.
    """
    results = []
    for target_date in dates:
        try:
            outcome = sync_garmin(target_date, repository)
            outcome["date"] = target_date
            results.append(outcome)
        except Exception as exc:
            results.append({"date": target_date, "error": str(exc)})
    return results


def _persist_cronometer_day(
    target_date: str, actuals: dict, repository: LocalJSONRepository
) -> dict:
    """Store one day's row and checkpoint the date it came from.

    Shared by the single-day and range paths so the two cannot disagree
    about when a row is worth keeping. Checkpointed the same way
    `sync_garmin` is, and for the same reason: a day with nothing logged is
    a genuine, checked outcome, and reaching this line at all (rather than
    raising out of the fetch) means Cronometer was actually asked. See
    `save_sync_checkpoint`.
    """
    if has_measurements(actuals):
        run_sync(repository.save_daily_actuals(actuals))

    run_sync(repository.save_sync_checkpoint("cronometer", target_date))

    return {"daily_actuals": actuals}


def sync_cronometer(target_date: str, repository: LocalJSONRepository) -> dict:
    """Fetch a day from Cronometer and persist it to `daily_actuals`.

    The single-day entry point. `sync_cronometer_range` deliberately does
    not loop over this — it fetches the whole span in one request instead,
    see `CronometerSyncService.fetch_range_summaries` — but both persist
    through `_persist_cronometer_day`.
    """
    day = _iso(target_date)
    service = CronometerSyncService()
    return _persist_cronometer_day(day, service.fetch_daily_summary(day), repository)


def _is_rate_limited(exc: Exception) -> bool:
    """Whether `exc` is the `requests.HTTPError` from a Cronometer 429.

    A 429 means the *account* is throttled, not that this one day's export
    is bad — unlike every other exception `sync_cronometer_range` catches,
    which really is independent per date (a network blip, a malformed CSV).
    Treating a 429 the same way and moving on to retry the next date is how
    one throttle hit became fourteen in a single real run: every date after
    the first was guaranteed to fail identically, against an endpoint that
    was already refusing the account's requests.
    """
    return (
        isinstance(exc, requests.exceptions.HTTPError)
        and exc.response is not None
        and exc.response.status_code == 429
    )


def _rate_limit_wait_hint(response: "requests.Response") -> str:
    """A concrete retry ETA from a 429 response's `Retry-After` header, or an
    honest admission there isn't one.

    `Retry-After` (RFC 7231 s7.1.3) is standard HTTP and comes in one of two
    shapes — a delta in seconds, or an HTTP-date — so both are tried. But
    Cronometer's export endpoint is reverse-engineered and undocumented,
    with no guarantee it sends the header at all: the 429s that prompted
    this carried no reason phrase either (`requests`'s default message
    renders as "429 Client Error:  for url: ..." with the reason blank),
    which already suggests this account isn't getting a considered error
    payload back. When there's genuinely nothing to parse, say so rather
    than inventing a wait time — a fabricated ETA is worse than none,
    because it reads as Cronometer's own answer rather than a guess.
    """
    retry_after = (response.headers.get("Retry-After") if response is not None else None) or ""
    retry_after = retry_after.strip()

    if not retry_after:
        return (
            "Cronometer sent no Retry-After header, so there's no reliable ETA. "
            "The failures so far read as a sustained account-level throttle "
            "rather than a brief burst limit — wait at least an hour before "
            "syncing again; retrying sooner risks extending it further."
        )

    if retry_after.isdigit():
        eta = datetime.now(timezone.utc) + timedelta(seconds=int(retry_after))
        return (
            f"Cronometer says retry after {retry_after}s "
            f"(around {eta.strftime('%Y-%m-%d %H:%M UTC')})."
        )

    try:
        eta = parsedate_to_datetime(retry_after)
    except (TypeError, ValueError):
        return f"Cronometer sent Retry-After: {retry_after!r} (unrecognised format)."
    return f"Cronometer says retry after {eta.strftime('%Y-%m-%d %H:%M UTC')}."


# The class name Cronometer serializes into a GWT-RPC failure when the session
# behind a request has expired. It reaches us in an export *URL* rather than an
# error body - see `CronometerSyncService._fetch_rows` for how.
_STALE_SESSION_MARKER = "NotLoggedInException"


def _is_stale_session(exc: Exception) -> bool:
    """Whether `exc` is the export endpoint refusing a dead saved session.

    Two independent triggers, because the poisoned nonce can surface in two
    shapes. The marker test catches the observed one, where the exception's own
    name was sent as the nonce and comes back url-encoded in the 403's message.
    The bare 403 is the backstop for the day upstream stops echoing the URL, or
    Cronometer serializes some other session exception: `/export` has no other
    reason to refuse a request that carried credentials this far.

    Deliberately not 401, and deliberately not every 4xx. Widening it would
    start clearing sessions and re-logging-in over failures a fresh login
    cannot fix, which is exactly the extra round trip a throttled account
    cannot afford. 429 in particular must never land here - that one is
    account-level, `_is_rate_limited` owns it, and a re-login in response to it
    would be the single worst thing to do next.
    """
    if _STALE_SESSION_MARKER in str(exc):
        return True
    return (
        isinstance(exc, requests.exceptions.HTTPError)
        and exc.response is not None
        and exc.response.status_code == 403
    )


def _stale_session_hint() -> str:
    """Why a 403 quoting a Java exception is not the bad password it looks like.

    The same job `_rate_limit_wait_hint` does for a 429: the raw message is
    `403 Client Error:  for url: ...nonce=com.cronometer.shared.user.
    exceptions.NotLoggedInException%2F844385496...`, which reads as a
    credentials problem and sends you to re-check an `.env` that was right all
    along - or worse, to retry, against an endpoint that throttles.
    """
    return (
        "That 403 is an expired saved session, not a bad password: the client "
        "resumes a cached session and validates it by minting an auth token, "
        "but Cronometer answers an expired one with HTTP 200 and a serialized "
        "NotLoggedInException - so the exception's own name gets sent as the "
        "export nonce. Any cached session has now been cleared and the next "
        "run will log in fresh. If it repeats on a clean login, check "
        "CRONOMETER_USERNAME/CRONOMETER_PASSWORD and that the account's "
        "subscription still allows web login."
    )


def sync_cronometer_range(dates: List[str], repository: LocalJSONRepository) -> List[dict]:
    """Every date in `dates`: one fetch for the span, then persisted per day.

    This deliberately does **not** mirror `sync_garmin_range`'s per-date
    loop, and the asymmetry is the point. Garmin has no restrictive rate
    limit, so it can afford a request per day and buys real per-day failure
    isolation with it. Cronometer throttles hard, and one day's fetch is
    about five HTTP requests rather than one (see `fetch_range_summaries`),
    so the per-day walk was itself provoking the 429s `_is_rate_limited`
    exists to survive — six days of catchup cost around thirty requests to
    retrieve six CSV rows the export endpoint would have returned together.

    What that trade costs: one request has one outcome, so a failure is no
    longer isolable to a single date. Less than it sounds — every Cronometer
    failure seen in the wild was a 429, which is account-level and already
    short-circuited the whole walk. Either kind of failure is now reported
    against the first date alone, since none of the others were attempted;
    `main` names the unattempted tail, and because nothing is checkpointed
    on that path `get_sync_date_range` finds the same days missing and
    retries them on the next run.
    """
    if not dates:
        return []

    days = sorted({_iso(day) for day in dates})
    service = CronometerSyncService()
    try:
        rows_by_day = service.fetch_range_summaries(days)
    except Exception as exc:
        failure = {"date": days[0], "error": str(exc)}
        if _is_rate_limited(exc):
            failure["rate_limited"] = True
            failure["wait_hint"] = _rate_limit_wait_hint(exc.response)
        elif _is_stale_session(exc):
            # Reaching here means the retry inside `_fetch_rows` failed too, or
            # there was no cached session to clear in the first place. Either
            # way the cache is gone now, so the next run starts clean - what
            # this flag buys is a legible reason, not a recovery.
            failure["stale_session"] = True
            failure["wait_hint"] = _stale_session_hint()
        return [failure]

    results = []
    for day in days:
        outcome = _persist_cronometer_day(day, rows_by_day[day], repository)
        outcome["date"] = day
        results.append(outcome)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync Garmin Connect and Cronometer data into data/biometrics.json.",
    )
    parser.add_argument(
        "--sync-garmin",
        action="store_true",
        help=(
            "Pull scale weigh-in, cardio sessions and overnight sleep/HRV "
            "readiness from Garmin Connect."
        ),
    )
    parser.add_argument(
        "--sync-cronometer",
        action="store_true",
        help="Pull the logged daily macro summary from Cronometer.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help=(
            "Day to sync, ISO YYYY-MM-DD. Defaults to yesterday - the first day whose logging is complete. Naming a day "
            "explicitly means that day only — pass --catchup as well to "
            "backfill up to it."
        ),
    )
    parser.add_argument(
        "--catchup",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Backfill every day missing between the latest recorded "
            "biometric and --date, not just --date itself. On by default "
            "for a bare run, off when --date names a specific day; pass "
            "--catchup or --no-catchup to say so outright."
        ),
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=14,
        help="Cap on how many days a --catchup run will walk back. Default 14.",
    )
    args = parser.parse_args(argv)

    if not (args.sync_garmin or args.sync_cronometer):
        parser.error("Nothing to do: pass --sync-garmin and/or --sync-cronometer.")

    repository = LocalJSONRepository()
    # A bare run targets yesterday, not today: the day is only complete once
    # it is over. Cronometer entries logged the evening before are in by then,
    # and a 07:30 same-day fetch was always asking about a half-empty day -
    # worse, its checkpoint then marked that day done, so late-logged entries
    # were stranded forever (get_sync_date_range never re-requests a
    # checkpointed date). Syncing yesterday means each day is fetched exactly
    # once, complete.
    target = _iso(args.date or (date.today() - timedelta(days=1)).isoformat())
    # Naming a date is a request for that date. Catchup stays on for a bare
    # run, which is the shape a scheduled sync takes and where a missed day
    # must not be lost — but "--date 2026-08-26" quietly fetching six other
    # days is the opposite of what asking for one day means, and against a
    # rate-limited Cronometer it is the difference between one export
    # request and a throttled account. An explicit --catchup still wins.
    catchup = args.catchup if args.catchup is not None else args.date is None
    # Scoped to the sync(es) actually requested — see get_sync_date_range's
    # docstring for why an unscoped call let a stale, never-run Cronometer
    # drag an already-caught-up Garmin back into a 14-day re-fetch every run.
    sources = [
        source
        for source, flag in (("garmin", args.sync_garmin), ("cronometer", args.sync_cronometer))
        if flag
    ]
    dates = (
        get_sync_date_range(repository, target, args.lookback_days, sources=sources)
        if catchup
        else [target]
    )
    failed = False

    if not dates:
        print(f"Nothing to sync: already up to date through {target}.")
        return 0
    if len(dates) > 1:
        print(f"Catching up {len(dates)} missing day(s): {dates[0]} through {dates[-1]}")

    if args.sync_garmin:
        for outcome in sync_garmin_range(dates, repository):
            day = outcome["date"]
            if "error" in outcome:
                # Each source is reported independently: a Garmin outage must
                # not cost the Cronometer sync that would have worked, on the
                # same reasoning as "a failed meal must not fail the week" —
                # and one bad day must not cost the rest of a catchup either.
                print(f"Garmin sync failed for {day}: {outcome['error']}", file=sys.stderr)
                failed = True
                continue
            weigh_in = outcome["weigh_in"]
            if has_measurements(weigh_in):
                print(f"Garmin weigh-in {day}: {weigh_in.get('weight_kg')} kg")
            else:
                print(f"Garmin weigh-in {day}: no reading")
            for session in outcome["activities"]:
                # Every activity, not just the cardio subset, and each says
                # whether it was stored: a `strength_training` session that
                # silently vanished between the watch and `activity_log`
                # would be indistinguishable from one Garmin never recorded,
                # which is exactly the question a missing schedule proposal
                # sends someone to this output to answer.
                mapped = (
                    f"-> {session['session_type']}"
                    if _storable(session)
                    else "(not stored: unmapped type or no start time)"
                )
                print(
                    f"  {session['start_time'] or '--:--'} {session['type']}: "
                    f"{session['duration_min']} min, "
                    f"{session['gross_calories']} kcal gross -> "
                    f"{session['net_calories']} kcal net {mapped}"
                )
            readiness = outcome["readiness"]
            if has_measurements(readiness):
                parts = []
                if readiness.get("sleep_score") is not None:
                    parts.append(
                        f"{readiness.get('readiness_label')} "
                        f"(sleep score {readiness['sleep_score']:.0f})"
                    )
                if readiness.get("hrv_ms") is not None:
                    parts.append(f"HRV {readiness['hrv_ms']:.0f} ms")
                print(f"  readiness: {', '.join(parts)} - not counted as energy")

    if args.sync_cronometer:
        cronometer_results = sync_cronometer_range(dates, repository)
        for outcome in cronometer_results:
            day = outcome["date"]
            if outcome.get("rate_limited") or outcome.get("stale_session"):
                print(f"Cronometer sync failed for {day}: {outcome['error']}", file=sys.stderr)
                print(f"  {outcome['wait_hint']}", file=sys.stderr)
                failed = True
                continue
            if "error" in outcome:
                print(f"Cronometer sync failed for {day}: {outcome['error']}", file=sys.stderr)
                failed = True
                continue
            actuals = outcome["daily_actuals"]
            if has_measurements(actuals):
                print(
                    f"Cronometer {day}: {actuals.get('calories')} kcal, "
                    f"P{actuals.get('protein_g')} / C{actuals.get('net_carbs_g')} / "
                    f"F{actuals.get('fat_g')}"
                )
            else:
                print(f"Cronometer {day}: nothing logged")

        # A failed range fetch leaves the whole span unfetched, and
        # sync_cronometer_range reports it against the first date only —
        # one request had one outcome. The tail was never attempted, which
        # isn't a second failure: nothing was checkpointed, so
        # get_sync_date_range finds those days still missing and retries
        # them on the next run, once any throttle has had a chance to clear.
        skipped = dates[len(cronometer_results):]
        if skipped:
            print(
                f"{len(skipped)} more day(s) ({skipped[0]} through {skipped[-1]}) "
                "were not fetched and will be picked up on the next run.",
                file=sys.stderr,
            )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

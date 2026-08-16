"""Pull measured reality in from Garmin Connect and Cronometer.

The input side of the adaptive loop. `config.json`'s `user_profile` says what
the body is aiming at and `nutrition_engine.py` does the arithmetic between
them, but both are useless without today's numbers — this module is what puts
those numbers in `biometrics.json` without a phone in the loop. It writes the
two lists `PlanRepository.load_biometrics` already promises and invents no
storage of its own:

    weigh_ins     <- GarminSyncService.fetch_body_composition
    daily_actuals <- CronometerSyncService.fetch_daily_summary

Five things here are decisions rather than detail, and each is load-bearing.

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
happens once, in `_daily_summary_row`.

**Exercise energy is discounted, never taken at face value.** Garmin reports
an activity's *gross* calories, which include the BMR the body would have
burned lying still for that hour — and TDEE already counts that hour. Adding
gross exercise calories to a TDEE estimate double-counts the overlap and
quietly inflates the day's allowance by a few hundred kcal, which is most of
a deficit. `EXERCISE_RECOVERY_FACTOR` halves it. See the constant.

**Sleep and HRV are readiness, not energy.** `fetch_readiness` exists and
returns a sleep score; nothing in it reaches a calorie figure. A sleep score
is a 0-100 index with no energy units behind it, so there is no arithmetic
that could legitimately turn it into kcal — the separation is enforced by
these being different methods writing different keys, not by a comment asking
the next caller nicely.

**Cronometer runs in-process.** `cronometer-mcp` requires Python >= 3.11,
which this project's Homebrew 3.14 venv satisfies, so `CronometerSyncService`
just imports and calls it directly — no sidecar interpreter involved.
"""

import argparse
import os
import sys
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# See the module docstring: `src/integrations/` is one level below the flat
# module layout the rest of the app relies on, so `src/` has to be put on the
# path by hand before any sibling import. Insert rather than append — a
# stray `repository.py` elsewhere on the path must not win over the project's.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from repository import LocalJSONRepository, run_sync  # noqa: E402

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

# Where garth caches its OAuth tokens. Overridable so a test never writes to
# the real one; the default matches garminconnect's own documented location.
GARMIN_TOKEN_DIR = os.path.expanduser(os.environ.get("GARMINTOKENS", "~/.garminconnect"))

PROJECT_ROOT = os.path.dirname(_SRC_DIR)

# Cronometer's daily-summary CSV column headers, mapped to the keys
# `daily_actuals` rows use. Several spellings per macro because the export has
# renamed columns across Cronometer releases and an older CSV must not parse
# into a row of zeroes — first header present wins.
CRONOMETER_MACRO_COLUMNS = {
    "calories": ("Energy (kcal)", "Energy (Calories)", "Calories"),
    "protein_g": ("Protein (g)", "Protein"),
    "net_carbs_g": ("Net Carbs (g)", "Net Carbs"),
    "fat_g": ("Fat (g)", "Fat"),
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
    ):
        _load_env()
        self.email = email or os.environ.get("GARMIN_EMAIL", "")
        self.password = password or os.environ.get("GARMIN_PASSWORD", "")
        self.token_dir = os.path.expanduser(token_dir)
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

    def fetch_cardio_activities(self, target_date: str) -> list:
        """Cardio sessions on `target_date`, with BMR-discounted calories.

        Each entry carries both `gross_calories` (what Garmin said) and
        `net_calories` (what this app is willing to add to a day), because a
        number that has been silently adjusted is impossible to reconcile
        against the watch when the two disagree. `EXERCISE_RECOVERY_FACTOR`
        explains the discount.

        Strength work, walks and anything else non-cardio is filtered out —
        see `CARDIO_ACTIVITY_KEYS`.
        """
        day = _iso(target_date)
        activities = self.client().get_activities_by_date(day, day) or []

        sessions = []
        for activity in activities:
            type_key = ((activity.get("activityType") or {}).get("typeKey") or "").lower()
            if not _is_cardio(type_key):
                continue

            gross = _as_float(activity.get("calories")) or 0.0
            duration_s = _as_float(activity.get("duration")) or 0.0
            sessions.append(
                {
                    "date": day,
                    "activity_id": activity.get("activityId"),
                    "name": activity.get("activityName"),
                    "type": type_key,
                    "duration_min": round(duration_s / 60.0, 1),
                    "gross_calories": round(gross),
                    "net_calories": round(gross * EXERCISE_RECOVERY_FACTOR),
                    "average_hr": _as_float(activity.get("averageHR")),
                    "source": "garmin",
                }
            )
        return sessions

    def fetch_readiness(self, target_date: str) -> dict:
        """Sleep score for `target_date`, as a readiness flag only.

        Deliberately separate from every other method here, and deliberately
        not summed into anything. A sleep score is a unitless 0-100 index —
        there is no conversion from it to kcal, so any energy equation that
        consumed it would be inventing one. Its legitimate use is deciding
        whether today is a day to train hard, which is a human's call.

        HRV is not returned at all, for the same reason and more strongly:
        it is the metric most likely to be mistaken for a recovery-cost
        number by a future caller looking for one.
        """
        day = _iso(target_date)
        try:
            payload = self.client().get_sleep_data(day) or {}
        except Exception:
            # Sleep is supplementary — a watch not worn overnight, or an
            # endpoint change, must not fail a weigh-in sync that succeeded.
            return {"date": day, "sleep_score": None, "readiness": None}

        daily = payload.get("dailySleepDTO") or {}
        scores = daily.get("sleepScores") or {}
        overall = scores.get("overall") or {}
        score = _as_float(overall.get("value"))
        sleep_seconds = _as_float(daily.get("sleepTimeSeconds"))

        return {
            "date": day,
            "sleep_score": score,
            "sleep_hours": round(sleep_seconds / 3600.0, 2) if sleep_seconds else None,
            # A label, not a coefficient. Nothing multiplies by this.
            "readiness": _readiness_label(score),
            "source": "garmin",
        }


def _is_cardio(type_key: str) -> bool:
    """Whether a Garmin `typeKey` is one of the cardio modalities tracked."""
    return any(key in type_key for key in CARDIO_ACTIVITY_KEYS)


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
        self.username = username or os.environ.get("CRONOMETER_USERNAME", "")
        self.password = password or os.environ.get("CRONOMETER_PASSWORD", "")

    def _require_credentials(self) -> None:
        if not self.username or not self.password:
            raise RuntimeError(
                "Cronometer credentials missing. Set CRONOMETER_USERNAME and "
                "CRONOMETER_PASSWORD in .env."
            )

    def _rows_in_process(self, day: str) -> List[dict]:
        from cronometer_mcp import CronometerClient  # type: ignore[import-not-found]

        target = datetime.strptime(day, "%Y-%m-%d").date()
        client = CronometerClient(username=self.username, password=self.password)
        return client.get_daily_summary(start=target, end=target)

    def fetch_daily_summary(self, target_date: str) -> dict:
        """What was actually eaten on `target_date`, as a `daily_actuals` row.

        Returns `{"date": ...}` alone for a day with nothing logged, on the
        same reasoning as `fetch_body_composition`: an unlogged day is a real
        state, and storing it as a day of zero calories would drag every
        average that reads the series.
        """
        day = _iso(target_date)
        self._require_credentials()
        rows = self._rows_in_process(day)
        return _daily_summary_row(rows, day)


def _daily_summary_row(rows: List[dict], day: str) -> dict:
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
        # No date column matched. A one-row export is unambiguous; anything
        # else would be a guess, and a wrong day silently overwrites a good
        # entry, so it is left unwritten instead.
        if len(rows) != 1:
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


def sync_garmin(target_date: str, repository: LocalJSONRepository) -> dict:
    """Fetch a day from Garmin and persist what it found.

    The weigh-in is the only part that becomes a stored row: cardio and
    readiness are returned for the caller to print, because `biometrics.json`
    has exactly two lists and neither of them is "activities". Adding a third
    would be a schema change reaching into `nutrition_engine` and the UI, and
    nothing consumes exercise calories yet — the discount they carry is what
    this sync is responsible for getting right when something does.
    """
    service = GarminSyncService()
    weigh_in = service.fetch_body_composition(target_date)
    cardio = service.fetch_cardio_activities(target_date)
    readiness = service.fetch_readiness(target_date)

    # Only write when the scale actually reported. `_prune` leaves a bare
    # `{"date": ...}` for a day with no reading, and storing that would create
    # a weigh-in row with no weight in it for `get_latest_biometrics` to find.
    if has_measurements(weigh_in):
        run_sync(repository.save_biometric_entry(weigh_in))

    return {"weigh_in": weigh_in, "cardio": cardio, "readiness": readiness}


def sync_cronometer(target_date: str, repository: LocalJSONRepository) -> dict:
    """Fetch a day from Cronometer and persist it to `daily_actuals`."""
    service = CronometerSyncService()
    actuals = service.fetch_daily_summary(target_date)

    if has_measurements(actuals):
        run_sync(repository.save_daily_actuals(actuals))

    return {"daily_actuals": actuals}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync Garmin Connect and Cronometer data into data/biometrics.json.",
    )
    parser.add_argument(
        "--sync-garmin",
        action="store_true",
        help="Pull scale weigh-in, cardio sessions and sleep readiness from Garmin Connect.",
    )
    parser.add_argument(
        "--sync-cronometer",
        action="store_true",
        help="Pull the logged daily macro summary from Cronometer.",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Day to sync, ISO YYYY-MM-DD. Defaults to today.",
    )
    args = parser.parse_args(argv)

    if not (args.sync_garmin or args.sync_cronometer):
        parser.error("Nothing to do: pass --sync-garmin and/or --sync-cronometer.")

    repository = LocalJSONRepository()
    target = _iso(args.date)
    failed = False

    if args.sync_garmin:
        try:
            result = sync_garmin(target, repository)
            weigh_in = result["weigh_in"]
            if has_measurements(weigh_in):
                print(f"Garmin weigh-in {target}: {weigh_in.get('weight_kg')} kg")
            else:
                print(f"Garmin weigh-in {target}: no reading")
            for session in result["cardio"]:
                print(
                    f"  cardio {session['type']}: {session['duration_min']} min, "
                    f"{session['gross_calories']} kcal gross -> "
                    f"{session['net_calories']} kcal net"
                )
            readiness = result["readiness"]
            if readiness.get("sleep_score") is not None:
                print(
                    f"  readiness: {readiness['readiness']} "
                    f"(sleep score {readiness['sleep_score']:.0f}) - not counted as energy"
                )
        except Exception as exc:
            # Each source is reported independently: a Garmin outage must not
            # cost the Cronometer sync that would have worked, on the same
            # reasoning as "a failed meal must not fail the week".
            print(f"Garmin sync failed: {exc}", file=sys.stderr)
            failed = True

    if args.sync_cronometer:
        try:
            actuals = sync_cronometer(target, repository)["daily_actuals"]
            if has_measurements(actuals):
                print(
                    f"Cronometer {target}: {actuals.get('calories')} kcal, "
                    f"P{actuals.get('protein_g')} / C{actuals.get('net_carbs_g')} / "
                    f"F{actuals.get('fat_g')}"
                )
            else:
                print(f"Cronometer {target}: nothing logged")
        except Exception as exc:
            print(f"Cronometer sync failed: {exc}", file=sys.stderr)
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

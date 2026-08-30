"""Deterministic energy and macro arithmetic — no API calls, no I/O, no config file.

The measured half of the app's loop. `config.json`'s `user_profile` says what
the body is aiming at, `biometrics.json` says where it actually is, and every
number in between is computed here in plain Python — the same rule the rest of
the planner already lives by: **Python calculates the targets, the model only
fills in food.** An expenditure estimate a model produced would be a plausible
number with no arithmetic behind it, and it would drift every run.

Everything here is a pure function of its arguments. Nothing reads a file,
nothing imports `planner`, and nothing needs an event loop, which is what lets
`calculate_macro_targets` run in a UI callback and in a test with equal ease.
The two dict shapes it consumes are the ones `repository.PlanRepository`
already promises:

    weigh_in     {"date": "2026-08-16", "weight_kg": 98.4, "body_fat_pct": 27.5}
    daily_actual {"date": "2026-08-16", "calories": 1820, "protein_g": 141, ...}

Three ideas are worth reading before changing anything:

- **Protein is locked to the *target* weight, not today's.** 80 kg x 1.8 is
  144 g whether you weigh 100 kg or 84 kg, so the floor doesn't sink as the
  scale does — the point of the protein is to keep the lean mass you're
  carrying toward that target.
- **The deficit slides with the gap.** 750 kcal at 100 kg is comfortable when
  there is a lot to lose and reckless at 82 kg, where it's most of a day's
  discretionary energy. `calculate_dynamic_deficit` ramps it down to 350 as
  the target approaches, so the aggression tapers automatically instead of
  needing a config edit every few kilos.
- **Fat is always derived, never chosen.** `derive_fat_g` spends whatever
  energy protein and carbs didn't, so the four macros always reconcile to the
  calorie figure. A typed fat number could only disagree with the other three.
- **The deficit is also capped by fat mass, not just distance to target.**
  `calculate_dynamic_deficit` layers `alpert_fat_energy_ceiling_kcal` — Alpert's
  measured limit on how fast fat alone can fund a deficit before fat-free mass
  starts covering the shortfall instead — on top of the weight ramp above. It
  is a second, independent floor under lean mass, not a replacement for the
  first: it only ever pulls the deficit down, engages only when `body_fat_pct`
  is known, and rarely binds outside a lean, heavy, or long-running cut.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

# Energy density of the macros, in kcal per gram. Atwater factors — the same
# constants `planner.derive_fat_g` divides by, spelled out here because this
# module is deliberately free of planner imports.
KCAL_PER_G_PROTEIN = 4.0
KCAL_PER_G_CARB = 4.0
KCAL_PER_G_FAT = 9.0

# Energy in a kilogram of body tissue. The textbook 7700 kcal/kg (3500/lb)
# figure for mixed fat-and-lean loss — approximate by nature, which is exactly
# why `calculate_adaptive_tdee` smooths its input before multiplying by it:
# a constant this large turns a 0.5 kg water swing into ~275 kcal/day of
# phantom expenditure over a fortnight.
KCAL_PER_KG_TISSUE = 7700.0

# Katch-McArdle: BMR = 370 + 21.6 x lean body mass. Preferred over
# Mifflin-St Jeor whenever a body-fat reading exists, because lean mass is
# what actually burns the energy — height, age and gender are only proxies
# for it, which is why the Katch path ignores all three.
KATCH_INTERCEPT = 370.0
KATCH_LBM_COEFFICIENT = 21.6

# Mifflin-St Jeor's sex constant: +5 male, -161 female. The rest of the
# equation (10 x kg + 6.25 x cm - 5 x age) is identical for both.
MIFFLIN_SEX_CONSTANT = {"male": 5.0, "female": -161.0}

# Multipliers from BMR to total daily expenditure. Kept as an explicit table
# rather than a formula because they are convention, not arithmetic; an
# unknown key raises rather than defaulting, on the same reasoning as
# `planner.calculate_daily_targets` refusing to plan against a typo'd weekday.
ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light_office": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

# The sliding deficit's two anchors. At or above `DEFICIT_CEILING_WEIGHT_KG`
# the full deficit applies; it ramps linearly down to `DEFICIT_FLOOR_KCAL` as
# the target weight is reached.
DEFICIT_CEILING_KCAL = 750.0
DEFICIT_FLOOR_KCAL = 350.0
DEFICIT_CEILING_WEIGHT_KG = 100.0

# A second, independent cap on the same deficit: Alpert's measured limit on
# how fast fat mass alone can supply energy before fat-free mass starts
# covering the shortfall instead — (290 +/- 25) kJ/kg fat mass/day, derived
# from the Minnesota Starvation Experiment data. Alpert SS, "A limit on the
# energy transfer rate from the human fat store in hypophagia," J Theor Biol.
# 2005;233(1):1-13 (PMID 15615615). 290 kJ / 4.184 = 69.3 kcal.
ALPERT_KCAL_PER_KG_FAT_MASS = 69.3

# Margin kept below that measured ceiling rather than programming a deficit
# right up against it: the published figure carries its own ~9% (25/290)
# measurement uncertainty, so this app keeps a further buffer instead of
# planning at the exact edge of what the study measured.
ALPERT_SAFETY_FACTOR = 0.80

# Baseline daily net carbohydrate allowance when no schedule supplies one.
# Low, so the derived fat figure absorbs the remaining energy — the app has no
# keto flag for the same reason (see CLAUDE.md): a low-carb day is just a low
# carb number, and `derive_fat_g` turns it into a high-fat day by itself.
DEFAULT_NET_CARBS_G = 60.0

# Smoothing factor for the weigh-in series. 0.3 keeps roughly a working week
# of history in view: high enough to follow a real trend within a fortnight,
# low enough that one dehydrated Monday morning doesn't move the estimate.
DEFAULT_SMOOTHING_ALPHA = 0.3

# Below this many days between the first and last weigh-in, the trend is not
# estimated at all. Dividing a noisy delta by a 2-day span and multiplying by
# 7700 produces four-figure swings in the answer from ordinary water weight —
# a confidently wrong TDEE is worse than an honest None, because the caller
# can fall back to the Mifflin/Katch estimate but cannot detect nonsense.
MIN_TREND_SPAN_DAYS = 7

MACRO_KEYS = ("calories", "protein_g", "net_carbs_g", "fat_g")


def _parse_iso_date(value: str) -> Optional[date]:
    """An ISO `YYYY-MM-DD` string as a `date`, or None if it isn't one.

    Tolerant rather than raising: these strings come from `biometrics.json`,
    which a scale integration writes and a human may hand-edit. One malformed
    row must cost that row, not the whole trend estimate.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def age_from_birth_date(birth_date: str, on_date: Optional[date] = None) -> int:
    """Whole years old on `on_date` (default today).

    `user_profile` stores `birth_date` rather than an age deliberately — a
    stored age is wrong within a year of being written and nothing ever tells
    you. The subtraction that fixes that lives here.
    """
    born = _parse_iso_date(birth_date)
    if born is None:
        raise ValueError(f"birth_date must be ISO YYYY-MM-DD: got {birth_date!r}")

    today = on_date or date.today()
    # The (month, day) comparison is the birthday-hasn't-happened-yet subtraction.
    had_birthday = (today.month, today.day) >= (born.month, born.day)
    return today.year - born.year - (0 if had_birthday else 1)


def calculate_bmr(
    weight_kg: float,
    height_cm: float,
    age: int,
    gender: str = "male",
    body_fat_pct: Optional[float] = None,
) -> float:
    """Basal metabolic rate in kcal/day.

    Two formulas, picked by what's actually known:

    - **Katch-McArdle**, when `body_fat_pct` is given. Lean mass is the tissue
      doing the burning, so measuring it beats inferring it. This path ignores
      `height_cm`, `age` and `gender` entirely — that is the point, not an
      oversight: they exist in the other formula only to approximate the lean
      mass this one has been handed directly.
    - **Mifflin-St Jeor** otherwise, the standard when only the tape measure
      and the calendar are available.

    A body-fat percentage outside 3-70% is rejected rather than used: a smart
    scale reporting `0` (its "couldn't read" value) would otherwise be taken
    as pure lean mass and inflate BMR by hundreds of kcal, which then flows
    straight into the calorie target as a silently too-generous day.
    """
    if weight_kg <= 0:
        raise ValueError(f"weight_kg must be positive: got {weight_kg!r}")

    if body_fat_pct is not None:
        if not 3.0 <= body_fat_pct <= 70.0:
            raise ValueError(
                f"body_fat_pct must be between 3 and 70: got {body_fat_pct!r}"
            )
        lean_body_mass_kg = weight_kg * (1.0 - body_fat_pct / 100.0)
        return KATCH_INTERCEPT + KATCH_LBM_COEFFICIENT * lean_body_mass_kg

    if height_cm is None or height_cm <= 0:
        raise ValueError(
            f"height_cm is required for Mifflin-St Jeor (no body_fat_pct given): "
            f"got {height_cm!r}"
        )
    if age is None or age < 0:
        raise ValueError(f"age must be a non-negative number of years: got {age!r}")

    key = (gender or "male").strip().lower()
    if key not in MIFFLIN_SEX_CONSTANT:
        raise ValueError(
            f"gender must be one of {sorted(MIFFLIN_SEX_CONSTANT)}: got {gender!r}"
        )

    return 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age + MIFFLIN_SEX_CONSTANT[key]


def calculate_tdee(bmr: float, activity_level: str = "light_office") -> float:
    """Total daily energy expenditure: BMR scaled by an activity factor.

    Raises on an unrecognised level rather than falling back to sedentary — a
    typo in `user_profile.activity_level` would otherwise quietly cost ~350
    kcal/day of target and look like nothing at all.
    """
    key = (activity_level or "").strip().lower()
    if key not in ACTIVITY_FACTORS:
        raise ValueError(
            f"Unknown activity_level {activity_level!r}. "
            f"Valid levels: {sorted(ACTIVITY_FACTORS)}"
        )
    return bmr * ACTIVITY_FACTORS[key]


# How far a measured (adaptive) TDEE may sit from the formula estimate before
# it is treated as bad data rather than a better measurement.
#
# Mifflin and Katch are population regressions that sit within roughly 10-15%
# of an individual, so a genuine personal difference of 25% is already at the
# edge of plausible. Past that the likelier explanations are all artefacts:
# systematic under-logging (the common one, which *depresses* the estimate), a
# fortnight of water-weight swing dominating the trend, or a stretch of days
# nobody logged. `calculate_adaptive_tdee` deliberately returns its figure
# unclamped so that bad data stays visible rather than being folded into a
# plausible-looking number; this is the caller-side sanity check its docstring
# asks for.
ADAPTIVE_TDEE_TOLERANCE = 0.25


def reconcile_adaptive_tdee(
    formula_tdee: float, adaptive_tdee: Optional[float]
) -> Tuple[float, str]:
    """Pick between the population formula and the measured estimate.

    Returns `(tdee, source)`, where source is one of:

    - `"formula"` — no adaptive figure was available at all (too few weigh-ins,
      too short a span, nothing logged). The normal state on a fresh checkout.
    - `"adaptive"` — the measured figure was close enough to the formula to
      believe, and is used.
    - `"formula_adaptive_rejected"` — a measured figure existed but sat outside
      `ADAPTIVE_TDEE_TOLERANCE`, so the formula was kept. Distinct from plain
      `"formula"` on purpose: "we measured and disbelieved it" is a different
      state from "we had nothing to measure", and only the first one is worth
      investigating.

    Deliberately *chooses* rather than blends. A weighted average of a good
    estimate and a bad one is a slightly bad estimate with no way to tell which
    it was — where picking leaves `basis` able to say plainly which number the
    week was planned against.
    """
    if not adaptive_tdee or adaptive_tdee <= 0:
        return formula_tdee, "formula"
    low = formula_tdee * (1 - ADAPTIVE_TDEE_TOLERANCE)
    high = formula_tdee * (1 + ADAPTIVE_TDEE_TOLERANCE)
    if not low <= adaptive_tdee <= high:
        return formula_tdee, "formula_adaptive_rejected"
    return adaptive_tdee, "adaptive"


def alpert_fat_energy_ceiling_kcal(
    current_weight_kg: float, body_fat_pct: Optional[float]
) -> Optional[float]:
    """The deficit ceiling implied by fat mass alone, or None when unknown.

    `ALPERT_KCAL_PER_KG_FAT_MASS x fat_mass_kg x ALPERT_SAFETY_FACTOR` — past
    this rate the body starts covering the shortfall from fat-free mass
    rather than fat. Deliberately returns None instead of assuming a body-fat
    percentage when one wasn't measured: guessing one to compute a "safety"
    ceiling would be exactly the fabricated body `calculate_macro_targets`
    already refuses to invent elsewhere.
    """
    if body_fat_pct is None:
        return None
    fat_mass_kg = current_weight_kg * (body_fat_pct / 100.0)
    return fat_mass_kg * ALPERT_KCAL_PER_KG_FAT_MASS * ALPERT_SAFETY_FACTOR


def calculate_dynamic_deficit(
    current_weight_kg: float,
    target_weight_kg: float = 80.0,
    body_fat_pct: Optional[float] = None,
) -> float:
    """The day's calorie deficit, scaled to how far there is left to go.

    Linear between two anchors: the full `DEFICIT_CEILING_KCAL` at or above
    100 kg, tapering to `DEFICIT_FLOOR_KCAL` as `target_weight_kg` is reached,
    and holding at the floor below it.

    The taper is the whole point. A fixed 750 kcal is a sane cut with 20 kg to
    lose and an aggressive one with 2 kg left, where it starts costing lean
    mass and adherence at exactly the stage they matter most. Sliding the
    number means the aggression tapers on its own instead of relying on
    someone remembering to edit config every few kilos.

    When `body_fat_pct` is supplied, the ramped figure above is additionally
    capped at `alpert_fat_energy_ceiling_kcal` — never raised by it, only ever
    pulled down. The weight-only ramp is usually the binding constraint; the
    Alpert cap only takes over once fat mass itself, not just weight, is
    getting scarce (a heavy but already-lean body, or a cut that has run past
    what the weight ramp alone accounts for). Omitting `body_fat_pct`
    reproduces exactly this function's behaviour from before the cap existed.
    """
    if current_weight_kg <= target_weight_kg:
        ramped = DEFICIT_FLOOR_KCAL
    elif current_weight_kg >= DEFICIT_CEILING_WEIGHT_KG:
        ramped = DEFICIT_CEILING_KCAL
    else:
        span_kg = DEFICIT_CEILING_WEIGHT_KG - target_weight_kg
        if span_kg <= 0:
            # A target at or above the ceiling weight leaves no ramp to
            # interpolate over. Anything above the target still gets the full
            # deficit; the branches above have already handled at-or-below.
            ramped = DEFICIT_CEILING_KCAL
        else:
            progress = (current_weight_kg - target_weight_kg) / span_kg
            ramped = (
                DEFICIT_FLOOR_KCAL
                + (DEFICIT_CEILING_KCAL - DEFICIT_FLOOR_KCAL) * progress
            )

    ceiling = alpert_fat_energy_ceiling_kcal(current_weight_kg, body_fat_pct)
    return ramped if ceiling is None else min(ramped, ceiling)


def resolve_current_weight_kg(
    profile: dict, latest_biometrics: Optional[dict]
) -> Optional[float]:
    """The weight `calculate_macro_targets` plans against — the latest weigh-in,
    falling back to `user_profile.current_weight_kg`.

    Pulled out of `calculate_macro_targets` so a second caller (the training
    editor's derived burn estimate, `ui_state.PlannerState.estimate_burn`) can
    ask the same question without duplicating the fallback rule. Unlike its
    original caller this returns `None` rather than raising — a UI default
    that can't be computed should just not offer one, not take the page down.
    """
    biometrics = latest_biometrics or {}
    return biometrics.get("weight_kg") or (profile or {}).get("current_weight_kg")


# MET (metabolic equivalent of task) per training-schedule session type, for
# `estimate_session_burn_kcal`'s default `estimated_burn_kcal`. Restated here
# rather than imported from `planner.TRAINING_INTENSITY_SPLIT` — this module
# is deliberately free of `planner`'s dependency graph, the same reasoning
# `derive_fat_g` above already documents for duplicating the Atwater
# constants rather than importing them. Keys and rough intensity match
# `TRAINING_INTENSITY_SPLIT`'s; values are Compendium-of-Physical-Activities
# ballpark figures (resistance training ~6, vigorous interval work ~8-10,
# running/cycling ~7.5-9.8, brisk walking ~3.5) — approximate by nature, the
# same caveat `KCAL_PER_KG_TISSUE`'s 7700 figure already carries, and good
# enough for an editable *default* rather than a claimed measurement.
MET_VALUES = {
    "gym_hypertrophy": 6.0,
    "cardio_hiit": 8.5,
    "cardio_run": 9.8,
    "cardio_ride": 7.5,
    "cardio_easy": 5.0,
    "walk": 3.5,
    "rest": 0.0,
    # Prefix fallbacks, longest-first at match time — mirrors
    # `ui_theme.training_icon`'s lookup so a future `gym_strength` or
    # `cardio_swim` gets a sensible estimate with no edit here.
    "gym": 6.0,
    "cardio": 7.5,
}
# What an unrecognised type gets — a light-moderate general-activity MET
# rather than 0, since an unknown but real session still burns something.
MET_FALLBACK = 5.0


def estimate_session_burn_kcal(
    session_type: str, duration_minutes: float, weight_kg: float
) -> float:
    """A MET-based default for `estimated_burn_kcal` — kcal, not a measurement.

    `kcal = MET * 3.5 * weight_kg / 200 * minutes`, the standard ACSM
    metabolic-equivalent formula: `MET * 3.5 * weight_kg / 200` is kcal/min at
    that intensity for that body. Deliberately editable everywhere it's used
    (see `ui_state.PlannerState.estimate_burn`) — nobody knows their real
    session burn, so this exists to stop the field defaulting to an arbitrary
    flat number, not to claim a measurement `apply_training_adjustments`
    should trust blindly.

    Lookup is exact match, then longest prefix, same as `training_icon` — a
    type this table hasn't heard of still gets a plausible estimate
    (`MET_FALLBACK`) rather than zero or a raise.
    """
    met = MET_VALUES.get(session_type)
    if met is None:
        matches = [key for key in MET_VALUES if session_type.startswith(key)]
        met = MET_VALUES[max(matches, key=len)] if matches else MET_FALLBACK
    return met * 3.5 * weight_kg / 200 * duration_minutes


def derive_fat_g(calories: float, protein_g: float, net_carbs_g: float) -> float:
    """Fat is whatever energy is left once protein and carbs are paid for.

    Identical in rule and result to `planner.derive_fat_g` — the app has one
    fat rule, so a per-meal override, a whole-day target and a computed
    expenditure target are all derived the same way. It is restated here
    rather than imported because this module is deliberately free of
    `planner`'s dependency graph (instructor, openai, the whole API layer),
    and `planner` is the natural direction for the eventual collapse: it
    should import this one, not the reverse.

    Floored at 0 so an impossible budget (protein and carbs already over the
    calorie figure) reads as "no fat left" rather than a negative gram count
    that would silently subtract from the day elsewhere.
    """
    spent = protein_g * KCAL_PER_G_PROTEIN + net_carbs_g * KCAL_PER_G_CARB
    return max(0.0, (calories - spent) / KCAL_PER_G_FAT)


def calculate_macro_targets(
    user_profile: dict,
    latest_biometrics: Optional[dict] = None,
    net_carbs_g: Optional[float] = None,
    adaptive_tdee: Optional[float] = None,
) -> dict:
    """A full day's macro target for the person `user_profile` describes.

    The assembly point for everything above, in order: current weight and body
    fat from the latest weigh-in, BMR from whichever formula that supports,
    TDEE from the activity factor, deficit from the remaining gap, then
    protein locked to the *target* weight, carbs from `net_carbs_g` (or the
    baseline), and fat deriving whatever energy is left.

    `net_carbs_g` is the hook for schedule-driven carbs — pass
    `weekly_schedule[day]["net_carbs_g"]` to cycle carbs by training day, or
    leave it out for `DEFAULT_NET_CARBS_G`. It is a keyword with a default so
    the two-argument call in the rest of the app keeps working unchanged.

    **Protein does not track current weight.** `target_weight_kg x
    protein_multiplier` is a constant 144 g for an 80 kg / 1.8 profile from
    the first day to the last. Tying it to today's weight would shrink the
    protein floor exactly as the diet started to threaten lean mass, which is
    backwards.

    Returns the four `MACRO_KEYS` at the top level — so
    `{k: result[k] for k in MACRO_KEYS}` drops straight into a
    `weekly_schedule` entry — plus a `basis` sub-dict showing the working.
    The diagnostics are nested rather than flat precisely because
    `DaySchedule` is `extra="forbid"`: a flat `bmr` key would make the obvious
    `**result` splat fail schema validation.

    Raises when a required input is missing rather than substituting a
    plausible body. Planning a week against a fabricated weight produces a
    target that looks entirely normal and is wrong every day of it.
    """
    profile = user_profile or {}
    biometrics = latest_biometrics or {}

    weight_kg = resolve_current_weight_kg(profile, latest_biometrics)
    if not weight_kg:
        raise ValueError(
            "No current weight available: pass a weigh-in with 'weight_kg' as "
            "latest_biometrics, or set 'current_weight_kg' on user_profile."
        )

    # A scale that couldn't get a reading writes 0 rather than omitting the
    # key, so this is `or None` — not `.get(...)` — to send a falsy reading
    # down the Mifflin path instead of into Katch's validator.
    body_fat_pct = biometrics.get("body_fat_pct") or None

    height_cm = profile.get("height_cm")
    birth_date = profile.get("birth_date")
    # Age is only needed by Mifflin; a Katch profile legitimately has neither
    # birth date nor height, so this stays None rather than raising there.
    age = age_from_birth_date(birth_date) if birth_date else None
    if body_fat_pct is None and age is None:
        raise ValueError(
            "Mifflin-St Jeor needs an age: set 'birth_date' on user_profile, "
            "or supply a weigh-in carrying 'body_fat_pct' to use Katch-McArdle."
        )

    bmr = calculate_bmr(
        weight_kg=weight_kg,
        height_cm=height_cm,
        age=age,
        gender=profile.get("gender") or "male",
        body_fat_pct=body_fat_pct,
    )
    activity_level = profile.get("activity_level") or "light_office"
    # The formula estimate is always computed, even when an adaptive figure
    # supersedes it: `reconcile_adaptive_tdee` needs it as the sanity bound,
    # and `basis` reports both so two runs a fortnight apart can be compared.
    tdee_formula = calculate_tdee(bmr, activity_level)
    tdee, tdee_source = reconcile_adaptive_tdee(tdee_formula, adaptive_tdee)

    target_weight_kg = profile.get("target_weight_kg") or weight_kg
    deficit = calculate_dynamic_deficit(weight_kg, target_weight_kg, body_fat_pct)
    calories = max(0.0, tdee - deficit)
    alpert_ceiling = alpert_fat_energy_ceiling_kcal(weight_kg, body_fat_pct)

    protein_multiplier = profile.get("protein_multiplier") or 1.8
    protein_g = target_weight_kg * protein_multiplier

    carbs_g = DEFAULT_NET_CARBS_G if net_carbs_g is None else net_carbs_g
    fat_g = derive_fat_g(calories, protein_g, carbs_g)

    return {
        "calories": round(calories),
        "protein_g": round(protein_g, 1),
        "net_carbs_g": round(carbs_g, 1),
        "fat_g": round(fat_g, 1),
        "basis": {
            "bmr": round(bmr, 1),
            "tdee": round(tdee, 1),
            # Which number the week was actually planned against, and what the
            # formula would have said. Equal values with source "formula" is
            # the normal state until enough weigh-ins and logs accumulate.
            "tdee_source": tdee_source,
            "tdee_formula": round(tdee_formula, 1),
            "tdee_adaptive": round(adaptive_tdee, 1) if adaptive_tdee else None,
            "deficit_kcal": round(deficit, 1),
            # None unless the weigh-in carried body_fat_pct — the fat-mass-only
            # cap calculate_dynamic_deficit layers on top of the weight ramp.
            # Whether it was the binding constraint is visible by comparing
            # this to deficit_kcal above (equal deficit_kcal to the ramp value
            # means it wasn't).
            "alpert_ceiling_kcal": (
                round(alpert_ceiling, 1) if alpert_ceiling is not None else None
            ),
            "current_weight_kg": weight_kg,
            "target_weight_kg": target_weight_kg,
            "activity_level": activity_level,
            # Which formula produced the BMR — the one diagnostic worth having
            # when two runs a week apart disagree by 200 kcal because a scale
            # started (or stopped) reporting body fat.
            "bmr_method": "katch_mcardle" if body_fat_pct is not None else "mifflin_st_jeor",
        },
    }


def _exponential_smooth(
    values: Sequence[float], alpha: float = DEFAULT_SMOOTHING_ALPHA
) -> List[float]:
    """One forward exponential-moving-average pass over `values`."""
    smoothed: List[float] = []
    running = None
    for value in values:
        running = value if running is None else alpha * value + (1 - alpha) * running
        smoothed.append(running)
    return smoothed


def smooth_series(
    values: Sequence[float], alpha: float = DEFAULT_SMOOTHING_ALPHA
) -> List[float]:
    """Exponentially smooth `values` **forwards then backwards**.

    For *display* — the weight-trend line a UI draws through a scatter of
    daily weigh-ins. The second pass cancels the phase lag a single forward
    EMA introduces, so the curve sits on the points rather than trailing them.

    **Not used to estimate the trend — see `_trend_slope_kg_per_day`.** It is
    kept separate rather than deleted because smoothing for the eye and
    estimating a rate are different jobs, and this one is only correct at the
    first.
    """
    forward = _exponential_smooth(values, alpha)
    backward = _exponential_smooth(list(reversed(forward)), alpha)
    return list(reversed(backward))


def _trend_slope_kg_per_day(days: Sequence[float], weights: Sequence[float]) -> float:
    """Least-squares slope of `weights` against `days`, in kg per day.

    Negative while losing. Regressing on the real day numbers rather than on
    list position is what keeps an irregular weighing habit honest — three
    weigh-ins in one week and one the next describe the same trend either way.

    **Why least squares and not the exponentially smoothed endpoints.** Reading
    the trend off a smoothed series' first and last values is the obvious
    approach and it is measurably wrong: every EMA has a startup transient, so
    both ends get dragged toward the middle and the delta between them comes
    out short. Handed a *noise-free* 1.00 kg decline over 14 days, the
    forward-backward smoother in `smooth_series` returns 0.74 kg — a 26%
    understatement with no noise present to excuse it.

    Measured over 4000 trials against a 1.00 kg / 14-day decline with 0.6 kg
    of Gaussian scale noise, expressed as the error each estimator puts into
    the final kcal/day figure:

    | estimator                     |    bias | spread |
    |-------------------------------|---------|--------|
    | raw first-minus-last          |    +1.0 |  460.9 |
    | smoothed first-minus-last     |  -143.6 |  238.4 |
    | smoothed, least-squares slope |  -112.9 |  226.7 |
    | **raw, least-squares slope**  |  **+3.2** | **272.5** |

    Least squares on the raw series is the only one that is both unbiased and
    quiet: it removes 41% of the noise the naive difference carries, because
    fitting all n points *is* the noise damping — smoothing first only adds
    the bias back. The distinction matters because bias and spread are not
    equally harmful here. Spread is visible, jitters run to run, and averages
    out over a few weeks; a 113-143 kcal/day bias points the same way every
    week, quietly understating expenditure, and lands as a deficit that much
    deeper than the one that was actually chosen.
    """
    n = len(days)
    mean_day = sum(days) / n
    mean_weight = sum(weights) / n
    covariance = sum(
        (day - mean_day) * (weight - mean_weight) for day, weight in zip(days, weights)
    )
    variance = sum((day - mean_day) ** 2 for day in days)
    if variance == 0:
        return 0.0
    return covariance / variance


def _in_window(rows: List[dict], end: date, window_days: int) -> List[Tuple[date, dict]]:
    """`rows` that carry a parseable date within `window_days` before `end`,
    oldest first. Undated and malformed rows are dropped, not raised on."""
    start = end - timedelta(days=window_days)
    dated = []
    for row in rows or []:
        when = _parse_iso_date((row or {}).get("date", ""))
        if when is not None and start <= when <= end:
            dated.append((when, row))
    dated.sort(key=lambda pair: pair[0])
    return dated

# Which precondition stopped the measurement, or `ADAPTIVE_READY` when none
# of them did. All four are legitimate states of a real database — the first
# three are simply what a young or gappy history looks like — and telling them
# apart is the entire reason this sibling exists. As a bare `Optional[float]`,
# "nobody has stood on the scale" and "four weigh-ins this week, but all
# within three days of each other" are the same `None`, and both read through
# to `basis["tdee_source"]` as the same `"formula"` a fresh checkout with an
# empty `biometrics.json` produces.
ADAPTIVE_READY = "ready"
ADAPTIVE_NO_WEIGH_INS = "no_weigh_ins"
ADAPTIVE_SHORT_SPAN = "short_span"
ADAPTIVE_NO_LOGS = "no_logs"


@dataclass(frozen=True)
class AdaptiveTDEEStatus:
    """What `calculate_adaptive_tdee` answered, and what it measured to decide.

    `estimate` is exactly what the bare function returns — None whenever
    `state` is not `ADAPTIVE_READY` — so a caller that only wants the number
    is unaffected by this type existing. The counts beside it are what a
    caller needs in order to say *why*, in the units the person can act on:
    stand on the scale again a week from the last time, or log the days you
    have already eaten.

    `span_days` is the gap between the first and last weigh-in **inside the
    window**, and it is the precondition worth naming loudest. It is the one
    that collapses while every visible count still looks healthy — five
    weigh-ins and five logged days, all bunched into three days — and the one
    a fully caught-up Cronometer cannot fix.

    Every count is taken inside the same window the estimate would have used,
    so a reported figure is one the arithmetic actually saw rather than a
    whole-file total that flatters it.
    """

    state: str
    estimate: Optional[float]
    weigh_ins: int
    span_days: int
    logged_days: int
    window_days: int
    required_span_days: int = MIN_TREND_SPAN_DAYS

    @property
    def ready(self) -> bool:
        return self.state == ADAPTIVE_READY


def measure_adaptive_tdee(
    daily_logs: list,
    weigh_in_history: list,
    window_days: int = 14,
) -> AdaptiveTDEEStatus:
    """Expenditure inferred from what was actually eaten and what the scale did.

        adaptive TDEE = mean logged calories + (kg lost per day x 7700)

    This is the honest number. Mifflin and Katch are population regressions
    that can sit 300 kcal from an individual; this measures the one body in
    question. Eat 2000 kcal a day and lose 0.5 kg a fortnight and you expended
    about 2275, whatever a formula predicted.

    **Sign convention:** `weight_delta_kg` is *weight lost*, positive while
    losing. Losing weight means expenditure exceeded intake, so the term is
    added. Inverting this is the easy mistake and it doubles the error rather
    than cancelling it.

    Three deliberate departures from the naive formula:

    - **The trend is a least-squares slope over the whole series, not the
      difference between the two end weigh-ins** (smoothed or otherwise). See
      `_trend_slope_kg_per_day` for the measurements behind that — briefly,
      differencing the ends of a smoothed series understates a noise-free
      1 kg decline by 26%, and differencing raw ends is unbiased but twice as
      noisy as the fit.

    - **There is no `/ window_days` divisor.** The regression is fitted
      against real elapsed days, so its slope is already kg per day and
      needs no dividing. This matters when the data is younger than the
      window: dividing a genuine 6-day trend by a nominal 14 would understate
      the daily rate by more than half and drag the estimate toward the logged
      intake — which is to say, toward concluding you eat at maintenance
      whenever your history is short. `window_days` selects *which* rows
      count; it is not a unit of time the rate is expressed in.
    - **A span under `MIN_TREND_SPAN_DAYS` returns no estimate.** Multiplying
      a two-day wobble by 7700 is noise amplification, not measurement.

    The three unmet-precondition states are a "keep using the formula
    estimate" signal, not an error, and `calculate_adaptive_tdee` collapses
    all three back to the `None` its callers have always received. They are
    reported separately here because the *reporting* was the gap: measured
    against a real `biometrics.json` carrying five weigh-ins and five logged
    days, this returns `ADAPTIVE_SHORT_SPAN` with a three-day span against a
    floor of seven — a database that looks by every visible count like it
    should be measuring, and isn't.

    The estimate is returned unclamped. Systematic under-logging (the common
    case) depresses it, and a caller wiring this into a live target should
    sanity-check it against `calculate_tdee` — `reconcile_adaptive_tdee` is
    that check — rather than trusting it blind; clamping here would hide bad
    data inside a plausible-looking number.
    """
    weigh_ins = [row for row in (weigh_in_history or []) if (row or {}).get("weight_kg")]
    dates = [
        d
        for d in (_parse_iso_date((row or {}).get("date", "")) for row in weigh_ins)
        if d
    ]

    # Anchored on the most recent weigh-in rather than on today, so a series
    # that stops a month before it is read still yields the trend it actually
    # recorded instead of an empty window.
    windowed = _in_window(weigh_ins, max(dates), window_days) if dates else []
    span_days = (windowed[-1][0] - windowed[0][0]).days if len(windowed) >= 2 else 0

    # Anchored on the newest windowed weigh-in for the same reason: without
    # one there is no window to count logs in, and 0 is then the honest count
    # rather than a whole-file total nothing would have read.
    logs = _in_window(daily_logs, windowed[-1][0], window_days) if windowed else []
    calories = [
        row["calories"]
        for _, row in logs
        if isinstance(row.get("calories"), (int, float))
    ]

    def unmet(state: str) -> AdaptiveTDEEStatus:
        return AdaptiveTDEEStatus(
            state=state,
            estimate=None,
            weigh_ins=len(windowed),
            span_days=span_days,
            logged_days=len(calories),
            window_days=window_days,
        )

    if len(windowed) < 2:
        return unmet(ADAPTIVE_NO_WEIGH_INS)
    if span_days < MIN_TREND_SPAN_DAYS:
        return unmet(ADAPTIVE_SHORT_SPAN)
    if not calories:
        return unmet(ADAPTIVE_NO_LOGS)

    mean_calories = sum(calories) / len(calories)

    # Days measured from the first weigh-in, so the regression's x-axis is
    # real elapsed time and an irregular weighing habit doesn't distort it.
    first_day = windowed[0][0]
    days = [float((when - first_day).days) for when, _ in windowed]
    weights = [float(row["weight_kg"]) for _, row in windowed]

    # Negated because the slope falls while weight is lost, and the formula's
    # delta is defined as weight *lost*.
    kg_lost_per_day = -_trend_slope_kg_per_day(days, weights)

    return AdaptiveTDEEStatus(
        state=ADAPTIVE_READY,
        estimate=round(mean_calories + kg_lost_per_day * KCAL_PER_KG_TISSUE, 1),
        weigh_ins=len(windowed),
        span_days=span_days,
        logged_days=len(calories),
        window_days=window_days,
    )


def calculate_adaptive_tdee(
    daily_logs: list,
    weigh_in_history: list,
    window_days: int = 14,
) -> Optional[float]:
    """`measure_adaptive_tdee`'s figure alone — see there for the arithmetic.

    Returns None whenever the inputs can't support an estimate — no logs, no
    dated weigh-ins, fewer than two of them, or too short a span. That is a
    "keep using the formula estimate" signal, not an error, and it is the
    contract every existing caller was written against.

    Which of those it was is deliberately not expressible in the return type,
    which is exactly why the status sibling exists: a surface that wants to
    tell a cold start from a three-day weigh-in span calls
    `measure_adaptive_tdee` instead, and one that only wants a number stays
    here.
    """
    return measure_adaptive_tdee(daily_logs, weigh_in_history, window_days).estimate


# ---------------------------------------------------------------------------
# Proposing the declared week from the observed one
# ---------------------------------------------------------------------------
#
# `config/schedule.json`'s `training_schedule` is what the planner acts on:
# `planner.apply_training_adjustments` expands a day's calorie budget from it
# and pins that day's post-workout meal, and `planner.morning_training_days`
# reads it to pin a breakfast shake. It has always been hand-declared, while
# the watch has been recording what actually happened all along.
#
# **The detector is the easy half; the confirmation is the feature.** Nothing
# here writes anything. A schedule inferred and applied silently would move
# every target on the day it guessed at, which is the one thing a derived
# number in this app is never allowed to do — the precedent is
# `estimate_session_burn_kcal` above, whose whole discipline is that it is a
# default a human applies with a click, into the same field a typed number
# lives in, so nothing downstream can tell the two apart.

# How far back a proposal looks. Four weeks is the shortest window that can
# see a weekday four times, which is what makes "3 of 4 Wednesdays" a
# sentence rather than a coin toss. Longer would keep proposing a routine
# that has already been abandoned.
TRAINING_PROPOSAL_WINDOW_DAYS = 28

# How many times a (weekday, session type) pair has to have been recorded
# before it is a routine rather than a one-off. Also the number of
# observations of a weekday required before its *silence* is evidence.
MIN_PROPOSAL_OCCURRENCES = 2

# How many distinct days in the window have to carry some recorded activity
# before a *drop* is proposed at all. A watch left in a drawer records
# nothing, which is indistinguishable from a fortnight of rest if you only
# look at one weekday — so the drop rule asks first whether this watch is
# being worn at all. Additions need no such guard: an activity that was
# recorded is evidence on its own.
MIN_ACTIVE_DAYS_FOR_DROP = 4

# Calendar order for the proposal list. Spelled out rather than derived from
# `%A` so the order is fixed regardless of locale, and Monday-first regardless
# of `week_start_day` — this module reads no config, and a list of suggestions
# has nothing on screen it needs to line up with.
WEEKDAY_ORDER = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

# What a proposal is asking for. `drop` never removes anything on its own —
# see the module note above.
PROPOSAL_ADD = "add"
PROPOSAL_DROP = "drop"

# Which precondition stopped a proposal, or `TRAINING_PROPOSAL_READY` when
# none did. `ready` with an empty list is a real and good answer — "the
# declared week already matches the recorded one" — and telling it apart from
# "no data yet" is the whole reason this is a state rather than a bare list.
# The same lesson `AdaptiveTDEEStatus` was written for.
TRAINING_PROPOSAL_READY = "ready"
TRAINING_PROPOSAL_NO_ACTIVITY = "no_activity"
TRAINING_PROPOSAL_SHORT_HISTORY = "short_history"


@dataclass(frozen=True)
class ProposedSession:
    """One accept/dismiss row: a session to add, or a declared one to drop.

    The five schedule fields are spelled exactly as a `training_schedule`
    entry spells them, so `session()` hands back something the config can
    hold verbatim — the same "derived and typed are the same field"
    discipline `estimate_session_burn_kcal` follows. `occurrences` and
    `observations` are the evidence behind it, in the units a person can
    argue with: seen 3 times, on a weekday that came round 4 times.

    A `drop` carries the *declared* session's own time, duration and burn
    rather than anything observed — there is nothing observed, which is the
    point — so that `session()` still names the exact row to remove.
    """

    kind: str
    day: str
    time: str
    type: str
    duration_minutes: int
    estimated_burn_kcal: float
    occurrences: int
    observations: int

    @property
    def key(self) -> str:
        """A stable identity for a proposal across repaints.

        Used by the UI to remember which proposals were dismissed this
        session. Deliberately excludes the counts: the same suggestion seen
        one more time is the same suggestion, and letting the evidence into
        the key would resurrect a dismissed row the next time the sync ran.
        """
        return f"{self.kind}:{self.day}:{self.time}:{self.type}"

    def session(self) -> dict:
        """This proposal as a `training_schedule` entry."""
        return {
            "day": self.day,
            "time": self.time,
            "type": self.type,
            "duration_minutes": self.duration_minutes,
            "estimated_burn_kcal": self.estimated_burn_kcal,
        }


@dataclass(frozen=True)
class TrainingScheduleProposal:
    """What `propose_training_schedule` answered, and what it looked at.

    `observed_days` is the span this actually had sight of — from the first
    recorded activity in the window through to the last date Garmin was asked
    about — not the window's own length. The two differ on every checkout
    whose sync is younger than four weeks, and reporting the window would
    claim evidence that was never gathered.

    `activity_days` is how many of those days carried any activity at all: it
    is the guard on drop proposals (`MIN_ACTIVE_DAYS_FOR_DROP`) and the one
    number that separates "you rested" from "the watch was in a drawer".
    """

    state: str
    proposals: List[ProposedSession]
    window_days: int
    observed_days: int
    activity_days: int
    observed_from: Optional[str] = None
    observed_to: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.state == TRAINING_PROPOSAL_READY

    @property
    def additions(self) -> List[ProposedSession]:
        return [p for p in self.proposals if p.kind == PROPOSAL_ADD]

    @property
    def drops(self) -> List[ProposedSession]:
        return [p for p in self.proposals if p.kind == PROPOSAL_DROP]


def _median(values: Sequence[float]) -> float:
    """The middle value, averaging the two middles for an even count.

    Median rather than mean throughout this section: one 90-minute Sunday
    ride among four 30-minute ones should not drag the proposed duration to
    45, and a single mis-tagged marathon should not propose a 900 kcal
    Tuesday. Written here rather than imported from `statistics` to keep this
    module's imports as they are — it is three lines, and the file already
    carries its own least-squares fit for the same reason.
    """
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2.0


def _round_to_five(minutes: float) -> int:
    """Minutes rounded to the nearest 5.

    A proposal reading "06:12, 33 minutes" claims a precision the median of
    four noisy starts does not have, and invites a pointless edit. Five
    minutes is the same granularity `ui_review`'s duration input already
    steps in.
    """
    return int(round(minutes / 5.0) * 5)


def _clock(minutes: int) -> str:
    """Minutes since midnight as "HH:MM", wrapped into the day."""
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _clock_minutes(value: str) -> Optional[int]:
    """"HH:MM" as minutes since midnight, or None if it isn't one.

    Tolerant like `_parse_iso_date`, and for the same reason: these strings
    come out of `biometrics.json` and out of a free-text config field, and
    one unreadable row must cost that row rather than the whole proposal.
    """
    try:
        hours, _, mins = str(value).partition(":")
        return int(hours) * 60 + int(mins)
    except (TypeError, ValueError):
        return None


def propose_training_schedule(
    activity_log: list,
    declared_schedule: list,
    today: date,
    weight_kg: Optional[float] = None,
    window_days: int = TRAINING_PROPOSAL_WINDOW_DAYS,
    checked_through: Optional[str] = None,
) -> TrainingScheduleProposal:
    """The recorded week, diffed against the declared one.

    Pure, like everything else here: it reads two lists and a date and
    returns proposals. Applying one is `ui_state.PlannerState.
    accept_training_proposal`'s job, and it happens on a click.

    **What counts as observed.** `activity_log` only ever holds a day that
    recorded something, so silence in it is ambiguous — a rest day and an
    unsynced day look identical, the same gap `sync_checkpoints` was added to
    close for weigh-ins. The observed span is therefore bounded: it starts at
    the first recorded activity inside the window and ends at the later of
    the last recorded activity and `checked_through` (Garmin's own
    checkpoint), capped at `today`. A stored row past the checkpoint still
    counts, because a row is proof the day was asked about — the identical
    rule `ui_state.sync_status` applies. Under-claiming at the start is the
    safe direction: it costs a proposal, where over-claiming would invent
    weeks of evidence that a session never happened.

    **Additions** need a (weekday, session type) pair recorded on at least
    `MIN_PROPOSAL_OCCURRENCES` distinct dates *and* on at least half the
    observations of that weekday, and no declared session of that type on
    that day already. Half rather than all, because a fortnight's illness
    should not erase a standing Tuesday.

    **Drops** are the answer to this feature's one open question, and they
    are deliberately symmetric with additions rather than either silent or
    absent: a declared session whose weekday came round at least
    `MIN_PROPOSAL_OCCURRENCES` times inside the observed span and carried no
    recorded activity *at all* is proposed for removal, and nothing removes
    it but a click. Two guards keep that honest — `MIN_ACTIVE_DAYS_FOR_DROP`
    asks whether the watch is being worn at all before reading its silence as
    evidence, and a weekday that recorded *something* is never proposed for a
    drop even when the modality disagrees, because a Sunday ride that has
    become a Sunday walk is a day you plainly train on. The walk arrives as
    its own addition instead.

    `weight_kg` is only a fallback: a proposal's `estimated_burn_kcal` is the
    median of what Garmin actually reported, discounted by
    `sync_service.EXERCISE_RECOVERY_FACTOR` at sync time, and the MET formula
    is used only when the watch reported no calories. That makes
    `net_calories` load-bearing for the first time, which is the whole reason
    it was worth storing.
    """
    window_start = today - timedelta(days=window_days - 1)
    windowed: List[Tuple[date, dict]] = []
    for row in activity_log or []:
        when = _parse_iso_date((row or {}).get("date", ""))
        if when is None or not (window_start <= when <= today):
            continue
        if not row.get("session_type") or _clock_minutes(row.get("start_time")) is None:
            # Rows `sync_service` would not have stored — a hand-edited file,
            # or one written by a future sync with a wider idea of what is
            # worth keeping. Skipped rather than guessed at, same as there.
            continue
        windowed.append((when, row))

    def unmet(state: str) -> TrainingScheduleProposal:
        return TrainingScheduleProposal(
            state=state,
            proposals=[],
            window_days=window_days,
            observed_days=0,
            activity_days=len({when for when, _ in windowed}),
        )

    if not windowed:
        return unmet(TRAINING_PROPOSAL_NO_ACTIVITY)

    recorded_dates = {when for when, _ in windowed}
    observed_from = min(recorded_dates)
    last_checked = _parse_iso_date(checked_through or "")
    observed_to = min(max([max(recorded_dates)] + ([last_checked] if last_checked else [])), today)
    observed = [
        observed_from + timedelta(days=offset)
        for offset in range((observed_to - observed_from).days + 1)
    ]

    observations: Dict[str, int] = {}
    for when in observed:
        name = when.strftime("%A")
        observations[name] = observations.get(name, 0) + 1

    if max(observations.values(), default=0) < MIN_PROPOSAL_OCCURRENCES:
        return TrainingScheduleProposal(
            state=TRAINING_PROPOSAL_SHORT_HISTORY,
            proposals=[],
            window_days=window_days,
            observed_days=len(observed),
            activity_days=len(recorded_dates),
            observed_from=observed_from.isoformat(),
            observed_to=observed_to.isoformat(),
        )

    # One row per (weekday, type, date): two runs on one Wednesday are one
    # Wednesday run habit, and counting them twice would let a single busy
    # week clear a threshold four calm ones did not. The earliest start wins,
    # since that is the session the rest of the day was planned around.
    by_pattern: Dict[Tuple[str, str], Dict[date, dict]] = {}
    trained_days: Dict[str, set] = {}
    for when, row in windowed:
        name = when.strftime("%A")
        pattern = (name, str(row["session_type"]))
        seen = by_pattern.setdefault(pattern, {})
        current = seen.get(when)
        if current is None or _clock_minutes(row["start_time"]) < _clock_minutes(
            current["start_time"]
        ):
            seen[when] = row
        trained_days.setdefault(name, set()).add(when)

    declared = [
        session
        for session in (declared_schedule or [])
        if session.get("type") != "rest"
        and float(session.get("estimated_burn_kcal", 0) or 0) > 0
    ]
    declared_patterns = {
        (str(session.get("day")), str(session.get("type"))) for session in declared
    }

    proposals: List[ProposedSession] = []
    for (day, session_type), rows in by_pattern.items():
        occurrences = len(rows)
        seen_on = observations.get(day, 0)
        if occurrences < MIN_PROPOSAL_OCCURRENCES or occurrences * 2 < seen_on:
            continue
        if (day, session_type) in declared_patterns:
            continue

        sessions = list(rows.values())
        duration = _round_to_five(_median([row["duration_min"] for row in sessions]))
        net = _median([float(row.get("net_calories") or 0.0) for row in sessions])
        if net <= 0:
            net = (
                estimate_session_burn_kcal(session_type, duration, weight_kg)
                if weight_kg
                else 0.0
            )
        proposals.append(
            ProposedSession(
                kind=PROPOSAL_ADD,
                day=day,
                time=_clock(
                    _round_to_five(_median([_clock_minutes(r["start_time"]) for r in sessions]))
                ),
                type=session_type,
                duration_minutes=duration,
                estimated_burn_kcal=round(net),
                occurrences=occurrences,
                observations=seen_on,
            )
        )

    if len(recorded_dates) >= MIN_ACTIVE_DAYS_FOR_DROP:
        for session in declared:
            day = str(session.get("day"))
            seen_on = observations.get(day, 0)
            if seen_on < MIN_PROPOSAL_OCCURRENCES or trained_days.get(day):
                continue
            proposals.append(
                ProposedSession(
                    kind=PROPOSAL_DROP,
                    day=day,
                    time=str(session.get("time", "")),
                    type=str(session.get("type", "")),
                    duration_minutes=int(session.get("duration_minutes") or 0),
                    estimated_burn_kcal=float(session.get("estimated_burn_kcal") or 0),
                    occurrences=0,
                    observations=seen_on,
                )
            )

    return TrainingScheduleProposal(
        state=TRAINING_PROPOSAL_READY,
        # Calendar order, not the order the evidence happened to be walked
        # in: this list is read top to bottom by a human deciding what their
        # week is, and grouping by weekday is how a week is read. Monday-first
        # rather than `week_start_day`'s rotation, because this module takes
        # no config — and a proposal list is not a grid, so nothing has to
        # line up with one.
        proposals=sorted(
            proposals,
            key=lambda session: (
                WEEKDAY_ORDER.index(session.day) if session.day in WEEKDAY_ORDER else len(WEEKDAY_ORDER),
                _clock_minutes(session.time) or 0,
                session.kind,
            ),
        ),
        window_days=window_days,
        observed_days=len(observed),
        activity_days=len(recorded_dates),
        observed_from=observed_from.isoformat(),
        observed_to=observed_to.isoformat(),
    )


# --------------------------------------------------------------------------
# The recorded week, read one day at a time
#
# `propose_training_schedule` above asks a four-week question — "what does
# your week look like" — and answers it as a schedule to accept. This asks
# the one-day question underneath it: *did today's declared sessions
# happen?* Same two inputs, same vocabulary (`GARMIN_SESSION_TYPES`' output
# is what both a stored row and a declared session are spelled in), so it
# lives here rather than growing a second module that knows how an activity
# row maps onto a declared one.
#
# It stores nothing and marks nothing. A session the watch never recorded is
# reported as unrecorded, and whether that means "didn't happen" or "the
# watch was flat" is exactly what the manual mark in `data/adherence.json`
# answers — see CLAUDE.md's "Whether the plan actually happened".
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionMatch:
    """One declared session for one date, and the activity that answers it.

    `recorded` is the only load-bearing field; the three `recorded_*` ones
    are the evidence a person can check it against, in the units the watch
    reported them in — the same "show the evidence, not just the verdict"
    shape `ProposedSession.occurrences`/`observations` takes.

    `recorded_kcal` is the **net** figure, already discounted by
    `sync_service.EXERCISE_RECOVERY_FACTOR` at sync time, because that is the
    number this app is willing to put on a day. Reporting the gross one
    beside a budget computed from the net would be two numbers for one
    session with nothing on screen saying which was which.
    """

    time: str
    session_type: str
    recorded: bool
    recorded_start: Optional[str] = None
    recorded_minutes: Optional[float] = None
    recorded_kcal: Optional[float] = None

    @property
    def session_id(self) -> str:
        """`planner.workout_session_id`'s spelling, without importing it.

        `nutrition_engine` imports nothing from `planner` — that is the rule
        the module docstring states and the reason it is testable without an
        event loop or an API key — so the format lives in two places by
        necessity. `tests/test_adherence.py` pins them equal, which is the
        cheap half of the trade: a drift here files a manual mark under a key
        the button that wrote it would never read back, and nothing else in
        the app would notice.
        """
        return f"{self.time}:{self.session_type}"


def match_recorded_sessions(
    activity_log: list,
    declared_sessions: list,
    on_date: str,
) -> List[SessionMatch]:
    """Each declared session for `on_date`, and whether the watch saw it.

    Pure, like everything else here: two lists and a date in, a list out.

    **Matched on `session_type`, then paired by closest start time.** A day
    with one gym session and one walk is unambiguous, but a day declaring two
    sessions of the same type is not, and matching on type alone would let
    one recorded lift answer for both. Each declared session claims the
    nearest unclaimed recording of its type, so two declared lifts against
    one recorded one leave the second honestly unrecorded rather than
    silently confirmed.

    Time is used to *choose between* candidates and never to reject one: a
    06:30 gym session started at 07:10 is the same session, and a matcher
    with a tolerance window would have to pick a number that is wrong for
    somebody. The type and the date are the claim; the clock only breaks
    ties.

    Rest entries are the caller's to filter — this module has no opinion on
    what `training_schedule` means by "rest", and `TrainingView.is_rest`
    already folds a typed rest and a zero-burn session together for the one
    caller that needs it.

    An activity row with no `session_type` (a modality `GARMIN_SESSION_TYPES`
    has never heard of) can answer nothing, and is skipped exactly as
    `propose_training_schedule` skips it — for the same reason: a yoga class
    is not evidence that the declared lift happened.
    """
    recorded = [
        row
        for row in (activity_log or [])
        if isinstance(row, dict)
        and str(row.get("date") or "")[:10] == on_date
        and row.get("session_type")
    ]

    claimed: set = set()
    matches: List[SessionMatch] = []
    for session in declared_sessions or []:
        session_type = str(session.get("type") or "")
        time = str(session.get("time") or "")
        wanted = _clock_minutes(time)
        candidates = [
            (index, row)
            for index, row in enumerate(recorded)
            if index not in claimed and row.get("session_type") == session_type
        ]
        if not candidates:
            matches.append(
                SessionMatch(time=time, session_type=session_type, recorded=False)
            )
            continue

        # Nearest start time wins. An unreadable clock on either side sorts
        # last rather than raising — it is still a recording of the right
        # type on the right day, so it is a worse match, not a non-match.
        def distance(pair: Tuple[int, dict]) -> Tuple[int, int]:
            started = _clock_minutes(str(pair[1].get("start_time") or ""))
            if wanted is None or started is None:
                return (1, pair[0])
            return (0, abs(started - wanted))

        index, row = min(candidates, key=distance)
        claimed.add(index)
        matches.append(
            SessionMatch(
                time=time,
                session_type=session_type,
                recorded=True,
                recorded_start=row.get("start_time"),
                recorded_minutes=row.get("duration_min"),
                recorded_kcal=row.get("net_calories"),
            )
        )
    return matches

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

from datetime import date, datetime, timedelta
from typing import List, Optional, Sequence, Tuple

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

    weight_kg = biometrics.get("weight_kg") or profile.get("current_weight_kg")
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


def calculate_adaptive_tdee(
    daily_logs: list,
    weigh_in_history: list,
    window_days: int = 14,
) -> Optional[float]:
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
    - **A span under `MIN_TREND_SPAN_DAYS` returns None.** Multiplying a
      two-day wobble by 7700 is noise amplification, not measurement.

    Returns None whenever the inputs can't support an estimate — no logs, no
    dated weigh-ins, fewer than two of them, or too short a span. That is a
    "keep using the formula estimate" signal, not an error.

    The result is returned unclamped. Systematic under-logging (the common
    case) depresses it, and a caller wiring this into a live target should
    sanity-check it against `calculate_tdee` rather than trusting it blind;
    clamping here would hide bad data inside a plausible-looking number.
    """
    weigh_ins = [row for row in (weigh_in_history or []) if (row or {}).get("weight_kg")]
    if len(weigh_ins) < 2:
        return None

    # Anchored on the most recent weigh-in rather than on today, so a series
    # that stops a month before it is read still yields the trend it actually
    # recorded instead of an empty window.
    dates = [d for d in (_parse_iso_date(row.get("date", "")) for row in weigh_ins) if d]
    if not dates:
        return None

    windowed = _in_window(weigh_ins, max(dates), window_days)
    if len(windowed) < 2:
        return None

    span_days = (windowed[-1][0] - windowed[0][0]).days
    if span_days < MIN_TREND_SPAN_DAYS:
        return None

    logs = _in_window(daily_logs, windowed[-1][0], window_days)
    calories = [
        row["calories"]
        for _, row in logs
        if isinstance(row.get("calories"), (int, float))
    ]
    if not calories:
        return None
    mean_calories = sum(calories) / len(calories)

    # Days measured from the first weigh-in, so the regression's x-axis is
    # real elapsed time and an irregular weighing habit doesn't distort it.
    first_day = windowed[0][0]
    days = [float((when - first_day).days) for when, _ in windowed]
    weights = [float(row["weight_kg"]) for _, row in windowed]

    # Negated because the slope falls while weight is lost, and the formula's
    # delta is defined as weight *lost*.
    kg_lost_per_day = -_trend_slope_kg_per_day(days, weights)

    return round(mean_calories + kg_lost_per_day * KCAL_PER_KG_TISSUE, 1)

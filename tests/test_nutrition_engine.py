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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import nutrition_engine as ne  # noqa: E402  (path setup must precede the import)


# The profile the app actually ships in config/profile.json, at the top of the
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


class TestAgeFromBirthDate(unittest.TestCase):
    def test_age_on_reference_date(self):
        # Any date on/after the Jan-10 birthday in 2026 gives age 55; picked
        # arbitrarily rather than reusing the real calendar date, since
        # `on_date` is passed explicitly and the test asserts nothing about
        # when it's run.
        reference_date = date(2026, 1, 15)
        self.assertEqual(ne.age_from_birth_date("1971-01-10", reference_date), 55)

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

    def test_unknown_body_fat_reproduces_the_weight_only_ramp(self):
        """No body_fat_pct must behave exactly as before the Alpert cap existed."""
        for weight in (100.0, 90.0, 82.0, 74.0):
            self.assertAlmostEqual(
                ne.calculate_dynamic_deficit(weight, 80.0),
                ne.calculate_dynamic_deficit(weight, 80.0, body_fat_pct=None),
            )

    def test_alpert_cap_binds_for_a_lean_heavy_body(self):
        """8% body fat at 100kg: fat mass alone can't fund the full 750kcal ramp."""
        # fat_mass = 100 * 0.08 = 8kg; ceiling = 8 * 69.3 * 0.80 = 443.52
        deficit = ne.calculate_dynamic_deficit(100.0, 80.0, body_fat_pct=8.0)
        self.assertAlmostEqual(deficit, 443.52)
        self.assertLess(deficit, 750.0)  # would otherwise be the full ramp

    def test_alpert_cap_never_raises_the_deficit(self):
        """Generous body fat must leave the weight ramp untouched, not increase it."""
        # fat_mass = 100 * 0.35 = 35kg; ceiling = 35 * 69.3 * 0.80 = 1940.4,
        # well above the 750kcal ramp ceiling.
        deficit = ne.calculate_dynamic_deficit(100.0, 80.0, body_fat_pct=35.0)
        self.assertAlmostEqual(deficit, 750.0)


class TestAlpertFatEnergyCeiling(unittest.TestCase):
    def test_none_when_body_fat_unknown(self):
        self.assertIsNone(ne.alpert_fat_energy_ceiling_kcal(100.0, None))

    def test_scales_with_fat_mass_not_total_weight(self):
        # Same fat mass (20kg) at two different body weights must agree.
        a = ne.alpert_fat_energy_ceiling_kcal(100.0, 20.0)
        b = ne.alpert_fat_energy_ceiling_kcal(80.0, 25.0)
        self.assertAlmostEqual(a, b)

    def test_matches_the_published_alpert_constant(self):
        # 10kg fat mass * 69.3 kcal/kg * 0.80 safety factor = 554.4
        self.assertAlmostEqual(
            ne.alpert_fat_energy_ceiling_kcal(50.0, 20.0), 554.4
        )


class TestDeriveFatG(unittest.TestCase):
    def test_fat_spends_the_remaining_energy(self):
        # 2000 - (150*4 + 60*4) = 2000 - 840 = 1160; /9
        self.assertAlmostEqual(ne.derive_fat_g(2000, 150, 60), 1160 / 9)

    def test_macros_reconcile_back_to_the_calorie_figure(self):
        fat = ne.derive_fat_g(1826, 144, 60)
        self.assertAlmostEqual(144 * 4 + 60 * 4 + fat * 9, 1826)

    def test_floored_at_zero_when_protein_and_carbs_overspend(self):
        self.assertEqual(ne.derive_fat_g(500, 150, 60), 0.0)


class _FixedToday(date):
    """`date` with `.today()` pinned.

    `calculate_macro_targets` resolves age via `age_from_birth_date(birth_date)`
    with no reference date, so it falls back to the real `date.today()`. Left
    unpatched, every expected number below that assumes age 55 (BMR, TDEE,
    ...) is only correct for real-world dates in the 55th year after the
    profile's 1971-01-10 birth date — this class quietly starts failing the
    day that year ends, with no code change to explain why. Subclassing
    (rather than replacing `date` outright) keeps every other use of `date` in
    `nutrition_engine` — the `date(...)` constructor, `_parse_iso_date` — working
    unchanged.
    """

    @classmethod
    def today(cls):
        return cls(2026, 8, 16)


class TestMacroTargets(unittest.TestCase):
    """The headline case: 55 years old, 100 kg, aiming at 80 kg."""

    def setUp(self):
        patcher = mock.patch("nutrition_engine.date", _FixedToday)
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def test_alpert_ceiling_is_none_without_a_body_fat_reading(self):
        self.assertIsNone(self.result["basis"]["alpert_ceiling_kcal"])

    def test_alpert_ceiling_reported_and_applied_when_body_fat_known(self):
        # 100kg * 8% = 8kg fat mass; ceiling = 8 * 69.3 * 0.80 = 443.52
        result = ne.calculate_macro_targets(
            PROFILE, {"weight_kg": 100.0, "body_fat_pct": 8.0}
        )
        self.assertAlmostEqual(result["basis"]["alpert_ceiling_kcal"], 443.5)
        self.assertAlmostEqual(result["basis"]["deficit_kcal"], 443.5)

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


class TestReconcileAdaptiveTdee(unittest.TestCase):
    """The caller-side sanity check `calculate_adaptive_tdee` asks for.

    That function returns its figure deliberately unclamped, so bad data stays
    visible rather than hiding inside a plausible-looking number. This is
    where the judgement about whether to believe it lives.
    """

    FORMULA = 2600.0

    def test_no_measurement_leaves_the_formula_alone(self):
        self.assertEqual(
            ne.reconcile_adaptive_tdee(self.FORMULA, None),
            (self.FORMULA, "formula"),
        )

    def test_a_close_measurement_is_believed(self):
        value, source = ne.reconcile_adaptive_tdee(self.FORMULA, 2800.0)
        self.assertEqual((value, source), (2800.0, "adaptive"))

    def test_a_measurement_far_below_is_rejected(self):
        """Under-logging is the common case and it always reads low."""
        value, source = ne.reconcile_adaptive_tdee(self.FORMULA, 1500.0)
        self.assertEqual((value, source), (self.FORMULA, "formula_adaptive_rejected"))

    def test_a_measurement_far_above_is_rejected(self):
        value, source = ne.reconcile_adaptive_tdee(self.FORMULA, 4000.0)
        self.assertEqual((value, source), (self.FORMULA, "formula_adaptive_rejected"))

    def test_the_band_is_symmetric_and_inclusive(self):
        tolerance = ne.ADAPTIVE_TDEE_TOLERANCE
        for edge in (self.FORMULA * (1 - tolerance), self.FORMULA * (1 + tolerance)):
            with self.subTest(edge=edge):
                self.assertEqual(
                    ne.reconcile_adaptive_tdee(self.FORMULA, edge)[1],
                    "adaptive",
                )

    def test_rejection_is_distinguishable_from_having_no_data(self):
        """Two different states: "we measured and disbelieved it" is worth
        investigating, "we had nothing to measure" is the normal early state."""
        self.assertNotEqual(
            ne.reconcile_adaptive_tdee(self.FORMULA, None)[1],
            ne.reconcile_adaptive_tdee(self.FORMULA, 100.0)[1],
        )

    def test_it_chooses_rather_than_blends(self):
        """A weighted average of a good estimate and a bad one is a slightly
        bad estimate with no way to tell which it was."""
        value, _ = ne.reconcile_adaptive_tdee(self.FORMULA, 2700.0)
        self.assertEqual(value, 2700.0)


class TestAdaptiveTdeeInMacroTargets(unittest.TestCase):
    PROFILE = {
        "birth_date": "1971-01-10",
        "height_cm": 183,
        "gender": "male",
        "target_weight_kg": 80.0,
        "activity_level": "light_office",
    }
    WEIGH_IN = {"weight_kg": 98.4, "body_fat_pct": 27.5}

    def targets(self, adaptive=None):
        return ne.calculate_macro_targets(
            self.PROFILE, self.WEIGH_IN, adaptive_tdee=adaptive
        )

    def test_the_default_is_the_formula_unchanged(self):
        basis = self.targets()["basis"]
        self.assertEqual(basis["tdee_source"], "formula")
        self.assertEqual(basis["tdee"], basis["tdee_formula"])
        self.assertIsNone(basis["tdee_adaptive"])

    def test_a_believed_measurement_moves_the_calorie_target(self):
        formula = self.targets()
        adaptive = self.targets(formula["basis"]["tdee_formula"] * 1.1)
        self.assertEqual(adaptive["basis"]["tdee_source"], "adaptive")
        self.assertGreater(adaptive["calories"], formula["calories"])

    def test_protein_does_not_move_with_it(self):
        """Locked to target weight. A measurement buys back energy, not
        protein — the whole point of the lock is that it holds while the
        numbers around it change."""
        formula = self.targets()
        adaptive = self.targets(formula["basis"]["tdee_formula"] * 1.1)
        self.assertEqual(adaptive["protein_g"], formula["protein_g"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

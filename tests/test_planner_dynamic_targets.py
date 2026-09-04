"""Tests for the biometric-driven half of `src/planner.py`.

Covers the three pieces that turn a measured body into a prompt:
`hydrate_dynamic_targets` (the day's macros come from the scale, not the file),
`apply_protein_floor` (the day's locked protein reaches every meal), and
`logged_intake_for` (today's Cronometer row beats the plan). Also covers
`resolve_week_blocks` and the two feeds it makes possible — `dev/task-queue-
modified.md`'s 3.1b, mid-week block resolution into `hydrate_dynamic_targets`.

All three original pieces are pure functions of their arguments — no
repository, no event loop, no API — which is the point of `hydrate_config`
being the only async wrapper around them. `unittest` and the `sys.path`
insert match `test_nutrition_engine.py`; see its docstring for why.
"""

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402  (path setup must precede the import)
from week import MODE_COOK, MODE_LEFTOVER, SlotSpec  # noqa: E402

# config/profile.json's own user_profile. Every expected number below is worked
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


class TestHydrationCallsEngineOnce(unittest.TestCase):
    """BMR/TDEE/deficit/protein depend on the body, never the day, so the
    engine must run once per hydration rather than once per weekday. Added
    after an audit found the call hoisted out of the loop by hand but with
    nothing pinning the invariant — a future per-day input (a training-day
    activity factor, say) could silently reintroduce the per-day call with no
    test catching it."""

    def test_seven_day_schedule_calls_the_engine_once(self):
        schedule = {
            day: {"calories": 2000, "protein_g": 140, "net_carbs_g": carbs, "fat_g": 60}
            for day, carbs in zip(
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                [130, 130, 60, 130, 60, 130, 60],
            )
        }
        config = config_with(weekly_schedule=schedule)
        with mock.patch(
            "planner.calculate_macro_targets", wraps=planner.calculate_macro_targets
        ) as spy:
            hydrated = planner.hydrate_dynamic_targets(config, WEIGH_IN)
        self.assertEqual(spy.call_count, 1)
        # Each day's own net_carbs_g still reaches fat_g despite the shared call.
        self.assertNotEqual(
            hydrated["weekly_schedule"]["Monday"]["fat_g"],
            hydrated["weekly_schedule"]["Wednesday"]["fat_g"],
        )


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
            fallen_back = planner.hydrate_dynamic_targets(config, None)
        for day, entry in config["weekly_schedule"].items():
            for key in planner.MACRO_KEYS:
                if key in entry:
                    self.assertEqual(fallen_back["weekly_schedule"][day][key], entry[key])

    def test_the_fallback_still_resolves_a_fibre_target(self):
        """The one figure a failed engine call does not cost, because it never
        needed the body: `calculate_fiber_target_g` wants the day's calories
        and a floor. Returning the config untouched here would have
        `/api/targets` omitting fibre on a machine with no weigh-in while the
        telemetry header printed one — the "one number, one call" rule at the
        end of CLAUDE.md, reached from the storage side."""
        config = config_with()
        with self.assertLogs("meals", level="WARNING"):
            fallen_back = planner.hydrate_dynamic_targets(config, None)
        for entry in fallen_back["weekly_schedule"].values():
            self.assertEqual(entry["fiber_g"], 30.0)

    def test_the_fallback_is_announced(self):
        """One note, not one per day: every day fails identically, because they
        differ only in the carb figure the failure never reaches."""
        notes = []
        with self.assertLogs("meals", level="WARNING"):
            planner.hydrate_dynamic_targets(config_with(), None, notes.append)
        self.assertEqual(len(notes), 1)
        self.assertIn("config.json targets", notes[0])

    def test_an_unfilled_profile_leaves_every_macro_alone(self):
        """Fibre is the exception and says why in
        `test_the_fallback_still_resolves_a_fibre_target` above: it is derived
        from the day rather than from the body, so an empty `user_profile`
        does not stop it resolving."""
        config = config_with(
            user_profile={"protein_multiplier": 1.8, "activity_level": "light_office"}
        )
        hydrated = planner.hydrate_dynamic_targets(config, WEIGH_IN)
        for day, entry in config["weekly_schedule"].items():
            for key in planner.MACRO_KEYS:
                if key in entry:
                    self.assertEqual(hydrated["weekly_schedule"][day][key], entry[key])
            self.assertEqual(hydrated["weekly_schedule"][day]["fiber_g"], 30.0)


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


class TestDietStyleCeilingCapsTheDay(unittest.TestCase):
    """The one numeric lever a diet style has, applied where the day's
    calories are already decided rather than as a knob beside them.

    The reading half — which ceiling wins, and when there is none — is in
    `test_diet_styles.py`. These pin what hydration does with the number, and
    the two properties that made a ceiling admissible at all where an
    *adjustment* was refused: it is idempotent across the two hydration
    passes, and it never overrides a target somebody stated.
    """

    def config(self, ceiling=800, active=("fast_800",), **overrides) -> dict:
        return config_with(
            diet_styles={
                "fast_800": {
                    "label": "Fast 800",
                    "principles": "Simple, lean, low-added-fat.",
                    "calorie_ceiling": ceiling,
                },
            },
            dietary_rules={"active_diet_styles": list(active)},
            **overrides,
        )

    def test_a_computed_day_is_capped(self):
        hydrated = planner.hydrate_dynamic_targets(self.config(ceiling=1600), WEIGH_IN)
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], 1600)

    def test_a_day_already_under_the_ceiling_is_untouched(self):
        """`min()`, not an assignment: a ceiling of 2400 sits above the
        engine's 1910 and must leave every figure exactly where it was."""
        hydrated = planner.hydrate_dynamic_targets(self.config(ceiling=2400), WEIGH_IN)
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL)

    def test_no_active_style_leaves_the_day_alone(self):
        hydrated = planner.hydrate_dynamic_targets(self.config(active=()), WEIGH_IN)
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL)

    def test_hydrating_twice_lands_on_the_same_number(self):
        """The property that made this admissible at all. The UI hydrates for
        its own live preview and generation hydrates that same config again;
        anything that *shifts* a figure shifts it twice, which is exactly how
        an earlier uplift-unwinding pass took a 2200 kcal override to 1850.
        """
        once = planner.hydrate_dynamic_targets(self.config(ceiling=1600), WEIGH_IN)
        twice = planner.hydrate_dynamic_targets(once, WEIGH_IN)
        self.assertEqual(
            twice["weekly_schedule"]["Monday"]["calories"],
            once["weekly_schedule"]["Monday"]["calories"],
        )
        self.assertEqual(twice["weekly_schedule"], once["weekly_schedule"])

    def test_a_stated_target_is_not_capped(self):
        """A stated figure is the day's *final* number, whichever route stated
        it. A ceiling that overrode one would be the second source of truth
        this whole section refuses — and would make flipping calories to
        manual silently move the day, which is the exact bug `target_modes`
        was introduced to fix."""
        hydrated = planner.hydrate_dynamic_targets(
            self.config(ceiling=800, target_modes={"calories": "manual"}), WEIGH_IN
        )
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], 1500)

    def test_a_workout_does_not_buy_an_exemption(self):
        """Capped *after* the uplift is replayed: the ceiling bounds what the
        day may total, not the base it was built from. 1910 + 350 = 2260,
        capped to 1600."""
        adjusted = planner.apply_training_adjustments(
            self.config(
                ceiling=1600,
                meal_types=["breakfast", "lunch", "dinner", "snack"],
                meal_weights={"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
                training_schedule=[{
                    "day": "Monday", "time": "18:00", "type": "gym_hypertrophy",
                    "estimated_burn_kcal": 350,
                }],
            )
        )
        hydrated = planner.hydrate_dynamic_targets(adjusted, WEIGH_IN)
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], 1600)

    def test_a_ceiling_below_the_locked_macros_reports_rather_than_hides_it(self):
        """Protein is locked to the *target* weight (144 g = 576 kcal) and
        carbs come straight off `weekly_schedule` (130 g = 520 kcal), so an
        800 kcal ceiling cannot pay for both. `derive_fat_g` floors at 0 and
        the day stops reconciling against 4p + 4c + 9f — surfaced as a note,
        never corrected into a number nobody chose, which is the same answer
        `split_targets` gives an overspent `meal_overrides`.
        """
        notes = []
        hydrated = planner.hydrate_dynamic_targets(
            self.config(ceiling=800), WEIGH_IN, notes.append
        )
        monday = hydrated["weekly_schedule"]["Monday"]
        self.assertEqual(monday["calories"], 800)
        self.assertEqual(monday["protein_g"], LOCKED_PROTEIN_G)
        self.assertEqual(monday["fat_g"], 0.0)
        self.assertTrue(any("cannot fit locked protein" in note for note in notes))

    def test_an_affordable_cap_notes_only_the_cap(self):
        notes = []
        planner.hydrate_dynamic_targets(self.config(ceiling=1600), WEIGH_IN, notes.append)
        self.assertTrue(any("diet-style ceiling" in note for note in notes))
        self.assertFalse(any("cannot fit locked protein" in note for note in notes))


class TestADayScopedCeilingCapsOnlyItsDays(unittest.TestCase):
    """"Fast 800 for four days" — the brief's own example, and the reason the
    ceiling lookup takes a day.

    Every property that made a ceiling admissible where an *adjustment* was
    refused has to survive the scoping, so each is re-asserted here against a
    window rather than against a whole week: idempotent across the two
    hydration passes, applied after the training uplift, never over a stated
    target. The change is to *which days* the ceiling is looked up for, not
    to what hydration does with the number.
    """

    WINDOW = ["Monday", "Tuesday", "Wednesday", "Thursday"]
    OUTSIDE = ["Friday", "Saturday", "Sunday"]

    def config(self, active, **overrides) -> dict:
        schedule = {
            day: {"calories": 1500, "protein_g": 120, "net_carbs_g": 130, "fat_g": 55}
            for day in self.WINDOW + self.OUTSIDE
        }
        return config_with(
            weekly_schedule=schedule,
            diet_styles={
                "fast_800": {
                    "label": "Fast 800",
                    "principles": "Simple, lean, low-added-fat.",
                    "calorie_ceiling": 1600,
                },
            },
            dietary_rules={"active_diet_styles": active},
            **overrides,
        )

    def test_four_days_are_capped_and_three_are_not(self):
        hydrated = planner.hydrate_dynamic_targets(
            self.config([{"style": "fast_800", "days": self.WINDOW}]), WEIGH_IN
        )["weekly_schedule"]
        for day in self.WINDOW:
            self.assertEqual(hydrated[day]["calories"], 1600, day)
        for day in self.OUTSIDE:
            self.assertEqual(hydrated[day]["calories"], DYNAMIC_KCAL, day)

    def test_the_note_names_the_days_rather_than_only_counting_them(self):
        """A day-scoped cap makes "4 day(s)" an incomplete answer: which four
        is the whole question the window was written to settle."""
        notes = []
        planner.hydrate_dynamic_targets(
            self.config([{"style": "fast_800", "days": self.WINDOW}]),
            WEIGH_IN,
            notes.append,
        )
        capped = [note for note in notes if "diet-style ceiling" in note]
        self.assertEqual(len(capped), 1)
        self.assertIn("1600 kcal on Monday, Tuesday, Wednesday, Thursday", capped[0])

    def test_the_capped_days_stay_capped_across_two_hydration_passes(self):
        """The UI hydrates for its own live preview and generation hydrates
        that same config again. `min()` on an already-capped day returns the
        same figure — and an uncapped Friday must not acquire a cap on the
        second pass either, which is the half a per-day lookup could newly
        get wrong."""
        config = self.config([{"style": "fast_800", "days": self.WINDOW}])
        once = planner.hydrate_dynamic_targets(config, WEIGH_IN)
        twice = planner.hydrate_dynamic_targets(once, WEIGH_IN)
        self.assertEqual(twice["weekly_schedule"], once["weekly_schedule"])

    def test_a_training_day_inside_the_window_is_capped_after_its_uplift(self):
        """1910 + 350 = 2260, capped to 1600. A workout does not buy an
        exemption from a bound its owner chose to eat inside — and a workout
        on a day *outside* the window keeps every kcal of it."""
        adjusted = planner.apply_training_adjustments(
            self.config(
                [{"style": "fast_800", "days": self.WINDOW}],
                meal_types=["breakfast", "lunch", "dinner", "snack"],
                meal_weights={"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
                training_schedule=[
                    {"day": "Monday", "time": "18:00", "type": "gym_hypertrophy",
                     "estimated_burn_kcal": 350},
                    {"day": "Friday", "time": "18:00", "type": "gym_hypertrophy",
                     "estimated_burn_kcal": 350},
                ],
            )
        )
        hydrated = planner.hydrate_dynamic_targets(adjusted, WEIGH_IN)["weekly_schedule"]
        self.assertEqual(hydrated["Monday"]["calories"], 1600)
        self.assertEqual(hydrated["Friday"]["calories"], DYNAMIC_KCAL + 350)

    def test_a_stated_target_inside_the_window_is_not_capped(self):
        """A stated figure is the day's final number by definition, window or
        no window — `target_locks` scopes to one day, which is exactly the
        collision a day-scoped ceiling could get wrong."""
        hydrated = planner.hydrate_dynamic_targets(
            self.config(
                [{"style": "fast_800", "days": self.WINDOW}],
                target_locks={"Monday": ["calories"]},
            ),
            WEIGH_IN,
        )["weekly_schedule"]
        self.assertEqual(hydrated["Monday"]["calories"], 1500)
        self.assertEqual(hydrated["Tuesday"]["calories"], 1600)

    def test_two_windows_with_different_ceilings_report_both(self):
        """Two styles whose windows differ put two ceilings on one week, and
        a message stating a single figure could only ever be right about part
        of it — which is why the caller carries (day, ceiling) pairs."""
        config = self.config(
            [
                {"style": "fast_800", "days": ["Monday"]},
                {"style": "light", "days": ["Friday"]},
            ]
        )
        config["diet_styles"]["light"] = {
            "label": "Light", "principles": "Lighter.", "calorie_ceiling": 1800,
        }
        notes = []
        hydrated = planner.hydrate_dynamic_targets(config, WEIGH_IN, notes.append)
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], 1600)
        self.assertEqual(hydrated["weekly_schedule"]["Friday"]["calories"], 1800)
        capped = next(note for note in notes if "diet-style ceiling" in note)
        self.assertIn("1600 kcal on Monday", capped)
        self.assertIn("1800 kcal on Friday", capped)

    def test_a_flat_list_still_caps_every_day(self):
        """The compatibility claim, on the hydration side: a bare name means
        every day and the week is byte-identical to before day-scoping."""
        hydrated = planner.hydrate_dynamic_targets(
            self.config(["fast_800"]), WEIGH_IN
        )["weekly_schedule"]
        scoped = planner.hydrate_dynamic_targets(
            self.config([{"style": "fast_800", "days": self.WINDOW + self.OUTSIDE}]),
            WEIGH_IN,
        )["weekly_schedule"]
        self.assertEqual(hydrated, scoped)
        for day in self.WINDOW + self.OUTSIDE:
            self.assertEqual(hydrated[day]["calories"], 1600, day)


def make_block(**overrides) -> dict:
    """A minimal valid block — `src/blocks.py`'s fixed field list, mirroring
    `test_blocks.py`'s own helper rather than importing it, so this file's
    fixtures do not reach across test modules for something this small.

    The default span, 2026-09-07 (Monday) through 2026-09-10 (Thursday), is
    deliberately the same four days `TestADayScopedCeilingCapsOnlyItsDays`
    above exercises via `dietary_rules.active_diet_styles` directly — "the
    block" and "the window" name the same four days throughout the classes
    below, once resolved against `BLOCK_TODAY`.
    """
    fields = dict(
        name="fast-800-kickstart",
        starts_on="2026-09-07",
        ends_on="2026-09-10",
        body_goal="lose 8 kg",
        fitness_goal="maintain",
    )
    fields.update(overrides)
    return fields


# Any date from 2026-09-07 through 2026-09-13 anchors the same calendar week;
# a day squarely inside the block's own span keeps the arithmetic obvious.
BLOCK_TODAY = date(2026, 9, 9)
BLOCK_WINDOW = ["Monday", "Tuesday", "Wednesday", "Thursday"]
BLOCK_OUTSIDE = ["Friday", "Saturday", "Sunday"]


class TestResolveWeekBlocks(unittest.TestCase):
    """The pure day -> block resolver 3.1c's frozen protein floor and 3.1d's
    transition ramp are meant to reuse rather than each re-deriving."""

    def test_a_block_resolves_onto_exactly_the_days_it_covers(self):
        resolved = planner.resolve_week_blocks(
            [make_block()], planner.WEEKDAY_NAMES, today=BLOCK_TODAY
        )
        for day in BLOCK_WINDOW:
            self.assertIsNotNone(resolved[day], day)
            self.assertEqual(resolved[day]["name"], "fast-800-kickstart", day)
        for day in BLOCK_OUTSIDE:
            self.assertIsNone(resolved[day], day)

    def test_no_blocks_resolves_every_day_to_none(self):
        resolved = planner.resolve_week_blocks([], planner.WEEKDAY_NAMES, today=BLOCK_TODAY)
        self.assertEqual(set(resolved.values()), {None})
        # None (no blocks.json at all) is the same "nothing declared" answer.
        resolved_none = planner.resolve_week_blocks(
            None, planner.WEEKDAY_NAMES, today=BLOCK_TODAY
        )
        self.assertEqual(set(resolved_none.values()), {None})

    def test_a_sparse_day_list_still_resolves_against_the_full_week(self):
        """A hand-built config or test fixture may carry a subset of
        `weekly_schedule`'s seven days — this file's own `config_with` does —
        so resolution must not need the missing days to place the ones it was
        actually asked about."""
        resolved = planner.resolve_week_blocks(
            [make_block()], ["Monday", "Thursday", "Sunday"], today=BLOCK_TODAY
        )
        self.assertIsNotNone(resolved["Monday"])
        self.assertIsNotNone(resolved["Thursday"])
        self.assertIsNone(resolved["Sunday"])

    def test_the_anchor_can_land_anywhere_in_the_same_calendar_week(self):
        """A day *later* in the rotation than the anchor must still resolve
        into the same week as one earlier — the bug this guards against
        instead placed a later name up to six days into the week before."""
        for anchor in (date(2026, 9, 7), date(2026, 9, 10), date(2026, 9, 13)):
            resolved = planner.resolve_week_blocks(
                [make_block()], planner.WEEKDAY_NAMES, today=anchor
            )
            self.assertEqual(
                [day for day in planner.WEEKDAY_NAMES if resolved[day]],
                BLOCK_WINDOW,
                anchor,
            )

    def test_an_unnamed_day_is_left_out_rather_than_raising(self):
        resolved = planner.resolve_week_blocks(
            [make_block()], ["Monday", "Not-A-Day"], today=BLOCK_TODAY
        )
        self.assertEqual(list(resolved), ["Monday"])


class TestBlockDietStylesUnionIntoActiveStyles(unittest.TestCase):
    """A block's `diet_styles` reaches `diet_style_calorie_ceiling` and
    `build_diet_style_rule` through the *same* `active_diet_styles` reading
    every other caller already uses — `day_scoped_entries` is the only
    parser, never a second one for what a block means by "active".

    Mirrors `TestADayScopedCeilingCapsOnlyItsDays` above, sourced from a
    block instead of `dietary_rules.active_diet_styles` directly.
    """

    def config(self, block_diet_styles, **overrides):
        schedule = {
            day: {"calories": 1500, "protein_g": 120, "net_carbs_g": 130, "fat_g": 55}
            for day in BLOCK_WINDOW + BLOCK_OUTSIDE
        }
        config = config_with(
            weekly_schedule=schedule,
            diet_styles={
                "fast_800": {
                    "label": "Fast 800",
                    "principles": "Simple, lean, low-added-fat.",
                    "calorie_ceiling": 1600,
                },
            },
            **overrides,
        )
        return config, [make_block(diet_styles=block_diet_styles)]

    def test_the_block_caps_exactly_the_days_it_covers(self):
        config, blocks = self.config(["fast_800"])
        hydrated = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )["weekly_schedule"]
        for day in BLOCK_WINDOW:
            self.assertEqual(hydrated[day]["calories"], 1600, day)
        for day in BLOCK_OUTSIDE:
            self.assertEqual(hydrated[day]["calories"], DYNAMIC_KCAL, day)

    def test_it_unions_with_a_base_config_style_rather_than_replacing_it(self):
        """A style already active every day through the base config must keep
        applying outside the block's window too — the block adds a window,
        it does not become the whole of `active_diet_styles`."""
        config, blocks = self.config(
            ["fast_800"], dietary_rules={"active_diet_styles": ["fast_800"]}
        )
        hydrated = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )["weekly_schedule"]
        for day in BLOCK_WINDOW + BLOCK_OUTSIDE:
            self.assertEqual(hydrated[day]["calories"], 1600, day)

    def test_no_blocks_argument_leaves_every_day_at_the_engine_figure(self):
        config, _ = self.config(["fast_800"])
        hydrated = planner.hydrate_dynamic_targets(config, WEIGH_IN)["weekly_schedule"]
        for day in BLOCK_WINDOW + BLOCK_OUTSIDE:
            self.assertEqual(hydrated[day]["calories"], DYNAMIC_KCAL, day)

    def test_hydrating_twice_lands_on_the_same_number(self):
        """The UI hydrates once for its own live preview and generation
        hydrates that same config again — the block feed is resolved fresh
        from `blocks`/`today` both times rather than from the previous
        pass's own output, so it must land on the identical figure twice."""
        config, blocks = self.config(["fast_800"])
        once = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )
        twice = planner.hydrate_dynamic_targets(
            once, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )
        self.assertEqual(twice["weekly_schedule"], once["weekly_schedule"])

    def test_a_stated_day_inside_the_window_is_not_capped(self):
        config, blocks = self.config(["fast_800"], target_locks={"Monday": ["calories"]})
        hydrated = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )["weekly_schedule"]
        self.assertEqual(hydrated["Monday"]["calories"], 1500)
        self.assertEqual(hydrated["Tuesday"]["calories"], 1600)

    def test_a_malformed_block_diet_style_entry_raises(self):
        """Reusing `day_scoped_entries` means reusing its raise: a block
        naming an unknown weekday is a typo, not data to silently drop — the
        same policy the base `active_diet_styles` already carries, and the
        opposite of `inventory_entries`' drop-with-warning one."""
        config, blocks = self.config([{"style": "fast_800", "days": ["Blursday"]}])
        with self.assertRaises(ValueError):
            planner.hydrate_dynamic_targets(
                config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
            )


class TestBlockTargetRateFeedsTheDeficit(unittest.TestCase):
    """`target_rate_kg_per_week` replaces the whole-week deficit
    `calculate_dynamic_deficit` produced, for exactly the days a block
    resolves onto: `tdee - (rate * KCAL_PER_KG_TISSUE / 7)`.
    """

    RATE = 1.0

    def config(self, **overrides):
        schedule = {
            day: {"calories": 1500, "protein_g": 120, "net_carbs_g": 130, "fat_g": 55}
            for day in BLOCK_WINDOW + BLOCK_OUTSIDE
        }
        config = config_with(weekly_schedule=schedule, **overrides)
        block = make_block(target_rate_kg_per_week=self.RATE)
        return config, [block]

    def expected_block_calories(self, hydrated: dict) -> int:
        basis = hydrated["dynamic_basis"]
        return round(basis["tdee"] - self.RATE * planner.KCAL_PER_KG_TISSUE / 7.0)

    def test_the_block_days_use_the_rate_based_deficit(self):
        config, blocks = self.config()
        hydrated = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )
        expected = self.expected_block_calories(hydrated)
        self.assertNotEqual(expected, DYNAMIC_KCAL)
        for day in BLOCK_WINDOW:
            self.assertEqual(hydrated["weekly_schedule"][day]["calories"], expected, day)

    def test_days_outside_the_block_keep_the_whole_week_figure(self):
        config, blocks = self.config()
        hydrated = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )["weekly_schedule"]
        for day in BLOCK_OUTSIDE:
            self.assertEqual(hydrated[day]["calories"], DYNAMIC_KCAL, day)

    def test_a_stated_day_is_never_touched_by_the_rate(self):
        config, blocks = self.config(target_locks={"Monday": ["calories"]})
        hydrated = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )["weekly_schedule"]
        self.assertEqual(hydrated["Monday"]["calories"], 1500)

    def test_it_never_mutates_the_config_it_is_handed(self):
        """`hydrate_dynamic_targets` never writes `weekly_schedule` directly —
        true before blocks existed and still true with one active."""
        config, blocks = self.config()
        before = config["weekly_schedule"]["Monday"]["calories"]
        planner.hydrate_dynamic_targets(config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY)
        self.assertEqual(config["weekly_schedule"]["Monday"]["calories"], before)

    def test_it_never_touches_target_modes(self):
        config, blocks = self.config(target_modes={"protein_g": "manual"})
        hydrated = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )
        self.assertEqual(hydrated["target_modes"], {"protein_g": "manual"})

    def test_the_uplift_is_still_replayed_on_a_block_day(self):
        config, blocks = self.config(
            meal_types=["breakfast", "lunch", "dinner", "snack"],
            meal_weights={"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
            training_schedule=[{
                "day": "Monday", "time": "18:00", "type": "gym_hypertrophy",
                "estimated_burn_kcal": 350,
            }],
        )
        adjusted = planner.apply_training_adjustments(config)
        hydrated = planner.hydrate_dynamic_targets(
            adjusted, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )
        expected = self.expected_block_calories(hydrated) + 350
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], expected)

    def test_a_diet_style_ceiling_still_caps_a_rate_based_day(self):
        """A block may declare both `target_rate_kg_per_week` and
        `diet_styles` naming a style with a `calorie_ceiling` — the ceiling
        is applied after whichever base the day started from, unchanged from
        the non-block path, and a tight enough one still wins."""
        config, blocks = self.config(
            diet_styles={
                "fast_800": {
                    "label": "Fast 800", "principles": "x", "calorie_ceiling": 1200,
                },
            },
        )
        blocks[0]["diet_styles"] = ["fast_800"]
        hydrated = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )["weekly_schedule"]
        for day in BLOCK_WINDOW:
            self.assertEqual(hydrated[day]["calories"], 1200, day)

    def test_hydrating_twice_lands_on_the_same_number(self):
        config, blocks = self.config()
        once = planner.hydrate_dynamic_targets(
            config, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )
        twice = planner.hydrate_dynamic_targets(
            once, WEIGH_IN, blocks=blocks, today=BLOCK_TODAY
        )
        self.assertEqual(twice["weekly_schedule"], once["weekly_schedule"])


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


def biometrics_series(daily_kcal: float, kg_lost_over_window: float = 0.7) -> dict:
    """14 days of weigh-ins and logs, losing `kg_lost_over_window` in total.

    A linear decline rather than a realistic wobble: `calculate_adaptive_tdee`
    fits a least-squares slope, and a clean line makes the expected figure
    something a reader can check by hand — mean intake plus (kg/day x 7700).
    """
    days = [f"2026-08-{day:02d}" for day in range(3, 17)]
    step = kg_lost_over_window / (len(days) - 1)
    return {
        "weigh_ins": [
            {"date": day, "weight_kg": round(98.4 - step * i, 3)}
            for i, day in enumerate(days)
        ],
        "daily_actuals": [
            {"date": day, "calories": daily_kcal, "protein_g": 150,
             "net_carbs_g": 120, "fat_g": 80}
            for day in days
        ],
    }


class TestAdaptiveTdeeReachesTheTargets(unittest.TestCase):
    """The loop closing: measured intake and weight trend correcting TDEE.

    Before this, `calculate_adaptive_tdee` was fully built and tested and
    called by nothing — `daily_actuals` was written to disk by the Cronometer
    sync, read once by `logged_intake_for` for a single regenerated meal, and
    never influenced a target. The formula estimate was used even where a
    direct measurement of this body was available.
    """

    def test_without_a_series_the_formula_still_wins(self):
        """Every pre-existing caller omits `biometrics`, and must be unchanged."""
        hydrated = planner.hydrate_dynamic_targets(config_with(), WEIGH_IN)
        self.assertEqual(hydrated["dynamic_basis"]["tdee_source"], "formula")
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL)

    def test_a_believable_measurement_replaces_the_formula(self):
        # ~2500 kcal/day eaten while losing 0.7 kg over 13 days is roughly
        # 2500 + (0.0538 x 7700) = ~2915, within 25% of the formula's 2627.
        hydrated = planner.hydrate_dynamic_targets(
            config_with(), WEIGH_IN, None, biometrics_series(2500)
        )
        basis = hydrated["dynamic_basis"]
        self.assertEqual(basis["tdee_source"], "adaptive")
        self.assertGreater(basis["tdee"], basis["tdee_formula"])
        self.assertNotEqual(hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL)

    def test_an_implausible_measurement_is_rejected_not_blended(self):
        """Systematic under-logging is the common failure and it depresses the
        estimate — 900 kcal/day "eaten" would otherwise cut the target."""
        hydrated = planner.hydrate_dynamic_targets(
            config_with(), WEIGH_IN, None, biometrics_series(600)
        )
        basis = hydrated["dynamic_basis"]
        self.assertEqual(basis["tdee_source"], "formula_adaptive_rejected")
        self.assertEqual(basis["tdee"], basis["tdee_formula"])
        # Rejected means the week plans exactly as it would have with no data.
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL)

    def test_too_short_a_series_reads_as_no_measurement(self):
        """Two weigh-ins a day apart x 7700 is noise amplification, not data."""
        short = {
            "weigh_ins": [
                {"date": "2026-08-15", "weight_kg": 98.4},
                {"date": "2026-08-16", "weight_kg": 98.1},
            ],
            "daily_actuals": [{"date": "2026-08-16", "calories": 2000}],
        }
        hydrated = planner.hydrate_dynamic_targets(config_with(), WEIGH_IN, None, short)
        self.assertEqual(hydrated["dynamic_basis"]["tdee_source"], "formula")

    def test_the_basis_reports_both_numbers(self):
        """Diagnostic, not planning input: two runs a fortnight apart will
        disagree, and `basis` is what says why."""
        hydrated = planner.hydrate_dynamic_targets(
            config_with(), WEIGH_IN, None, biometrics_series(2500)
        )
        basis = hydrated["dynamic_basis"]
        self.assertIsNotNone(basis["tdee_adaptive"])
        self.assertIsNotNone(basis["tdee_formula"])
        self.assertEqual(basis["tdee"], basis["tdee_adaptive"])

    def test_protein_stays_locked_whichever_tdee_wins(self):
        """Energy is what a measurement buys back. Protein is tied to the
        *target* weight and must not move with it."""
        for label, series in [("adaptive", biometrics_series(2500)), ("none", None)]:
            with self.subTest(source=label):
                hydrated = planner.hydrate_dynamic_targets(
                    config_with(), WEIGH_IN, None, series
                )
                for day in hydrated["weekly_schedule"].values():
                    self.assertEqual(day["protein_g"], LOCKED_PROTEIN_G)


class TestEmptyScheduleIsNotAFailure(unittest.TestCase):
    def test_an_empty_weekly_schedule_returns_the_config_untouched(self):
        """The loop never runs, so nothing raises into the fallback — but
        `basis` stays None and there is no day to read a protein figure off,
        which the summary log line used to dereference."""
        config = config_with(weekly_schedule={})
        self.assertIs(planner.hydrate_dynamic_targets(config, WEIGH_IN), config)


if __name__ == "__main__":
    unittest.main()


class TestTargetModesDecideWhoOwnsANumber(unittest.TestCase):
    """`target_modes`/`target_locks` — who gets to state a macro's value.

    Written after the bug they fix, per CLAUDE.md's rule about recording the
    failure and not just the fix. Hydration used to overwrite every day's
    calories, protein and fat unconditionally, which made two things true at
    once: `weekly_schedule`'s stated numbers were dead weight the moment a
    weigh-in existed, and every override typed into the review dialog was a
    silent no-op — the UI accepted 2200 kcal, moved the bar, and generation
    planned the computed figure regardless.
    """

    def test_auto_is_the_default_and_replaces_the_file(self):
        """A config predating `target_modes` plans exactly as it did before."""
        hydrated = planner.hydrate_dynamic_targets(config_with(), WEIGH_IN)
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], DYNAMIC_KCAL)
        self.assertEqual(
            hydrated["weekly_schedule"]["Monday"]["protein_g"], LOCKED_PROTEIN_G
        )

    def test_manual_keeps_the_file_figure(self):
        hydrated = planner.hydrate_dynamic_targets(
            config_with(target_modes={"calories": "manual", "protein_g": "auto"}),
            WEIGH_IN,
        )
        # config_with's Monday states 1500; the engine would say DYNAMIC_KCAL.
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], 1500)
        # The other macro is untouched by the switch.
        self.assertEqual(
            hydrated["weekly_schedule"]["Monday"]["protein_g"], LOCKED_PROTEIN_G
        )

    def test_manual_still_derives_fat_from_the_stated_numbers(self):
        """Fat has no mode — it is always whatever energy is left."""
        hydrated = planner.hydrate_dynamic_targets(
            config_with(target_modes={"calories": "manual", "protein_g": "manual"}),
            WEIGH_IN,
        )
        monday = hydrated["weekly_schedule"]["Monday"]
        self.assertEqual(
            monday["fat_g"],
            round(planner.derive_fat_g(1500, 120, 130), 1),
        )

    def test_a_per_day_lock_beats_auto_for_that_day_only(self):
        """`target_locks` is how a review-dialog override reaches hydration.

        Without it the fold into `weekly_schedule` is invisible here —
        hydration cannot tell an edited value from a stale file one, and
        overwrote both.
        """
        hydrated = planner.hydrate_dynamic_targets(
            config_with(target_locks={"Monday": ["calories"]}), WEIGH_IN
        )
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], 1500)
        self.assertEqual(
            hydrated["weekly_schedule"]["Wednesday"]["calories"], DYNAMIC_KCAL
        )

    def test_every_macro_manual_never_calls_the_engine(self):
        """A week planned entirely off the file must not need a weigh-in.

        Passing `None` where a weigh-in goes is what would raise inside
        `calculate_macro_targets`; reaching the assertions at all is the
        test. `dynamic_basis` is absent because nothing was computed.
        """
        hydrated = planner.hydrate_dynamic_targets(
            config_with(target_modes={"calories": "manual", "protein_g": "manual"}),
            None,
        )
        self.assertEqual(hydrated["weekly_schedule"]["Monday"]["calories"], 1500)
        self.assertNotIn("dynamic_basis", hydrated)

    def test_hydration_is_idempotent_for_a_stated_macro(self):
        """The UI hydrates for its live preview and generation hydrates again.

        An earlier version subtracted the training uplift from a stated
        figure to undo what `apply_training_adjustments` had added. That was
        right exactly once: the second pass subtracted it from a number that
        no longer carried it, taking a 2200 kcal override down to 1850.
        """
        config = config_with(
            target_locks={"Monday": ["calories"]},
            meal_types=["breakfast", "lunch", "dinner", "snack"],
            meal_weights={"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
            training_schedule=[{
                "day": "Monday", "time": "18:00", "type": "gym_hypertrophy",
                "estimated_burn_kcal": 350,
            }],
        )
        once = planner.hydrate_dynamic_targets(
            planner.apply_training_adjustments(config), WEIGH_IN
        )
        twice = planner.hydrate_dynamic_targets(once, WEIGH_IN)
        self.assertEqual(
            once["weekly_schedule"]["Monday"]["calories"],
            twice["weekly_schedule"]["Monday"]["calories"],
        )


class TestAWorkoutDoesNotGrowAStatedTarget(unittest.TestCase):
    """`apply_training_adjustments` skips a macro somebody stated.

    A toggle that changes *who decides* a number must not change the number.
    Before this, flipping protein to manual moved a training Monday from
    144 g to 187.8 g, because the uplift was still being added on top of the
    figure the toggle had just seeded from the engine.
    """

    def setUp(self):
        self.session = [{
            "day": "Monday", "time": "18:00", "type": "gym_hypertrophy",
            "estimated_burn_kcal": 350,
        }]
        self.shape = {
            "meal_types": ["breakfast", "lunch", "dinner", "snack"],
            "meal_weights": {
                "breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1
            },
        }

    def adjusted(self, **extra):
        return planner.apply_training_adjustments(
            config_with(training_schedule=self.session, **self.shape, **extra)
        )

    def test_an_auto_macro_is_still_expanded(self):
        adjusted = self.adjusted()
        self.assertEqual(adjusted["weekly_schedule"]["Monday"]["calories"], 1500 + 350)
        self.assertEqual(adjusted["training_uplift"]["Monday"]["calories"], 350.0)

    def test_a_manual_macro_is_left_alone(self):
        adjusted = self.adjusted(target_modes={"calories": "manual"})
        self.assertEqual(adjusted["weekly_schedule"]["Monday"]["calories"], 1500)

    def test_the_uplift_it_did_not_apply_is_not_recorded(self):
        """Hydration replays this record onto the engine's base and the review
        dialog draws it as the bar's amber segment, so recording calories
        nothing added would show the day holding energy it never got."""
        adjusted = self.adjusted(target_modes={"calories": "manual"})
        self.assertNotIn("calories", adjusted["training_uplift"].get("Monday", {}))
        # The macros still on auto keep their share.
        self.assertEqual(
            adjusted["training_uplift"]["Monday"]["net_carbs_g"], 350 * 0.5 / 4
        )

    def test_a_locked_day_is_left_alone_and_its_neighbours_are_not(self):
        adjusted = planner.apply_training_adjustments(
            config_with(
                training_schedule=self.session + [{
                    "day": "Wednesday", "time": "18:00", "type": "gym_hypertrophy",
                    "estimated_burn_kcal": 350,
                }],
                target_locks={"Monday": ["calories"]},
                **self.shape,
            )
        )
        self.assertEqual(adjusted["weekly_schedule"]["Monday"]["calories"], 1500)
        self.assertEqual(adjusted["weekly_schedule"]["Wednesday"]["calories"], 1000 + 350)

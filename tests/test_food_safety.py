"""Tests for storage windows as a property of the dish.

**This is the only part of the app where being wrong makes somebody ill**, and
these tests are written with that in mind: every default is asserted to fail
*short*, which inverts the convention every other optional field in this
codebase follows.

The defect this feature fixed was live. `inventory_rules.fridge_safe_days` was
a single number, 3, read in six places, and it was wrong in both directions at
once — too short for a beef stew (a day of good food thrown away, and a batch
that could have covered Thursday), and, the direction that matters, too long
for a rice tray bake. Cooked rice and pasta carry *Bacillus cereus* spores
that survive cooking and produce toxin as the dish sits; the shipped config
permitted a rice tray bake batched on prep day to be eaten a day past its safe
window, and `apply_batch_selections` built exactly that shape on every week
the long-cook toggle ran.

Two behaviour changes ride together here and the pairing is deliberate: the
default window *lengthens* (3 day-gaps to 4, so a prep-day batch may now reach
Thursday) while rice *tightens* (to 2). Lengthening alone would have made the
dangerous case worse, so `TestTheDefaultLengthened` and
`TestRiceIsBoundShorter` are two halves of one change and neither may be
removed without the other.

`unittest` and the `sys.path` insert match `test_week_mechanics.py`; see its
docstring for why.
"""

import asyncio
import sys
import unittest
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import planner  # noqa: E402
import week as wk  # noqa: E402
from planner import Ingredient, Recipe  # noqa: E402
from ui_generation import apply_batch_selections  # noqa: E402
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, WeekSpec  # noqa: E402

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]


def spec_with(modes=None, servings_per_meal=2) -> WeekSpec:
    modes = modes or {}
    slots = []
    for day in DAYS:
        for meal_type in MEAL_TYPES:
            override = modes.get(wk.slot_id(day, meal_type), {})
            slots.append(
                SlotSpec(
                    day=day,
                    meal_type=meal_type,
                    mode=override.get("mode", MODE_COOK),
                    source=override.get("source"),
                )
            )
    return WeekSpec(days=DAYS, slots=slots, servings_per_meal=servings_per_meal)


def recipe(name="Tray bake", meal_type="dinner", storage_class=None, **kw) -> Recipe:
    return Recipe(
        name=name,
        meal_type=meal_type,
        servings=kw.get("servings", 1),
        storage_class=storage_class,
        prep_time_minutes=20,
        instructions=["Cook it."],
        ingredients=[
            Ingredient(
                name="test food", quantity_g=200.0, nova_group=1,
                calories=400.0, protein_g=40.0, net_carbs_g=20.0, fat_g=15.0,
            )
        ],
    )


BASE_CONFIG = {
    "cuisines": ["thai"],
    "cuisine_meal_types": ["dinner"],
    "meal_types": MEAL_TYPES,
    "meal_styles": {"dinner": {"curry": "..."}},
    "meal_weights": {"breakfast": 0.3, "lunch": 0.3, "dinner": 0.3, "snack": 0.1},
    "planning_rules": dict(planner.DEFAULT_PLANNING_RULES),
    "week_defaults": {k: MODE_COOK for k in MEAL_TYPES},
    "serving_rules": {"servings_per_meal": 2},
    "week_start_day": "Monday",
    "weekly_schedule": {day: {} for day in DAYS},
    "inventory_rules": dict(wk.DEFAULT_INVENTORY_RULES),
    "dietary_rules": {
        "allowed_nova_groups": [1, 2, 3],
        "banned_ingredients": [],
        "active_diet_styles": [],
    },
    "enable_sunday_prep": False,
    "max_prep_active_mins": 120,
}


# ---------------------------------------------------------------------------
# The resolver: hours in the table, whole day-gaps everywhere else
# ---------------------------------------------------------------------------


class TestTheWindowsAreMeasuredInDays(unittest.TestCase):
    """The tables are hours and every consumer holds a date.

    Nothing anywhere stores a *time* — a `SlotSpec` carries a weekday name, a
    `WeekPlan` carries `week_start_date`, a `CookEvent` resolves to a grid day
    — so no consumer can establish that a Sunday cook eaten Thursday was
    inside 96 hours. The hours are the guidance the figures were derived from;
    the day-gap is what the app enforces, and it is the only unit any surface
    is allowed to print.
    """

    def test_hours_become_whole_day_gaps(self):
        self.assertEqual(wk.storage_day_gaps(96), 4)
        self.assertEqual(wk.storage_day_gaps(48), 2)
        self.assertEqual(wk.storage_day_gaps(72), 3)

    def test_a_part_day_never_rounds_up(self):
        """The floor, so a stated window is never over-claimed by rounding."""
        self.assertEqual(wk.storage_day_gaps(95), 3)
        self.assertEqual(wk.storage_day_gaps(47), 1)

    def test_a_window_shorter_than_a_day_is_zero_not_negative(self):
        self.assertEqual(wk.storage_day_gaps(12), 0)
        self.assertEqual(wk.storage_day_gaps(0), 0)

    def test_no_hour_figure_reaches_a_message(self):
        """The app does not know hours, so it must not print them. Checked
        across every surface that renders a window: the storage note, the
        `validate_week` backstop and the freezer warning."""
        spec = spec_with({
            f"{day}:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"}
            for day in DAYS[1:]
        })
        messages = wk.storage_safety_errors(spec, BASE_CONFIG, {"Monday:dinner": None})
        messages.append(planner.storage_note(6, 6, storage_class="rice_or_pasta"))
        messages.append(
            wk.freezer_quality_note(date(2020, 1, 1), date(2026, 1, 1), "fried")
        )
        self.assertTrue(messages)
        for message in messages:
            self.assertNotIn("hour", message.lower())
            for hours in ("96", "48", "72"):
                self.assertNotIn(hours, message)


class TestEveryDefaultFailsShort(unittest.TestCase):
    """The one place the codebase's usual default convention is inverted.

    Everywhere else an absent value resolves to the behaviour before the
    feature existed — `long_oven_cook` defaults False, `total_time_minutes`
    defaults to None meaning unknown, an absent `sourcing` block emits
    nothing. All of those are safe because being wrong costs a worse meal
    plan. Here it costs a food-poisoning risk.

    And the failure it guards against is documented rather than theoretical:
    `is_sunday_prepped` broke because a batch anchor came back with both its
    flags False despite the per-slot directive telling the model to set one —
    a self-report the model simply dropped, on a field the prompt explicitly
    asked for. If a dropped `storage_class` resolved to the default row, the
    failure mode of a model forgetting a field would be a rice dish scheduled
    four days out.
    """

    def test_an_unclassified_dish_gets_the_shortest_fridge_window(self):
        self.assertEqual(wk.fridge_day_gaps(None), wk.fridge_day_gaps("rice_or_pasta"))
        self.assertLess(wk.fridge_day_gaps(None), wk.fridge_day_gaps("default"))

    def test_an_unclassified_dish_gets_the_shortest_freezer_window(self):
        self.assertEqual(wk.freezer_months(None), 1)
        self.assertEqual(wk.freezer_months(None), min(
            wk.DEFAULT_STORAGE_WINDOWS["freezer_months"].values()
        ))

    def test_a_class_nobody_recognises_is_unclassified(self):
        """A model inventing a word, or a hand-edited catalog entry. It fails
        short rather than raising: `Recipe.storage_class` is deliberately a
        plain `Optional[str]` and not a `Literal`, because a `Literal` turns a
        vocabulary typo into a hard retry, and a retry is 30s-3min on a free
        route. The span validator still rejects anything genuinely too short
        for its slot, so nothing unsafe gets through the softness."""
        self.assertEqual(wk.fridge_day_gaps("braised_something"), wk.fridge_day_gaps(None))
        self.assertEqual(wk.freezer_months("braised_something"), wk.freezer_months(None))

    def test_the_shortest_is_the_tables_own_minimum_not_a_hardcoded_rice(self):
        """If a shorter class is ever added, the unclassified case has to
        follow it *down*. Hardcoding the rice figure would leave the default
        stranded above a new, tighter row."""
        config = {
            "inventory_rules": {"storage_windows": {"fridge": {"raw_fish": 24}}}
        }
        self.assertEqual(wk.fridge_day_gaps(None, config), 1)

    def test_default_is_a_statement_and_none_is_its_absence(self):
        """Two different answers, deliberately. `"default"` means somebody
        looked and said "an ordinary cooked dish"; `None` means nobody said.
        The same distinction `total_time_minutes` draws between None and 0,
        and the one an un-marked adherence row draws against a marked one."""
        self.assertEqual(wk.fridge_day_gaps("default"), 4)
        self.assertEqual(wk.fridge_day_gaps(None), 2)

    def test_a_recipe_saved_before_the_field_existed_loads_and_is_short(self):
        stored = recipe(storage_class="cooked_meat").model_dump()
        del stored["storage_class"]
        revived = Recipe.model_validate(stored)
        self.assertIsNone(revived.storage_class)
        self.assertEqual(wk.fridge_day_gaps(revived.storage_class), 2)

    def test_a_class_the_fridge_table_does_not_name_gets_the_default_row(self):
        """A stew has no fridge row because it keeps as long as anything else
        — only rice is exceptional there. That is not the same as being
        unclassified, and resolving it short would throw away good food on
        exactly the dish batch cooking exists for."""
        self.assertEqual(wk.fridge_day_gaps("soup_stew_casserole"), 4)
        self.assertEqual(wk.fridge_day_gaps("cooked_poultry"), 4)


class TestAConfigCannotDeleteTheRiceException(unittest.TestCase):
    """Tables merge over the shipped ones rather than replacing them.

    Replacing meant a config stating only `fridge: {"default": 72}` had no
    `rice_or_pasta` row at all, so a rice dish resolved through the `default`
    row — *lengthening* its window, the one direction nothing here may move by
    accident.
    """

    PARTIAL = {"inventory_rules": {"storage_windows": {"fridge": {"default": 72}}}}

    def test_an_override_of_one_row_keeps_the_others(self):
        self.assertEqual(wk.fridge_day_gaps("default", self.PARTIAL), 3)
        self.assertEqual(wk.fridge_day_gaps("rice_or_pasta", self.PARTIAL), 2)

    def test_overriding_the_fridge_table_keeps_the_freezer_one(self):
        self.assertEqual(wk.freezer_months("soup_stew_casserole", self.PARTIAL), 2)

    def test_a_config_may_still_tighten_the_rice_row(self):
        config = {
            "inventory_rules": {"storage_windows": {"fridge": {"rice_or_pasta": 24}}}
        }
        self.assertEqual(wk.fridge_day_gaps("rice_or_pasta", config), 1)


# ---------------------------------------------------------------------------
# The two behaviour changes, which must land together
# ---------------------------------------------------------------------------


class TestTheDefaultLengthened(unittest.TestCase):
    """The permissive half, asserted rather than discovered.

    `apply_batch_selections` bounds a prep-day batch at `window - 1` day
    indices, because day index `i` is `i + 1` days after prep. The old global
    of 3 gave `max_day_index = 2` (Wednesday); the 4-day default gives 3
    (Thursday). This is a real behaviour change in the permissive direction
    riding on a safety change, which is exactly why it is pinned here.
    """

    CONFIG = dict(BASE_CONFIG, bulk_prep_enabled=True, long_cook_enabled=False)

    def test_the_bound_reaches_thursday(self):
        self.assertEqual(wk.fridge_day_gaps("default", self.CONFIG) - 1, 3)
        self.assertEqual(DAYS[3], "Thursday")

    def test_a_batch_stepping_over_a_blocked_day_now_reaches_thursday(self):
        """On the untouched shipped grid the batch still stops at Wednesday,
        and not because of the fridge bound: `spread_batch`'s `target_claims`
        caps at 3 claims, so an unobstructed walk is Monday/Tuesday/Wednesday
        either way. The lengthening is only visible when the walk has to step
        over a day it cannot claim — which is the case the old bound silently
        shortened, leaving the batch a claim short of its target.
        """
        base = spec_with()
        blocked = base.model_copy(update={
            "slots": [
                slot.model_copy(update={"mode": MODE_SKIP})
                if slot.id == "Tuesday:lunch" else slot
                for slot in base.slots
            ]
        })
        spread, anchors = apply_batch_selections(blocked, self.CONFIG)
        self.assertEqual(anchors["bulk_prep_anchor"], "Monday:lunch")
        self.assertEqual(
            wk.eaten_on(spread)["Monday:lunch"],
            ["Monday:lunch", "Wednesday:lunch", "Thursday:lunch"],
        )

    def test_the_old_bound_would_have_stopped_at_wednesday(self):
        """The same grid under the pre-change window, to show the difference
        is the window and not the grid."""
        old = dict(
            self.CONFIG,
            inventory_rules={"storage_windows": {"fridge": {"default": 72}}},
        )
        base = spec_with()
        blocked = base.model_copy(update={
            "slots": [
                slot.model_copy(update={"mode": MODE_SKIP})
                if slot.id == "Tuesday:lunch" else slot
                for slot in base.slots
            ]
        })
        spread, _ = apply_batch_selections(blocked, old)
        self.assertEqual(
            wk.eaten_on(spread)["Monday:lunch"], ["Monday:lunch", "Wednesday:lunch"]
        )


class TestRiceIsBoundShorter(unittest.TestCase):
    """The safety half. A rice dish may sit two day-gaps, not four.

    Both routes to a long span are covered, because only one of them goes
    through `spread_batch`: the batch toggles, and a hand-built chain of "Link
    to next lunch" clicks that never touches the planner at all.
    """

    def test_a_rice_dish_gets_half_the_default_window(self):
        self.assertEqual(wk.fridge_day_gaps("rice_or_pasta"), 2)
        self.assertEqual(wk.fridge_day_gaps("default"), 4)

    def _chain(self, *days):
        return spec_with({
            f"{day}:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"}
            for day in days
        })

    def test_a_hand_built_chain_past_the_rice_window_is_reported(self):
        """Three "Link to next lunch" clicks, which never go through
        `spread_batch` at all — the whole reason the backstop exists."""
        spec = self._chain("Tuesday", "Wednesday", "Thursday")
        rice = wk.storage_safety_errors(
            spec, BASE_CONFIG, {"Monday:dinner": "rice_or_pasta"}
        )
        self.assertEqual(len(rice), 1)
        self.assertIn("Monday dinner", rice[0])
        self.assertIn("cooked 3 days", rice[0])
        self.assertIn("rice/pasta", rice[0])

    def test_the_same_chain_is_fine_for_a_stew(self):
        self.assertEqual(
            wk.storage_safety_errors(
                self._chain("Tuesday", "Wednesday", "Thursday"),
                BASE_CONFIG,
                {"Monday:dinner": "soup_stew_casserole"},
            ),
            [],
        )

    def test_two_day_gaps_are_the_rice_limit_not_one_past_it(self):
        """The boundary, pinned: 48 hours is two day-gaps under the
        day-primary reading `design-05` §5 settles on. One day either side of
        a limit is where an off-by-one hides."""
        self.assertEqual(
            wk.storage_safety_errors(
                self._chain("Tuesday", "Wednesday"),
                BASE_CONFIG,
                {"Monday:dinner": "rice_or_pasta"},
            ),
            [],
        )

    def test_a_prep_day_rice_anchor_is_caught_where_a_grid_day_one_is_not(self):
        """The prevention half runs against the *default* window because no
        recipe exists when the grid is built, so a rice anchor is caught by
        the prompt, the response validator, and finally here. The anchor's
        span is measured from prep day, which is what tips a Monday-Wednesday
        batch from two day-gaps to three."""
        spread, anchors = apply_batch_selections(
            spec_with(), dict(BASE_CONFIG, bulk_prep_enabled=True)
        )
        classes = {anchors["bulk_prep_anchor"]: "rice_or_pasta"}
        self.assertEqual(wk.storage_safety_errors(spread, BASE_CONFIG, classes), [])
        anchored = dict(BASE_CONFIG, **{k: v for k, v in anchors.items() if v})
        errors = wk.storage_safety_errors(spread, anchored, classes)
        self.assertEqual(len(errors), 1)
        self.assertIn("re-point that meal", errors[0])


# ---------------------------------------------------------------------------
# The backstop
# ---------------------------------------------------------------------------


class TestTheBackstopChangedCharacter(unittest.TestCase):
    """It was a static check on the grid against one global number; it now
    checks a *generated* week against the dishes actually in it.

    The two calls differ only in what they know, and the difference is exactly
    `storage_classes`: `None` is "no plan yet, judge against the default the
    grid was planned against", a mapping is "a real week, and a slot missing
    from it is genuinely unclassified".
    """

    def _three_day_chain(self):
        return spec_with({
            "Tuesday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
            "Wednesday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
            "Thursday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })

    def test_a_bare_grid_is_judged_against_the_default(self):
        """Not against the shortest window. The grid stage cannot know what
        the dish will be, and `spread_batch` planned it against the default —
        so judging it shorter here would pre-emptively fail a grid the app
        itself just built."""
        self.assertEqual(wk.storage_safety_errors(self._three_day_chain(), BASE_CONFIG), [])

    def test_a_generated_week_with_an_unclassified_dish_is_judged_short(self):
        errors = wk.storage_safety_errors(
            self._three_day_chain(), BASE_CONFIG, {"Monday:dinner": None}
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("an unclassified dish", errors[0])

    def test_a_slot_missing_from_the_mapping_is_unclassified_too(self):
        errors = wk.storage_safety_errors(self._three_day_chain(), BASE_CONFIG, {})
        self.assertEqual(len(errors), 1)

    def test_it_names_the_slot_and_the_days_and_trims_nothing(self):
        spec = self._three_day_chain()
        errors = wk.storage_safety_errors(spec, BASE_CONFIG, {"Monday:dinner": None})
        self.assertIn("Monday dinner", errors[0])
        self.assertIn("cooked 3 days", errors[0])
        self.assertIn("Thursday dinner", errors[0])
        # The spec is untouched: a plan quietly rewritten is one nobody checks.
        self.assertEqual(spec.by_id()["Thursday:dinner"].source, "Monday:dinner")

    def test_validate_week_reads_the_same_function(self):
        """One implementation, two callers — the grid gate and the
        post-generation report — so they cannot come to different answers
        about a Thursday."""
        spec = self._three_day_chain()
        classes = {"Monday:dinner": "rice_or_pasta"}
        self.assertEqual(
            wk.storage_safety_errors(spec, BASE_CONFIG, classes),
            [e for e in wk.validate_week(spec, BASE_CONFIG, classes) if "fridge" in e],
        )


# ---------------------------------------------------------------------------
# Prep day is where the clock starts
# ---------------------------------------------------------------------------


class TestAPrepDayAnchorIsMeasuredFromPrepDay(unittest.TestCase):
    """CLAUDE.md records this exact off-by-one being fixed twice already —
    once as `max_day_index`, once in `storage_note` — so a per-dish window is
    the third chance to reintroduce it.

    A batch folded into the prep session is cooked the day *before* the week
    starts (`week.PREP_DAY_INDEX`). Its anchor's grid day is only where the
    leftover chain has to begin, so measuring the span from there is short by
    exactly one on every prep batch — and short in the unsafe direction.
    """

    SPEC_MODES = {
        "Tuesday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        "Wednesday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
    }

    def test_an_anchors_span_is_one_longer_than_its_grid_span(self):
        spec = spec_with(self.SPEC_MODES)
        anchored = dict(BASE_CONFIG, long_cook_anchor="Monday:dinner")
        self.assertEqual(planner.storage_spans(spec, BASE_CONFIG)["Monday:dinner"], 2)
        self.assertEqual(planner.storage_spans(spec, anchored)["Monday:dinner"], 3)

    def test_that_extra_day_is_what_rejects_a_rice_anchor(self):
        """Two day-gaps is exactly the rice window, so the grid-day reading
        would have accepted a rice tray bake here. Measured from prep day it
        is three, and rejects — which is the case that most needs the rule."""
        spec = spec_with(self.SPEC_MODES)
        anchored = dict(BASE_CONFIG, long_cook_anchor="Monday:dinner")
        anchored = dict(anchored, storage_spans=planner.storage_spans(spec, anchored))
        pairs = [("Monday", recipe(storage_class="rice_or_pasta"))]

        planner.reject_short_storage_class(
            pairs, dict(BASE_CONFIG, storage_spans=planner.storage_spans(spec, BASE_CONFIG))
        )
        with self.assertRaises(ValueError) as caught:
            planner.reject_short_storage_class(pairs, anchored)
        self.assertIn("next 3 days", str(caught.exception))

    def test_the_shake_candidate_is_not_prep_day_anchored(self):
        """Only the two dish anchors are cooked ahead. A shake rides along in
        the same session but is only *portioned* ahead — each training morning
        blends it fresh — so its food is exactly as old as its own day says."""
        anchored = dict(
            BASE_CONFIG,
            long_cook_anchor="Monday:dinner",
            bulk_prep_anchor="Monday:lunch",
        )
        self.assertEqual(
            planner.prep_day_batch_slot_ids(anchored),
            {"Monday:dinner", "Monday:lunch"},
        )


# ---------------------------------------------------------------------------
# The prompt, and the validator it is the other half of
# ---------------------------------------------------------------------------


class TestTheModelIsToldTheSpan(unittest.TestCase):
    """The ordering problem: the grid is built before any recipe exists, so at
    the moment a batch's span is chosen nothing knows whether the dish will be
    a rice tray bake.

    Planning short always is safe and useless (no batch could reach past two
    days, which removes bulk cooking); planning long and validating afterwards
    throws a 30s-3min call away to discover a constraint one sentence would
    have stated. Telling the model the span is the third way out, and it is
    what this codebase already does for `build_batch_roast_rule` /
    `reject_misplaced_long_cook`.
    """

    def _config_with_spans(self, spans):
        return dict(BASE_CONFIG, storage_spans=spans)

    def test_nothing_is_said_when_every_span_is_inside_the_short_window(self):
        """Byte-identical to before this feature existed, which is the
        convention every rule in `build_generation_rules` follows."""
        config = self._config_with_spans({"Monday:dinner": 2, "Tuesday:dinner": 0})
        self.assertEqual(
            planner.build_storage_rule(config, ["Monday:dinner", "Tuesday:dinner"]), ""
        )

    def test_the_whole_rules_block_is_byte_identical_for_a_short_week(self):
        without = planner.build_generation_rules(
            BASE_CONFIG, days=["Monday"], style_rule="", variety_rule="", budget_rule="",
        )
        with_short_spans = planner.build_generation_rules(
            self._config_with_spans({"Monday:dinner": 2}),
            days=["Monday"],
            slot_ids=["Monday:dinner"],
            style_rule="", variety_rule="", budget_rule="",
        )
        self.assertEqual(without, with_short_spans)

    def test_a_long_span_names_the_slot_and_the_days(self):
        rule = planner.build_storage_rule(
            self._config_with_spans({"Monday:dinner": 3}), ["Monday:dinner"]
        )
        self.assertIn("Monday dinner", rule)
        self.assertIn("still good 3 days after cooking", rule)
        self.assertIn("rice", rule)

    def test_slots_are_grouped_by_the_figure_they_need(self):
        """Not reduced to the largest: the validator judges each slot against
        its own span, and the prompt has to name the rule it is judged
        against."""
        rule = planner.build_storage_rule(
            self._config_with_spans(
                {"Monday:dinner": 3, "Thursday:dinner": 3, "Friday:dinner": 4}
            ),
            ["Monday:dinner", "Thursday:dinner", "Friday:dinner"],
        )
        self.assertIn("Monday dinner, Thursday dinner (still good 3 days", rule)
        self.assertIn("Friday dinner (still good 4 days", rule)

    def test_the_rule_states_days_and_the_validator_uses_the_same_number(self):
        """A model rejected against a figure different from the one it was
        given is a retry spent on the app's own inconsistency."""
        config = self._config_with_spans({"Monday:dinner": 3})
        self.assertIn("still good 3 days", planner.build_storage_rule(config, ["Monday:dinner"]))
        with self.assertRaises(ValueError) as caught:
            planner.reject_short_storage_class(
                [("Monday", recipe(storage_class="rice_or_pasta"))], config
            )
        self.assertIn("next 3 days", str(caught.exception))


class TestTheValidatorRejectsAShortDish(unittest.TestCase):
    def _config(self, span=3):
        return dict(BASE_CONFIG, storage_spans={"Monday:dinner": span})

    def test_a_dish_that_keeps_long_enough_passes(self):
        planner.reject_short_storage_class(
            [("Monday", recipe(storage_class="soup_stew_casserole"))], self._config()
        )

    def test_a_rice_dish_over_its_window_rejects(self):
        with self.assertRaises(ValueError) as caught:
            planner.reject_short_storage_class(
                [("Monday", recipe(storage_class="rice_or_pasta"))], self._config()
            )
        message = str(caught.exception)
        self.assertIn("Tray bake", message)
        self.assertIn("rice/pasta", message)
        self.assertIn("only good for 2 days", message)

    def test_a_dropped_class_rejects_rather_than_being_defaulted_away(self):
        """The `is_sunday_prepped` failure mode, headed off: a self-report the
        model simply omits. Rejecting is the model being told to answer the
        question, rather than the app guessing on its behalf."""
        with self.assertRaises(ValueError) as caught:
            planner.reject_short_storage_class([("Monday", recipe())], self._config())
        self.assertIn("do not leave it unset", str(caught.exception))

    def test_a_slot_with_no_span_is_left_alone(self):
        """A single-day cook. Nothing to keep, nothing to ask about."""
        planner.reject_short_storage_class(
            [("Monday", recipe())], dict(BASE_CONFIG, storage_spans={"Monday:dinner": 0})
        )
        planner.reject_short_storage_class([("Monday", recipe())], BASE_CONFIG)

    def test_a_batch_anchor_is_not_exempt(self):
        """Unlike `reject_misplaced_long_cook`, which exempts the anchors
        because the *day* judgement is wrong for food cooked before the week
        started. This rule is about the *window*, and an anchor's span is
        measured from prep day and is therefore longer — it is the case that
        most needs the rule."""
        spec = spec_with({
            "Tuesday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
            "Wednesday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        anchored = dict(BASE_CONFIG, long_cook_anchor="Monday:dinner")
        anchored = dict(anchored, storage_spans=planner.storage_spans(spec, anchored))
        with self.assertRaises(ValueError):
            planner.reject_short_storage_class(
                [("Monday", recipe(storage_class="rice_or_pasta"))], anchored
            )

    def test_both_response_axes_reject_identically(self):
        """`DayRecipes` from its context's single day, `MealTypeWeekRecipes`
        over its own day keys, one shared function underneath — so the two
        axes cannot disagree about a Tuesday."""
        config = self._config()
        rice = recipe(storage_class="rice_or_pasta")
        with self.assertRaises(Exception) as day_axis:
            planner.DayRecipes.model_validate(
                {"recipes": [rice.model_dump()]}, context={"config": config, "day": "Monday"}
            )
        with self.assertRaises(Exception) as week_axis:
            planner.MealTypeWeekRecipes.model_validate(
                {"recipes": {"Monday": rice.model_dump()}}, context={"config": config}
            )
        for caught in (day_axis, week_axis):
            self.assertIn("only good for 2 days", str(caught.exception))


class TestAPinnedFavouriteIsTheThirdRoute(unittest.TestCase):
    """A favourite is never generated, so no prompt briefs it and no response
    validator judges it — it is the one route to a long span that neither half
    of §4 can see.

    Leaving it ungated would reproduce, in mirror image, the exact bug
    `favorite_fits_day` was written for: there a *saved* braise was refused a
    Thursday a *generated* braise could take. Here a generated rice dish would
    be rejected for a span a saved one could quietly claim.
    """

    def _favourite(self, name, meal_type, storage_class=None):
        return {
            "id": f"id-{name}",
            "content_key": f"key-{name}",
            "recipe": recipe(name, meal_type, storage_class=storage_class).model_dump(),
            "is_favorite": True,
            "source": "favorited",
        }

    def _config(self, spans):
        return dict(BASE_CONFIG, storage_spans=spans)

    def test_a_short_keeping_favourite_is_passed_over_for_a_long_slot(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:lunch"},
            "Wednesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:lunch"},
            "Thursday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:lunch"},
        })
        config = self._config(planner.storage_spans(spec, BASE_CONFIG))
        assignments = planner.select_favorite_assignments(
            spec, config, [], [self._favourite("Fried rice", "lunch", "rice_or_pasta")]
        )
        self.assertNotIn("Monday:lunch", assignments)

    def test_a_long_keeping_favourite_takes_the_same_slot(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:lunch"},
            "Wednesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:lunch"},
            "Thursday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:lunch"},
        })
        config = self._config(planner.storage_spans(spec, BASE_CONFIG))
        assignments = planner.select_favorite_assignments(
            spec, config, [], [self._favourite("Beef stew", "lunch", "soup_stew_casserole")]
        )
        self.assertIn("Monday:lunch", assignments)

    def test_an_unclassified_favourite_may_still_take_a_short_slot(self):
        """Every one of the 91 recipes in the shipped catalog predates this
        field, so this is the common case rather than an edge one: a favourite
        keeps its place on a slot eaten within two days of its cook, and only
        the long spans go to generation instead — where the model is asked
        what the dish actually is."""
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:lunch"},
        })
        config = self._config(planner.storage_spans(spec, BASE_CONFIG))
        assignments = planner.select_favorite_assignments(
            spec, config, [], [self._favourite("Old import", "lunch")]
        )
        self.assertIn("Monday:lunch", assignments)

    def test_the_gate_is_a_second_axis_not_a_second_reading_of_the_first(self):
        """A slow-cooked stew passes the day rule and the window rule; a rice
        tray bake passes the day rule and fails the window rule. Neither
        implies the other, which is why they are two functions."""
        rice = self._favourite("Fried rice", "dinner", "rice_or_pasta")
        config = self._config({"Monday:dinner": 3})
        self.assertTrue(planner.favorite_fits_day(rice, "Monday", config))
        self.assertFalse(
            planner.favorite_keeps_long_enough(rice, "Monday:dinner", config)
        )

    def test_a_single_day_slot_asks_nothing_of_a_favourite(self):
        rice = self._favourite("Fried rice", "dinner", "rice_or_pasta")
        self.assertTrue(
            planner.favorite_keeps_long_enough(rice, "Monday:dinner", BASE_CONFIG)
        )


# ---------------------------------------------------------------------------
# The freezer half: quality, not safety, and nothing is ever removed
# ---------------------------------------------------------------------------


class TestTheFreezerHalfIsAboutQuality(unittest.TestCase):
    """`data/freezer.json` does not exist yet — this is the resolver the
    freezer ledger will import rather than write a second time, because
    writing it twice is how the two come to disagree about a tub.

    Frozen food does not become unsafe at two months, it degrades. The fridge
    figures are safety and the freezer figures are quality, and conflating the
    two teaches a reader to ignore both.
    """

    TODAY = date(2026, 9, 1)

    def test_a_fresh_lot_says_nothing(self):
        self.assertEqual(
            wk.freezer_quality_note(date(2026, 8, 20), self.TODAY, "soup_stew_casserole"),
            "",
        )

    def test_a_lot_past_its_window_warns(self):
        note = wk.freezer_quality_note(date(2026, 1, 1), self.TODAY, "fried")
        self.assertIn("past its best", note)

    def test_the_warning_never_says_unsafe(self):
        for storage_class in wk.STORAGE_CLASSES + (None,):
            note = wk.freezer_quality_note(date(2020, 1, 1), self.TODAY, storage_class)
            self.assertIn("still safe to eat", note)
            self.assertNotIn("unsafe", note)

    def test_an_undateable_lot_is_flagged_never_assumed_fresh(self):
        """The one field whose absence cannot be defaulted safely. A missing
        freeze date degrades to "no idea how old this is", and the
        conservative reading of that is not a number this function is entitled
        to pick."""
        note = wk.freezer_quality_note(None, self.TODAY, "soup_stew_casserole")
        self.assertIn("No freeze date", note)
        self.assertNotIn("past its best", note)

    def test_an_unclassified_lot_gets_one_month(self):
        stew = wk.freezer_quality_note(date(2026, 7, 15), self.TODAY, "soup_stew_casserole")
        unknown = wk.freezer_quality_note(date(2026, 7, 15), self.TODAY, None)
        self.assertEqual(stew, "")
        self.assertIn("past its best", unknown)

    def test_the_window_reads_from_config(self):
        config = {
            "inventory_rules": {"storage_windows": {"freezer_months": {"fried": 6}}}
        }
        self.assertEqual(
            wk.freezer_quality_note(date(2026, 5, 1), self.TODAY, "fried", config), ""
        )
        self.assertIn(
            "past its best",
            wk.freezer_quality_note(date(2026, 5, 1), self.TODAY, "fried"),
        )


# ---------------------------------------------------------------------------
# The note and the badge, which have already disagreed twice
# ---------------------------------------------------------------------------


class TestTheNoteAndTheBadgeAgree(unittest.TestCase):
    """CLAUDE.md records this pair disagreeing twice — once about
    `cook_day_index`, once about the prep-day origin — and a per-dish window
    gives it a third opportunity, now that two cards in one week can
    legitimately differ.

    They agree because they make the identical `week.fridge_day_gaps` call on
    the identical event, rather than each holding a copy of the threshold.
    """

    def test_both_read_one_function(self):
        import ui_state
        import inspect
        badge = inspect.getsource(ui_state.PlannerState.slot_views)
        note = inspect.getsource(planner.storage_note)
        self.assertIn("fridge_day_gaps(", badge)
        self.assertIn("fridge_day_gaps(", note)

    def test_the_note_flips_at_the_dishs_own_window(self):
        for storage_class, flips_at in (
            ("rice_or_pasta", 2), ("soup_stew_casserole", 4), (None, 2),
        ):
            below = planner.storage_note(6, flips_at - 1, storage_class=storage_class)
            at = planner.storage_note(6, flips_at, storage_class=storage_class)
            self.assertIn("refrigerate in airtight containers", below)
            self.assertIn("freeze the rest", at)


if __name__ == "__main__":
    unittest.main()

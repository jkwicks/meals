"""Tests for ingredient sourcing: what can actually be bought where this week
is cooked.

Written after a generated week called for mustard greens — not stocked by any
supermarket within reach of the configured `regional` postcode — and leaned on
fresh seafood a regional Victorian town can't reliably supply. Three
mechanisms answer that, and each is covered here because each failed
differently:

- `build_sourcing_rule` is the soft, unenumerable half (the long tail no
  blocklist can name), and must emit nothing at all when `sourcing` is absent.
- `build_seafood_limit_rule` caps seafood across the *week*, which no single
  generation call can see — `generate_week_plan` spends the cap in
  MEAL_TYPE_PRIORITY order and passes the remainder down, so the rule has to
  be correct at 0, part-spent, and fully-spent.
- `select_nudge_foods` was the actual culprit: whfoods.json ships "Mustard
  greens", "Halibut", "Scallops" and "Cod", and `build_slot_brief` names the
  sample in every slot's brief. It suggested cod while `banned_ingredients`
  forbade it. The filter is what makes `banned_ingredients` one lever rather
  than two.

`unittest` and the `sys.path` insert match `test_week_composition.py`; see its
docstring for why.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402

CONSTRAINED = {
    "regional": {"country": "AU", "state": "VIC"},
    "sourcing": {
        "supermarkets": ["Coles", "Woolworths", "Aldi"],
        "specialty_grocers_available_days": [],
        "fresh_seafood_available_days": [],
        "max_seafood_meals_per_week": 1,
    },
    "dietary_rules": {"allowed_nova_groups": [1, 2, 3], "banned_ingredients": []},
}
WEEKDAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def ingredient(name, protein_g):
    return planner.Ingredient(
        name=name, quantity_g=100.0, nova_group=1,
        calories=100.0, protein_g=protein_g, net_carbs_g=1.0, fat_g=1.0,
    )


def recipe(*ingredients, meal_type="dinner"):
    return planner.Recipe(
        name="Test dish", meal_type=meal_type, style="test", servings=1,
        ingredients=list(ingredients), instructions=["Cook it."],
        calories=100.0, protein_g=10.0, net_carbs_g=1.0, fat_g=1.0,
        prep_time_minutes=10,
    )


class TestBuildSourcingRule(unittest.TestCase):
    def test_absent_sourcing_emits_nothing(self):
        # A checkout whose schedule.json predates this section must produce a
        # byte-identical prompt to before it existed.
        self.assertEqual(planner.build_sourcing_rule({}, WEEKDAYS), "")

    def test_default_sourcing_emits_nothing(self):
        # Defaults mean "no constraint" — every day available, no cap.
        config = {"sourcing": planner.SourcingRules().model_dump()}
        self.assertEqual(planner.build_sourcing_rule(config, WEEKDAYS), "")

    def test_names_the_shops_and_the_region(self):
        rule = planner.build_sourcing_rule(CONSTRAINED, WEEKDAYS)
        self.assertIn("Coles, Woolworths or Aldi", rule)
        self.assertIn("VIC, AU", rule)

    def test_no_specialty_grocers_names_what_that_rules_out(self):
        rule = planner.build_sourcing_rule(CONSTRAINED, WEEKDAYS)
        self.assertIn("mustard greens", rule)
        self.assertIn("Asian grocer", rule)

    def test_asks_for_a_substitute_rather_than_a_prohibition(self):
        # The wording matters: "don't use it" invites the model to drop the
        # cuisine, or to name the item anyway because the dish needs one.
        rule = planner.build_sourcing_rule(CONSTRAINED, WEEKDAYS)
        self.assertIn("SUBSTITUTE", rule)

    def test_no_fresh_seafood_steers_to_tinned_and_frozen(self):
        rule = planner.build_sourcing_rule(CONSTRAINED, WEEKDAYS)
        self.assertIn("tinned", rule)
        self.assertIn("frozen", rule)

    def test_available_seafood_says_nothing_about_it(self):
        config = dict(
            CONSTRAINED,
            sourcing=dict(CONSTRAINED["sourcing"], fresh_seafood_available_days=None),
        )
        self.assertNotIn("tinned", planner.build_sourcing_rule(config, WEEKDAYS))

    def test_rule_folds_into_build_generation_rules(self):
        rules = planner.build_generation_rules(
            CONSTRAINED,
            days=WEEKDAYS,
            style_rule=planner.DAY_STYLE_RULE,
            variety_rule=planner.DAY_VARIETY_RULE,
            budget_rule=planner.DAY_BUDGET_RULE,
        )
        self.assertIn("Coles, Woolworths or Aldi", rules)


class TestSourcingDayGating(unittest.TestCase):
    """Sat/Sun-only sourcing (e.g. a weekend-only market) — the mixed case,
    where a call's days straddle both an open day and a restricted one."""

    MARKET_DAYS = {
        "regional": {"country": "AU", "state": "VIC"},
        "sourcing": {
            "supermarkets": ["Coles", "Woolworths", "Aldi"],
            "specialty_grocers_available_days": ["Saturday", "Sunday"],
            "fresh_seafood_available_days": ["Saturday", "Sunday"],
            "max_seafood_meals_per_week": 1,
        },
        "dietary_rules": {"allowed_nova_groups": [1, 2, 3], "banned_ingredients": []},
    }

    def test_a_fully_open_week_says_nothing_about_seafood_or_specialty(self):
        # Every day in scope is a market day, so there's nothing to restrict.
        rule = planner.build_sourcing_rule(self.MARKET_DAYS, ["Saturday", "Sunday"])
        self.assertNotIn("tinned", rule)
        self.assertNotIn("Asian grocer", rule)

    def test_a_fully_restricted_week_reads_like_the_unconditional_rule(self):
        # No open day in scope at all, e.g. a meal type only cooked weeknights.
        weeknights = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        rule = planner.build_sourcing_rule(self.MARKET_DAYS, weeknights)
        self.assertIn("tinned", rule)
        self.assertIn("Asian grocer", rule)
        # Unconditional wording, not the day-scoped "only reliably available on".
        self.assertNotIn("only reliably available", rule)

    def test_a_mixed_week_names_which_days_are_open(self):
        rule = planner.build_sourcing_rule(self.MARKET_DAYS, WEEKDAYS)
        self.assertIn("Saturday", rule)
        self.assertIn("Sunday", rule)
        self.assertIn("only reliably available", rule)
        # Still steers the restricted days to tinned/frozen.
        self.assertIn("tinned", rule)

    def test_a_mixed_week_names_which_days_are_restricted(self):
        rule = planner.build_sourcing_rule(self.MARKET_DAYS, WEEKDAYS)
        self.assertIn("Monday", rule)
        self.assertIn("Friday", rule)


class TestSeafoodLimitRule(unittest.TestCase):
    def test_no_cap_configured_emits_nothing(self):
        self.assertEqual(planner.build_seafood_limit_rule({}, "dinner", 7), "")

    def test_cap_at_or_above_the_slot_count_emits_nothing(self):
        # Nothing to say: the model couldn't exceed it if it tried.
        config = {"sourcing": {"max_seafood_meals_per_week": 7}}
        self.assertEqual(planner.build_seafood_limit_rule(config, "dinner", 7), "")

    def test_unspent_cap_states_the_remaining_allowance(self):
        rule = planner.build_seafood_limit_rule(CONSTRAINED, "dinner", 7, 0)
        self.assertIn("at most 1 of these 7 dinners", rule)

    def test_spent_cap_forbids_seafood_outright(self):
        rule = planner.build_seafood_limit_rule(CONSTRAINED, "lunch", 7, 1)
        self.assertIn("none of these 7 lunches", rule)
        self.assertIn("already taken", rule)

    def test_overspent_cap_does_not_go_negative(self):
        # A bulk-cooked stage can return more seafood than the cap allowed.
        rule = planner.build_seafood_limit_rule(CONSTRAINED, "lunch", 7, 4)
        self.assertIn("none of these", rule)
        # "at most -3" would be the failure; the leading "- " is the bullet.
        self.assertNotRegex(rule, r"-\d")

    def test_lunch_pluralises_correctly(self):
        rule = planner.build_seafood_limit_rule(CONSTRAINED, "lunch", 7, 1)
        self.assertNotIn("lunchs", rule)


class TestIsSeafoodMeal(unittest.TestCase):
    def test_dominant_seafood_protein_counts(self):
        self.assertTrue(
            planner.is_seafood_meal(
                recipe(ingredient("Salmon fillet", 25.0), ingredient("Rice", 3.0))
            )
        )

    def test_fish_sauce_does_not_count(self):
        # The whole reason this reads the highest-protein ingredient rather
        # than scanning every name: a tablespoon of fish sauce must not spend
        # the week's seafood allowance on a chicken dinner.
        self.assertFalse(
            planner.is_seafood_meal(
                recipe(ingredient("Chicken thigh", 26.0), ingredient("Fish sauce", 0.5))
            )
        )

    def test_applies_to_breakfast_too(self):
        # Unlike extract_main_protein, which is lunch/dinner only — a
        # smoked-salmon breakfast is a trip to the same counter.
        self.assertTrue(
            planner.is_seafood_meal(
                recipe(ingredient("Smoked salmon", 22.0), ingredient("Rye toast", 4.0),
                       meal_type="breakfast")
            )
        )

    def test_no_ingredients_is_not_seafood(self):
        self.assertFalse(planner.is_seafood_meal(recipe()))


class TestBuildRejectionRule(unittest.TestCase):
    """`build_rejection_rule` — the soft-guidance line rejection capture
    contributes to `build_generation_rules`, "beside banned_ingredients and
    diet-style principles" per CLAUDE.md's "Rejection capture"."""

    def test_no_rejections_emits_nothing(self):
        # A fresh checkout (or one that's never had a regenerate answered)
        # must produce a byte-identical prompt to before this feature existed.
        self.assertEqual(planner.build_rejection_rule({}), "")
        self.assertEqual(planner.build_rejection_rule({"rejected_preferences": []}), "")

    def test_one_rejection_names_the_dish_and_reason(self):
        config = {
            "rejected_preferences": [
                {
                    "date": "2026-08-20",
                    "slot_id": "Monday:dinner",
                    "recipe_name": "Slow Cooker Beef Cheeks",
                    "reason": "too_much_prep",
                    "marked_at": "2026-08-20T18:00:00+00:00",
                }
            ]
        }
        rule = planner.build_rejection_rule(config)
        self.assertIn("Slow Cooker Beef Cheeks", rule)
        self.assertIn("too much prep", rule)

    def test_several_rejections_all_appear(self):
        config = {
            "rejected_preferences": [
                {
                    "date": "2026-08-20", "slot_id": "Monday:dinner",
                    "recipe_name": "Dish A", "reason": "too_much_prep",
                    "marked_at": "2026-08-20T18:00:00+00:00",
                },
                {
                    "date": "2026-08-21", "slot_id": "Tuesday:lunch",
                    "recipe_name": "Dish B", "reason": "had_it_recently",
                    "marked_at": "2026-08-21T13:00:00+00:00",
                },
            ]
        }
        rule = planner.build_rejection_rule(config)
        self.assertIn("Dish A", rule)
        self.assertIn("Dish B", rule)
        self.assertIn("had it recently", rule)

    def test_reason_labels_match_the_ui_chip_wording(self):
        # planner.REJECTION_REASON_LABELS is what both the prompt rule and
        # ui_generation.py's chip buttons read from — the two must not name
        # the same reason differently.
        self.assertEqual(
            set(planner.RejectionEntry.model_fields["reason"].annotation.__args__),
            set(planner.REJECTION_REASON_LABELS),
        )

    def test_placed_beside_diet_style_in_the_assembled_rules(self):
        config = {
            "dietary_rules": {"allowed_nova_groups": [1, 2, 3], "banned_ingredients": []},
            "diet_styles": {},
            "rejected_preferences": [
                {
                    "date": "2026-08-20", "slot_id": "Monday:dinner",
                    "recipe_name": "Dish A", "reason": "wrong_for_slot",
                    "marked_at": "2026-08-20T18:00:00+00:00",
                },
            ],
        }
        rules = planner.build_generation_rules(
            config, days=WEEKDAYS, style_rule="", variety_rule="", budget_rule="",
        )
        self.assertIn("Dish A", rules)


class TestRejectionStorage(unittest.TestCase):
    """`LocalJSONRepository.save_rejection_entry`/`load_rejections` — an
    append-only event log, not upsert-by-date like biometrics (two
    rejections can land on the same slot on the same day)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalJSONRepository(data_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, recipe_name: str, reason: str) -> dict:
        return planner.RejectionEntry(
            date="2026-08-20",
            slot_id="Monday:dinner",
            recipe_name=recipe_name,
            reason=reason,
            marked_at="2026-08-20T18:00:00+00:00",
        ).model_dump()

    def test_absent_file_loads_empty(self):
        self.assertEqual(run_sync(self.repo.load_rejections()), [])

    def test_a_saved_entry_round_trips(self):
        run_sync(self.repo.save_rejection_entry(self.entry("Dish A", "too_much_prep")))
        loaded = run_sync(self.repo.load_rejections())
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["recipe_name"], "Dish A")

    def test_two_rejections_on_the_same_slot_both_survive(self):
        # Unlike a weigh-in, a rejection is an event, not a fact keyed by
        # date — regenerating the same slot twice must record twice, not
        # overwrite.
        run_sync(self.repo.save_rejection_entry(self.entry("Dish A", "too_much_prep")))
        run_sync(self.repo.save_rejection_entry(self.entry("Dish B", "dont_fancy_it")))
        loaded = run_sync(self.repo.load_rejections())
        self.assertEqual([e["recipe_name"] for e in loaded], ["Dish A", "Dish B"])


class FakeWhfoodsRepository:
    """Stands in for LocalJSONRepository at the one seam select_nudge_foods
    uses, so nothing here touches reference/whfoods.json."""

    def __init__(self, foods):
        self._foods = foods

    async def load_whfoods(self):
        return list(self._foods)


class TestNudgeFoodsRespectBans(unittest.TestCase):
    CORPUS = ["Broccoli", "Mustard greens", "Cod", "Halibut", "Eggs", "Lentils"]

    def test_banned_foods_are_never_suggested(self):
        config = {"dietary_rules": {"banned_ingredients": ["mustard greens", "cod"]}}
        picked = run_sync(
            planner.select_nudge_foods(FakeWhfoodsRepository(self.CORPUS), config, count=6)
        )
        self.assertNotIn("Mustard greens", picked)
        self.assertNotIn("Cod", picked)
        self.assertIn("Broccoli", picked)

    def test_matching_is_case_insensitive_substring(self):
        # Same semantics as Ingredient.reject_banned_ingredients, so the two
        # can't disagree about whether an item is banned.
        config = {"dietary_rules": {"banned_ingredients": ["GREENS"]}}
        picked = run_sync(
            planner.select_nudge_foods(FakeWhfoodsRepository(self.CORPUS), config, count=6)
        )
        self.assertNotIn("Mustard greens", picked)

    def test_no_config_leaves_the_corpus_alone(self):
        picked = run_sync(
            planner.select_nudge_foods(FakeWhfoodsRepository(self.CORPUS), count=6)
        )
        self.assertEqual(sorted(picked), sorted(self.CORPUS))

    def test_empty_corpus_still_resolves_to_nothing(self):
        picked = run_sync(planner.select_nudge_foods(FakeWhfoodsRepository([]), {}))
        self.assertEqual(picked, [])


class TestRealConfig(unittest.TestCase):
    """The shipped config must satisfy its own schema and the corpus filter."""

    def setUp(self):
        self.config = planner.load_app_config(run_sync(LocalJSONRepository().load_config()))

    def test_sourcing_loads_and_validates(self):
        self.assertIn("sourcing", self.config)

    def test_shipped_corpus_suggests_nothing_the_config_bans(self):
        # The regression itself: whfoods.json names Cod, Halibut, Scallops and
        # Mustard greens, all of which this config bans.
        foods = run_sync(LocalJSONRepository().load_whfoods())
        banned = [b.lower() for b in self.config["dietary_rules"]["banned_ingredients"]]
        picked = run_sync(
            planner.select_nudge_foods(config=self.config, count=len(foods))
        )
        for food in picked:
            for term in banned:
                self.assertNotIn(term, food.lower(), f"{food} is banned by '{term}'")


if __name__ == "__main__":
    unittest.main()

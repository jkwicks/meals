"""Tests for `week_shape` — design-02's declarative replacement for the
bulk-prep/long-cook toggles: the schema (`WeekShape`/`BatchDeclaration`/
`FreezerDrawDeclaration` on `AppConfig`, owned by `week.json`) and the one
shared validator, `planner.week_shape_errors`, that both the loader and the
(later) preset editor run.

Four layers, each covered separately:

- `WeekShapeErrorsTests` calls the pure function directly with hand-built
  config dicts — one test per rule in design-02 §7, named after the rule.
- `AppConfigWeekShapeTests` confirms the same function is actually wired into
  `AppConfig` (a real load fails on the same shapes, absence is byte-
  identical to an explicit empty list).
- `WeekShapeArrivesFromAPresetTests` confirms the standard leaf-path resolver
  needs no one-off exception for this key — the same claim
  `TestADayScopedDietStyleArrivesFromAPreset` makes for
  `active_diet_styles` in `test_presets.py`.
- `ApplyWeekShapeTests` (Task 1.2c) covers the literal applier —
  `week.apply_week_shape` — that turns an already-*coherent* shape (the
  layer above never re-runs `week_shape_errors`) into grid edits: literal
  anchoring on `serves[0]`, `spread_batch`'s own linking half for the rest,
  `resolve_freezer_draws` for the freezer half, and a warning rather than a
  search whenever a real `WeekSpec` disagrees with an abstractly-coherent
  declaration. `PrepDayBatchSlotIdsFromWeekShapeTests` covers the one other
  change this task makes, generalising `prep_day_batch_slot_ids` so a
  declarative batch's prep-day anchor is discoverable the same way the
  legacy two-toggle ones always were, with no new config key to merge in.

`unittest` and the `sys.path` insert match `test_week_composition.py`.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402
import week as wk  # noqa: E402
from planner import week_shape_errors  # noqa: E402
from repository import CONFIG_KEY_OWNER, LocalJSONRepository, run_sync  # noqa: E402
from week import (  # noqa: E402
    LINK_ORIGIN_BATCH,
    LINK_ORIGIN_FREEZER,
    LINK_ORIGIN_LOCATION,
    LINK_ORIGIN_USER,
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    PrepDayResolution,
    SlotSpec,
    WeekSpec,
    apply_week_shape,
    prep_day_batch_slot_ids,
    resolve_prep_day,
    slot_id,
)

WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# No prep-day restriction anywhere -> resolve_prep_day always lands on the
# day before the week starts, exactly as the shipped config does.
OPEN_PREP_DAY = PrepDayResolution(day="Sunday")

NO_PREP_DAY = PrepDayResolution(
    day=None, reason="No prep day: neither Sunday nor Saturday has the hours."
)


def config_for(week_shape, *, base_schedule=None, location_rules=None, meal_types=None):
    """The minimal dict `week_shape_errors` needs — `week_days`, `meal_types`
    and `location_mode` are its only readers, so nothing about macros or
    `DaySchedule` shape belongs here."""
    return {
        "week_shape": week_shape,
        "weekly_schedule": {day: {} for day in WEEK},
        "week_start_day": "Monday",
        "meal_types": meal_types or ["breakfast", "lunch", "dinner", "snack"],
        "base_schedule": base_schedule or {},
        "location_rules": location_rules or {},
    }


def batch(**fields):
    declared = {
        "name": "bulk-prep",
        "meal_type": "lunch",
        "cook_on": "prep_day",
        "serves": ["Monday", "Tuesday"],
        "freeze_portions": 0,
    }
    declared.update(fields)
    return declared


class WeekShapeErrorsTests(unittest.TestCase):
    """One test per design-02 §7 rule, plus the two full-shape baselines."""

    def test_an_empty_shape_is_clean(self):
        self.assertEqual(
            week_shape_errors(config_for({"batches": [], "freezer_draws": []}), OPEN_PREP_DAY),
            [],
        )

    def test_the_shipped_migration_shape_is_clean(self):
        # design-02 §9's worked example: two prep-day batches, both serving
        # Monday-Wednesday, at the shipped 4-day-gap fridge default.
        shape = {
            "batches": [
                batch(name="bulk-prep", meal_type="lunch",
                      serves=["Monday", "Tuesday", "Wednesday"]),
                batch(name="long-cook", meal_type="dinner",
                      serves=["Monday", "Tuesday", "Wednesday"]),
            ],
            "freezer_draws": [],
        }
        self.assertEqual(week_shape_errors(config_for(shape), OPEN_PREP_DAY), [])

    def test_a_same_week_cook_on_batch_is_clean(self):
        shape = {
            "batches": [batch(cook_on="Wednesday", serves=["Wednesday", "Thursday"])],
            "freezer_draws": [],
        }
        self.assertEqual(week_shape_errors(config_for(shape), OPEN_PREP_DAY), [])

    def test_a_lone_freezer_draw_is_clean(self):
        # No stock is ever consulted here — an unsatisfiable draw is a
        # generation-time warning (`week.resolve_freezer_draws`), never a
        # load error, so a schema-valid draw always passes this layer.
        shape = {"batches": [], "freezer_draws": [{"meal_type": "lunch", "day": "Thursday"}]}
        self.assertEqual(week_shape_errors(config_for(shape), OPEN_PREP_DAY), [])

    def test_an_empty_batch_name_is_rejected(self):
        shape = {"batches": [batch(name="")], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("non-empty name" in error for error in errors), errors)

    def test_duplicate_batch_names_are_rejected(self):
        shape = {
            "batches": [batch(name="dup"), batch(name="dup", meal_type="dinner")],
            "freezer_draws": [],
        }
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("unique" in error for error in errors), errors)

    def test_an_unknown_meal_type_is_rejected(self):
        shape = {"batches": [batch(meal_type="brunch")], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("meal_type" in error for error in errors), errors)

    def test_an_unknown_weekday_in_serves_is_rejected(self):
        shape = {"batches": [batch(serves=["Frday", "Tuesday"])], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("unknown weekday" in error for error in errors), errors)

    def test_empty_serves_is_rejected(self):
        shape = {"batches": [batch(serves=[])], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("at least one day" in error for error in errors), errors)

    def test_a_duplicate_day_in_serves_is_rejected(self):
        shape = {"batches": [batch(serves=["Monday", "Monday"])], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("more than once" in error for error in errors), errors)

    def test_serves_out_of_week_order_is_rejected(self):
        shape = {"batches": [batch(serves=["Tuesday", "Monday"])], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("week order" in error for error in errors), errors)

    def test_a_gap_in_serves_is_rejected_as_a_missing_freezer_draw(self):
        shape = {"batches": [batch(serves=["Monday", "Wednesday"])], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("gap" in error and "freezer draw" in error for error in errors), errors)

    def test_cook_on_must_equal_serves_first_day(self):
        shape = {
            "batches": [batch(cook_on="Tuesday", serves=["Monday", "Tuesday"])],
            "freezer_draws": [],
        }
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("anchors on the first slot" in error for error in errors), errors)

    def test_a_nonsense_cook_on_is_rejected(self):
        shape = {"batches": [batch(cook_on="Someday")], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(
            any("neither 'prep_day' nor a day this week plans" in error for error in errors),
            errors,
        )

    def test_prep_day_cook_on_needs_a_resolved_prep_day(self):
        shape = {"batches": [batch(cook_on="prep_day")], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), NO_PREP_DAY)
        self.assertTrue(any("this week has none" in error for error in errors), errors)
        self.assertTrue(any(NO_PREP_DAY.reason in error for error in errors), errors)

    def test_a_prep_day_batch_may_not_serve_the_final_grid_day(self):
        shape = {
            "batches": [batch(serves=["Saturday", "Sunday"])],
            "freezer_draws": [],
        }
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("final day" in error for error in errors), errors)

    def test_a_same_week_batch_may_serve_the_final_grid_day(self):
        # The final-day exclusion is prep-day-specific (design-02 §5's
        # `exclude_target_days`) — an ordinary "cooked Saturday, eaten Sunday"
        # chain is perfectly legal.
        shape = {
            "batches": [batch(cook_on="Saturday", serves=["Saturday", "Sunday"])],
            "freezer_draws": [],
        }
        self.assertEqual(week_shape_errors(config_for(shape), OPEN_PREP_DAY), [])

    def test_past_the_fridge_window_is_rejected(self):
        # From prep day, Thursday is 4 day-gaps out (the shipped default's
        # limit) and Friday is 5 — past it.
        shape = {
            "batches": [batch(serves=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])],
            "freezer_draws": [],
        }
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(
            any("past the" in error and "fridge window" in error for error in errors), errors
        )

    def test_exactly_at_the_fridge_window_is_clean(self):
        shape = {
            "batches": [batch(serves=["Monday", "Tuesday", "Wednesday", "Thursday"])],
            "freezer_draws": [],
        }
        self.assertEqual(week_shape_errors(config_for(shape), OPEN_PREP_DAY), [])

    def test_location_skipping_the_meal_blocks_the_batch(self):
        shape = {"batches": [batch(serves=["Monday", "Tuesday"], meal_type="lunch")],
                  "freezer_draws": []}
        config = config_for(
            shape,
            base_schedule={"Monday": "Holiday"},
            location_rules={"Holiday": {"lunch_mode": "skip"}},
        )
        errors = week_shape_errors(config, OPEN_PREP_DAY)
        self.assertTrue(any("location rules skip" in error for error in errors), errors)

    def test_two_batches_claiming_one_slot_is_rejected(self):
        shape = {
            "batches": [
                batch(name="a", meal_type="lunch", serves=["Monday", "Tuesday"]),
                batch(name="b", meal_type="lunch", serves=["Tuesday", "Wednesday"]),
            ],
            "freezer_draws": [],
        }
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("already claimed by" in error for error in errors), errors)

    def test_a_batch_and_a_draw_claiming_one_slot_is_rejected(self):
        shape = {
            "batches": [batch(name="a", meal_type="lunch", serves=["Monday", "Tuesday"])],
            "freezer_draws": [{"meal_type": "lunch", "day": "Tuesday"}],
        }
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("already claimed by" in error for error in errors), errors)

    def test_a_freezer_draw_with_an_unknown_meal_type_is_rejected(self):
        shape = {"batches": [], "freezer_draws": [{"meal_type": "brunch", "day": "Thursday"}]}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("meal_type" in error for error in errors), errors)

    def test_a_freezer_draw_with_an_unknown_day_is_rejected(self):
        shape = {"batches": [], "freezer_draws": [{"meal_type": "lunch", "day": "Frday"}]}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("weekday name" in error for error in errors), errors)

    def test_errors_name_the_batch(self):
        shape = {"batches": [batch(name="sunday-soup", meal_type="brunch")], "freezer_draws": []}
        errors = week_shape_errors(config_for(shape), OPEN_PREP_DAY)
        self.assertTrue(any("sunday-soup" in error for error in errors), errors)

    def test_resolve_prep_day_is_the_real_input(self):
        """The function takes `prep_day` rather than recomputing it, but the
        two must agree — `resolve_prep_day` over a week with no prep-day
        restriction is exactly `OPEN_PREP_DAY` above."""
        resolved = resolve_prep_day(WEEK, config_for({"batches": [], "freezer_draws": []}))
        self.assertEqual(resolved.day, OPEN_PREP_DAY.day)


class AppConfigWeekShapeTests(unittest.TestCase):
    """`week_shape_is_coherent` is really wired into `AppConfig.model_validate`,
    not just a free function nobody calls."""

    def base(self, **overrides):
        raw = {
            "weekly_schedule": {
                day: {"calories": 2000, "protein_g": 150, "net_carbs_g": 150, "fat_g": 60}
                for day in WEEK
            },
        }
        raw.update(overrides)
        return raw

    def test_week_shape_defaults_to_empty_when_absent(self):
        config = planner.load_app_config(self.base())
        self.assertEqual(config["week_shape"], {"batches": [], "freezer_draws": []})

    def test_an_explicitly_empty_week_shape_is_byte_identical_to_absent(self):
        with_key = planner.load_app_config(
            self.base(week_shape={"batches": [], "freezer_draws": []})
        )
        without_key = planner.load_app_config(self.base())
        self.assertEqual(with_key, without_key)

    def test_a_valid_week_shape_loads(self):
        config = planner.load_app_config(
            self.base(week_shape={
                "batches": [batch(serves=["Monday", "Tuesday", "Wednesday"])],
                "freezer_draws": [],
            })
        )
        self.assertEqual(len(config["week_shape"]["batches"]), 1)

    def test_an_incoherent_week_shape_fails_load(self):
        with self.assertRaises(ValueError) as caught:
            planner.load_app_config(
                self.base(week_shape={"batches": [batch(meal_type="brunch")], "freezer_draws": []})
            )
        self.assertIn("week_shape", str(caught.exception))
        self.assertIn("meal_type", str(caught.exception))

    def test_a_negative_surplus_fails_pydantic_directly(self):
        with self.assertRaises(Exception):
            planner.load_app_config(
                self.base(
                    week_shape={"batches": [batch(freeze_portions=-1)], "freezer_draws": []}
                )
            )

    def test_an_unknown_batch_key_is_forbidden(self):
        with self.assertRaises(Exception):
            planner.load_app_config(
                self.base(
                    week_shape={
                        "batches": [{**batch(), "prefer_days": ["Monday"]}],
                        "freezer_draws": [],
                    }
                )
            )


BASE = None  # set in setUpModule


def setUpModule():
    global BASE
    BASE = run_sync(LocalJSONRepository().load_config())


def preset_file(active, **overrides_by_name):
    return {
        "active": active,
        "presets": {
            name: {"label": name.title(), "overrides": overrides}
            for name, overrides in overrides_by_name.items()
        },
    }


class WeekShapeArrivesFromAPresetTests(unittest.TestCase):
    """`week_shape` is owned by `week.json` (`CONFIG_FILES`) and reached by
    the same leaf-path resolver every other key goes through — no special
    case in `presets.py` for it, mirroring
    `TestADayScopedDietStyleArrivesFromAPreset` in `test_presets.py`."""

    def test_week_shape_is_owned_by_week_json(self):
        self.assertEqual(CONFIG_KEY_OWNER["week_shape"], "week.json")

    def test_a_whole_week_shape_override_survives_the_layer(self):
        layered = planner.apply_preset_layer(
            BASE,
            preset_file("fast", fast={
                "week_shape": {
                    "batches": [batch(serves=["Monday", "Tuesday", "Wednesday"])],
                    "freezer_draws": [],
                },
            }),
        )
        self.assertEqual(len(layered["week_shape"]["batches"]), 1)
        self.assertEqual(layered["week_shape"]["batches"][0]["name"], "bulk-prep")

    def test_a_leaf_beneath_week_shape_survives_the_layer(self):
        layered = planner.apply_preset_layer(
            BASE,
            preset_file("fast", fast={
                "week_shape.batches": [batch(serves=["Monday", "Tuesday", "Wednesday"])],
            }),
        )
        self.assertEqual(len(layered["week_shape"]["batches"]), 1)
        self.assertEqual(layered["week_shape"]["freezer_draws"], [])

    def test_a_preset_that_breaks_week_shape_fails_the_layer_named(self):
        with self.assertRaises(ValueError) as caught:
            planner.apply_preset_layer(
                BASE,
                preset_file("broken", broken={
                    "week_shape.batches": [batch(meal_type="brunch")],
                }),
            )
        self.assertIn("broken", str(caught.exception))
        self.assertIn("meal_type", str(caught.exception))


MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]


def spec_with(modes=None, servings_per_meal=2) -> WeekSpec:
    """A full 7x4 grid of plain cook slots, `modes` overriding named slot ids
    — the `test_week_mechanics.spec_with` shape, kept local so this module
    doesn't reach across a test-to-test import for it."""
    modes = modes or {}
    slots = []
    for day in WEEK:
        for meal_type in MEAL_TYPES:
            sid = slot_id(day, meal_type)
            override = modes.get(sid, {})
            slots.append(
                SlotSpec(
                    day=day,
                    meal_type=meal_type,
                    mode=override.get("mode", MODE_COOK),
                    source=override.get("source"),
                    recipe_id=override.get("recipe_id"),
                    extra_portions=override.get("extra_portions", 0),
                    link_origin=override.get("link_origin", LINK_ORIGIN_USER),
                )
            )
    return WeekSpec(days=WEEK, slots=slots, servings_per_meal=servings_per_meal)


def draw(meal_type="lunch", day="Thursday"):
    return {"meal_type": meal_type, "day": day}


def freezer_item(**overrides):
    fields = dict(
        id="lot-a",
        label="beef massaman",
        portions=4,
        cooked_on="2026-08-01",
        frozen_on="2026-08-01",
        storage_class="soup_stew_casserole",
        per_serving={"calories": 450.0, "protein_g": 30.0, "net_carbs_g": 20.0, "fat_g": 15.0},
        recipe_id="recipe-123",
    )
    fields.update(overrides)
    return fields


class ApplyWeekShapeTests(unittest.TestCase):
    """`apply_week_shape` — Task 1.2c. Every case here hands it a shape that
    `WeekShapeErrorsTests` above would call clean; what's under test is the
    *application*, not a second pass at coherence."""

    def test_a_batch_anchors_on_its_first_served_day(self):
        shape = {"batches": [batch(serves=["Monday", "Tuesday", "Wednesday"])], "freezer_draws": []}
        result = apply_week_shape(spec_with(), shape, config_for(shape), OPEN_PREP_DAY, [])
        self.assertEqual(result.batch_anchors, {"bulk-prep": "Monday:lunch"})
        self.assertEqual(result.spec.by_id()["Monday:lunch"].mode, MODE_COOK)

    def test_the_remaining_served_days_are_linked_with_batch_provenance(self):
        shape = {"batches": [batch(serves=["Monday", "Tuesday", "Wednesday"])], "freezer_draws": []}
        result = apply_week_shape(spec_with(), shape, config_for(shape), OPEN_PREP_DAY, [])
        for day in ("Tuesday", "Wednesday"):
            slot = result.spec.by_id()[f"{day}:lunch"]
            self.assertEqual(slot.mode, MODE_LEFTOVER)
            self.assertEqual(slot.source, "Monday:lunch")
            self.assertEqual(slot.link_origin, LINK_ORIGIN_BATCH)

    def test_a_day_the_batch_does_not_serve_is_untouched(self):
        shape = {"batches": [batch(serves=["Monday", "Tuesday"])], "freezer_draws": []}
        result = apply_week_shape(spec_with(), shape, config_for(shape), OPEN_PREP_DAY, [])
        self.assertEqual(result.spec.by_id()["Thursday:lunch"].mode, MODE_COOK)
        self.assertEqual(result.warnings, [])

    def test_freeze_portions_lands_on_the_anchors_extra_portions_only(self):
        shape = {
            "batches": [batch(serves=["Monday", "Tuesday"], freeze_portions=6)],
            "freezer_draws": [],
        }
        result = apply_week_shape(spec_with(), shape, config_for(shape), OPEN_PREP_DAY, [])
        self.assertEqual(result.spec.by_id()["Monday:lunch"].extra_portions, 6)
        self.assertEqual(result.spec.by_id()["Tuesday:lunch"].extra_portions, 0)

    def test_the_shipped_migration_shape_applies_with_no_warnings(self):
        shape = {
            "batches": [
                batch(name="bulk-prep", meal_type="lunch",
                      serves=["Monday", "Tuesday", "Wednesday"]),
                batch(name="long-cook", meal_type="dinner",
                      serves=["Monday", "Tuesday", "Wednesday"]),
            ],
            "freezer_draws": [],
        }
        result = apply_week_shape(spec_with(), shape, config_for(shape), OPEN_PREP_DAY, [])
        self.assertEqual(result.warnings, [])
        self.assertEqual(
            result.batch_anchors, {"bulk-prep": "Monday:lunch", "long-cook": "Monday:dinner"}
        )
        # Three slots claim each anchor (itself plus the two it feeds) —
        # portions_for is untouched by where the links came from.
        self.assertEqual(wk.portions_for(result.spec)["Monday:lunch"], 6)
        self.assertEqual(wk.portions_for(result.spec)["Monday:dinner"], 6)

    def test_an_anchor_thats_not_a_cook_slot_strands_the_whole_batch(self):
        """No re-anchoring: a batch whose first served day isn't cooking on
        this grid is skipped outright, never moved to a day that would work."""
        spec = spec_with({"Monday:lunch": {"mode": MODE_SKIP}})
        shape = {"batches": [batch(serves=["Monday", "Tuesday"])], "freezer_draws": []}
        result = apply_week_shape(spec, shape, config_for(shape), OPEN_PREP_DAY, [])
        self.assertIsNone(result.batch_anchors["bulk-prep"])
        self.assertTrue(any("skipped" in w for w in result.warnings), result.warnings)
        self.assertEqual(result.spec.by_id()["Tuesday:lunch"].mode, MODE_COOK)

    def test_a_location_forced_leftover_on_the_anchor_day_also_strands_it(self):
        """design-02 §8: location wins on facts — extended to the anchor
        itself, not only to a target `_claimable` might re-point."""
        spec = spec_with({
            "Monday:lunch": {
                "mode": MODE_LEFTOVER, "source": "placeholder", "link_origin": LINK_ORIGIN_LOCATION,
            },
        })
        shape = {"batches": [batch(serves=["Monday", "Tuesday"])], "freezer_draws": []}
        result = apply_week_shape(spec, shape, config_for(shape), OPEN_PREP_DAY, [])
        self.assertIsNone(result.batch_anchors["bulk-prep"])
        self.assertEqual(result.spec.by_id()["Monday:lunch"].source, "placeholder")

    def test_a_user_made_link_on_a_served_day_is_left_alone(self):
        spec = spec_with({
            "Tuesday:lunch": {
                "mode": MODE_LEFTOVER, "source": "Monday:dinner", "link_origin": LINK_ORIGIN_USER,
            },
        })
        shape = {"batches": [batch(serves=["Monday", "Tuesday"])], "freezer_draws": []}
        result = apply_week_shape(spec, shape, config_for(shape), OPEN_PREP_DAY, [])
        tuesday = result.spec.by_id()["Tuesday:lunch"]
        self.assertEqual(tuesday.source, "Monday:dinner")
        self.assertEqual(tuesday.link_origin, LINK_ORIGIN_USER)
        self.assertTrue(any("already claimed" in w for w in result.warnings), result.warnings)

    def test_a_prep_day_batch_is_skipped_when_this_week_resolved_none(self):
        shape = {
            "batches": [batch(cook_on="prep_day", serves=["Monday", "Tuesday"])],
            "freezer_draws": [],
        }
        result = apply_week_shape(spec_with(), shape, config_for(shape), NO_PREP_DAY, [])
        self.assertIsNone(result.batch_anchors["bulk-prep"])
        self.assertTrue(any(NO_PREP_DAY.reason in w for w in result.warnings), result.warnings)
        self.assertEqual(result.spec.by_id()["Tuesday:lunch"].mode, MODE_COOK)

    def test_a_same_week_cook_on_batch_needs_no_prep_day_at_all(self):
        shape = {
            "batches": [batch(cook_on="Wednesday", serves=["Wednesday", "Thursday"])],
            "freezer_draws": [],
        }
        result = apply_week_shape(spec_with(), shape, config_for(shape), NO_PREP_DAY, [])
        self.assertEqual(result.batch_anchors["bulk-prep"], "Wednesday:lunch")
        self.assertEqual(result.warnings, [])

    def test_a_freezer_draw_is_resolved_through_the_shared_resolver(self):
        shape = {"batches": [], "freezer_draws": [draw(meal_type="lunch", day="Thursday")]}
        lot = freezer_item()
        result = apply_week_shape(spec_with(), shape, config_for(shape), OPEN_PREP_DAY, [lot])
        self.assertEqual(len(result.selected_lots), 1)
        self.assertEqual(result.selected_lots[0].item, lot)
        self.assertEqual(result.selected_lots[0].slot_id, "Thursday:lunch")
        thursday = result.spec.by_id()["Thursday:lunch"]
        self.assertEqual(thursday.mode, MODE_LEFTOVER)
        self.assertEqual(thursday.source, "lot-a")
        self.assertEqual(thursday.link_origin, LINK_ORIGIN_FREEZER)
        self.assertEqual(result.warnings, [])

    def test_an_unsatisfiable_freezer_draw_warns_and_leaves_the_slot_cooking(self):
        shape = {"batches": [], "freezer_draws": [draw()]}
        result = apply_week_shape(spec_with(), shape, config_for(shape), OPEN_PREP_DAY, [])
        self.assertEqual(result.selected_lots, [])
        self.assertEqual(result.spec.by_id()["Thursday:lunch"].mode, MODE_COOK)
        self.assertTrue(any("nothing is declared" in w for w in result.warnings), result.warnings)

    def test_a_batch_and_a_draw_do_not_interfere(self):
        shape = {
            "batches": [batch(serves=["Monday", "Tuesday"])],
            "freezer_draws": [draw(meal_type="lunch", day="Thursday")],
        }
        result = apply_week_shape(
            spec_with(), shape, config_for(shape), OPEN_PREP_DAY, [freezer_item()]
        )
        self.assertEqual(result.batch_anchors["bulk-prep"], "Monday:lunch")
        self.assertEqual(len(result.selected_lots), 1)
        self.assertEqual(result.warnings, [])

    def test_the_same_shape_applied_twice_to_a_fresh_spec_is_byte_identical(self):
        shape = {
            "batches": [
                batch(name="bulk-prep", meal_type="lunch",
                      serves=["Monday", "Tuesday", "Wednesday"]),
                batch(name="long-cook", meal_type="dinner",
                      serves=["Monday", "Tuesday", "Wednesday"], freeze_portions=4),
            ],
            "freezer_draws": [draw(meal_type="lunch", day="Thursday")],
        }
        config = config_for(shape)
        first = apply_week_shape(spec_with(), shape, config, OPEN_PREP_DAY, [freezer_item()])
        second = apply_week_shape(spec_with(), shape, config, OPEN_PREP_DAY, [freezer_item()])
        self.assertEqual(first.spec, second.spec)
        self.assertEqual(first.batch_anchors, second.batch_anchors)
        self.assertEqual(first.warnings, second.warnings)

    def test_a_previous_runs_anchor_cannot_freeze_the_next_run(self):
        """Unlike `spread_batch`, this applier never counts what a slot
        already claims — the anchor is always `serves[0]`, so a stale grid
        fed back in (a caller that forgot to reset it) still names the same
        anchor rather than drifting to whatever `spread_batch`'s own
        claim-counting would have frozen in place."""
        shape = {"batches": [batch(serves=["Monday", "Tuesday", "Wednesday"])], "freezer_draws": []}
        config = config_for(shape)
        first = apply_week_shape(spec_with(), shape, config, OPEN_PREP_DAY, [])
        second = apply_week_shape(first.spec, shape, config, OPEN_PREP_DAY, [])
        self.assertEqual(first.batch_anchors, second.batch_anchors)
        self.assertEqual(second.batch_anchors["bulk-prep"], "Monday:lunch")


class PrepDayBatchSlotIdsFromWeekShapeTests(unittest.TestCase):
    """`prep_day_batch_slot_ids` generalised (Task 1.2c) to read a
    declarative batch's own `cook_on`/`serves` — the "same anchor data" the
    legacy `long_cook_anchor`/`bulk_prep_anchor` config keys already fed it —
    so a declarative prep-day batch needs no third config key merged in."""

    def test_a_prep_day_batch_is_discoverable_straight_off_week_shape(self):
        shape = {
            "batches": [batch(cook_on="prep_day", meal_type="dinner", serves=["Monday", "Tuesday"])],
            "freezer_draws": [],
        }
        self.assertEqual(prep_day_batch_slot_ids({"week_shape": shape}), {"Monday:dinner"})

    def test_a_same_week_cook_on_batch_does_not_count_as_prepped_ahead(self):
        shape = {
            "batches": [batch(cook_on="Wednesday", serves=["Wednesday", "Thursday"])],
            "freezer_draws": [],
        }
        self.assertEqual(prep_day_batch_slot_ids({"week_shape": shape}), set())

    def test_legacy_and_declarative_anchors_combine(self):
        shape = {
            "batches": [batch(cook_on="prep_day", meal_type="dinner", serves=["Tuesday"])],
            "freezer_draws": [],
        }
        self.assertEqual(
            prep_day_batch_slot_ids({"long_cook_anchor": "Monday:dinner", "week_shape": shape}),
            {"Monday:dinner", "Tuesday:dinner"},
        )

    def test_no_week_shape_at_all_is_unaffected(self):
        self.assertEqual(prep_day_batch_slot_ids({}), set())
        self.assertEqual(prep_day_batch_slot_ids(None), set())


if __name__ == "__main__":
    unittest.main()

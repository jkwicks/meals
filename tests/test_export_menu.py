"""Tests for `src/export_menu.py`'s text pipeline.

`format_week_menu_markdown` has no button behind it — it is kept as the text
counterpart to the PDF (diffing two weeks, pasting a menu into a note), and is
the reason `_slot_entry` is factored out of the PDF builder at all, so the two
formats cannot silently disagree about what a slot says.

That makes it exactly the kind of function that rots: no caller to break, and
until now no test either. These cover the per-slot walk both formats share —
cook, leftover, skip and not-generated — so a change to `WeekPlan`'s shape
fails here rather than the next time someone reaches for the Markdown export.

The PDF builder itself is deliberately not tested: asserting on reportlab's
byte output would pin the layout rather than the content, and the content is
`_slot_entry`, which is covered below.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import export_menu  # noqa: E402
from planner import CookEvent, Ingredient, Recipe, WeekPlan  # noqa: E402
from shopping import matcher_name  # noqa: E402
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, slot_id  # noqa: E402

DAYS = ["Monday", "Tuesday"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]


def recipe(name, meal_type="dinner", calories=600.0):
    return Recipe(
        name=name,
        meal_type=meal_type,
        ingredients=[
            Ingredient(
                name="Chicken breast", quantity_g=250, nova_group=1,
                calories=calories, protein_g=calories * 0.09,
                net_carbs_g=calories * 0.04, fat_g=calories * 0.03,
            )
        ],
        instructions=["Cook it.", "Serve it."],
        prep_time_minutes=25,
        servings=1,
    )


def week_plan(*, failures=None) -> WeekPlan:
    """Two days covering all four slot modes at once.

    Monday dinner is cooked and feeds Tuesday lunch; Monday breakfast is
    cooked; Monday lunch is skipped; Tuesday dinner is a cook that failed to
    generate. That is every branch `_slot_entry` has.
    """
    slots = []
    for day in DAYS:
        for meal_type in MEAL_TYPES:
            mode, source = MODE_COOK, None
            if day == "Monday" and meal_type == "lunch":
                mode = MODE_SKIP
            elif day == "Tuesday" and meal_type == "lunch":
                mode, source = MODE_LEFTOVER, "Monday:dinner"
            elif meal_type == "snack":
                mode = MODE_SKIP
            elif day == "Tuesday" and meal_type == "breakfast":
                mode = MODE_SKIP
            slots.append(SlotSpec(day=day, meal_type=meal_type, mode=mode, source=source))

    events = [
        CookEvent(
            slot_id="Monday:breakfast", day="Monday", meal_type="breakfast",
            portions=1, eaten_by=["Monday:breakfast"],
            recipe=recipe("Smoked Salmon Scramble", "breakfast", 450.0),
        ),
        CookEvent(
            slot_id="Monday:dinner", day="Monday", meal_type="dinner",
            portions=2, eaten_by=["Monday:dinner", "Tuesday:lunch"],
            recipe=recipe("Green Chicken Curry", "dinner", 620.0),
        ),
    ]
    return WeekPlan(
        days=DAYS,
        servings_per_meal=1,
        generated_at="2026-08-18T09:30:00",
        week_start_date="2026-08-17",
        cook_events=events,
        slots=slots,
        targets={day: {"calories": 2000, "protein_g": 144,
                       "net_carbs_g": 120, "fat_g": 89} for day in DAYS},
        failures=failures or {"Tuesday:dinner": "provider returned an empty completion"},
    )


class TestFormatWeekMenuMarkdown(unittest.TestCase):
    def setUp(self):
        self.plan = week_plan()
        self.text = export_menu.format_week_menu_markdown(self.plan)

    def test_it_renders_without_a_caller(self):
        """The point of the test: nothing in the app calls this, so nothing in
        the app would notice it breaking."""
        self.assertTrue(self.text.strip())
        self.assertTrue(self.text.endswith("\n"))

    def test_every_day_gets_a_section(self):
        for day in DAYS:
            with self.subTest(day=day):
                self.assertIn(f"## {day}", self.text)

    def test_a_cooked_dish_is_named(self):
        self.assertIn("Green Chicken Curry", self.text)
        self.assertIn("Smoked Salmon Scramble", self.text)

    def test_a_leftover_points_at_the_meal_it_came_from(self):
        """The reader has to be able to tell a leftover from a second cook."""
        tuesday = self.text.split("## Tuesday", 1)[1]
        self.assertIn("Green Chicken Curry", tuesday)

    def test_a_failed_slot_is_shown_rather_than_omitted(self):
        """Silently dropping it would read as a cheap day rather than a gap."""
        self.assertRegex(self.text, r"(?i)not generated")

    def test_each_day_carries_a_total(self):
        self.assertEqual(self.text.count("Day total"), len(DAYS))

    def test_the_generation_timestamp_is_included(self):
        self.assertIn("2026-08-18 09:30", self.text)

    def test_a_plan_with_no_failures_still_renders(self):
        text = export_menu.format_week_menu_markdown(week_plan(failures={}))
        self.assertIn("## Monday", text)


class TestSlotEntryIsSharedWithThePdf(unittest.TestCase):
    """`_slot_entry` is the one walk both formats read, which is what stops
    the Markdown and the PDF disagreeing about what a slot says."""

    def setUp(self):
        self.plan = week_plan()
        self.by_slot = self.plan.by_slot()

    def entry(self, day, meal_type):
        slot = next(
            s for s in self.plan.slots if s.id == slot_id(day, meal_type)
        )
        return export_menu._slot_entry(self.plan, self.by_slot, slot)

    def test_a_cook_reports_its_dish_and_macros(self):
        entry = self.entry("Monday", "dinner")
        self.assertEqual(entry["dish"], "Green Chicken Curry")
        self.assertTrue(entry["macros"])

    def test_a_skip_carries_no_macros(self):
        entry = self.entry("Monday", "lunch")
        self.assertFalse(entry["macros"])

    def test_a_failed_cook_is_distinguishable_from_a_skip(self):
        failed = self.entry("Tuesday", "dinner")
        skipped = self.entry("Monday", "lunch")
        self.assertNotEqual(failed["dish"], skipped["dish"])

    def test_an_orphaned_leftover_does_not_crash_the_walk(self):
        """Its source failed to generate, so there is no recipe behind it —
        it must read as a gap rather than raise."""
        plan = week_plan()
        plan = plan.model_copy(update={"cook_events": [
            e for e in plan.cook_events if e.slot_id != "Monday:dinner"
        ]})
        text = export_menu.format_week_menu_markdown(plan)
        self.assertIn("## Tuesday", text)


class TestBuildWeekMenuHtml(unittest.TestCase):
    """`build_week_menu_html` is the mobile counterpart to the PDF — one
    scrolling page instead of paginated print output, with tap-to-strike
    steps. Unlike the PDF (untested — asserting on reportlab bytes would pin
    layout, not content), plain markup can be asserted on directly.
    """

    def setUp(self):
        self.plan = week_plan()
        self.html = export_menu.build_week_menu_html(self.plan)

    def test_it_renders_without_a_caller(self):
        self.assertTrue(self.html.strip())
        self.assertIn("<!doctype html>", self.html.lower())

    def test_every_day_gets_a_section(self):
        for day in DAYS:
            with self.subTest(day=day):
                self.assertIn(f'id="day-{day.lower()}"', self.html)

    def test_a_cooked_dish_is_named(self):
        self.assertIn("Green Chicken Curry", self.html)
        self.assertIn("Smoked Salmon Scramble", self.html)

    def test_a_failed_slot_is_shown_rather_than_omitted(self):
        self.assertRegex(self.html, r"(?i)not generated")

    def test_recipe_steps_are_present_and_individually_clickable(self):
        self.assertIn("Cook it.", self.html)
        self.assertIn("Serve it.", self.html)
        # Two recipes x two instructions each = four tappable step rows.
        self.assertEqual(self.html.count('class="step" onclick='), 4)

    def test_shopping_list_is_included_and_tappable(self):
        self.assertIn('id="shopping"', self.html)
        self.assertIn("Chicken breast", self.html)
        self.assertIn('class="shop-row" onclick=', self.html)

    def test_a_plan_with_no_cook_events_has_no_shopping_section(self):
        plan = self.plan.model_copy(update={"cook_events": []})
        html = export_menu.build_week_menu_html(plan)
        self.assertNotIn('id="shopping"', html)

    def test_recipe_name_is_escaped(self):
        """A model-generated dish name is untrusted text going straight into
        markup — an unescaped `<`/`&` would break the page it's embedded in."""
        events = [
            e.model_copy(update={"recipe": e.recipe.model_copy(update={"name": "Pan & <Grill>"})})
            if e.slot_id == "Monday:breakfast" else e
            for e in self.plan.cook_events
        ]
        html = export_menu.build_week_menu_html(self.plan.model_copy(update={"cook_events": events}))
        self.assertIn("Pan &amp; &lt;Grill&gt;", html)
        self.assertNotIn("<Grill>", html)

    def test_no_script_tag(self):
        """The whole point of onclick-toggled classes over a script block:
        nothing to fetch, nothing to break, on a file opened straight from a
        phone's downloads folder."""
        self.assertNotIn("<script", self.html.lower())


def cook_event(ingredients, *, name="Test Dish", portions=2, instructions=("Mix it.",)):
    """A cook event with arbitrary ingredient names, for the JSON-LD export.

    `ingredients` is a list of `(name, quantity_g)`. `portions` is the batch
    the event is scaled to — the recipe's `servings` is set to match, as
    `build_cook_event` leaves it.
    """
    return CookEvent(
        slot_id="Monday:dinner",
        day="Monday",
        meal_type="dinner",
        portions=portions,
        eaten_by=["Monday:dinner", "Tuesday:lunch"],
        recipe=Recipe(
            name=name,
            meal_type="dinner",
            ingredients=[
                Ingredient(
                    name=n, quantity_g=q, nova_group=1,
                    calories=q, protein_g=q * 0.1, net_carbs_g=q * 0.05, fat_g=q * 0.02,
                )
                for n, q in ingredients
            ],
            instructions=list(instructions),
            prep_time_minutes=20,
            servings=portions,
        ),
    )


class TestMatcherName(unittest.TestCase):
    """`shopping.matcher_name` exists only for this exporter — the 2.1a
    Cronometer probe showed `display_name` resolves to the wrong food
    (sardines in oil for "in water", dried Italian seasoning for "Herbs").
    It strips prep verbs and keeps everything else.
    """

    def test_prep_verbs_are_stripped(self):
        self.assertEqual(matcher_name("Garlic, minced"), "Garlic")
        self.assertEqual(matcher_name("Chicken breast, raw, diced small"), "Chicken breast")
        self.assertEqual(matcher_name("Red onion, finely chopped"), "Red onion")

    def test_state_and_fat_level_and_clarifiers_are_kept(self):
        # display_name would collapse all three of these.
        self.assertEqual(matcher_name("Sardines in water, drained"), "Sardines in water, drained")
        self.assertEqual(matcher_name("Low-fat cottage cheese"), "Low-fat cottage cheese")
        self.assertEqual(matcher_name("Dijon mustard"), "Dijon mustard")

    def test_a_clarifying_parenthetical_survives(self):
        # strip_parentheticals would leave the useless token "Herbs".
        self.assertEqual(
            matcher_name("Chopped fresh herbs (parsley, dill, chives)"),
            "Herbs (parsley, dill, chives)",
        )
        self.assertEqual(matcher_name("Winter squash (butternut), cubed"), "Winter squash (butternut)")

    def test_a_prep_word_inside_a_bracket_does_not_orphan_it(self):
        self.assertEqual(matcher_name("Parmesan (freshly grated)"), "Parmesan")
        self.assertNotIn("(", matcher_name("Tomatoes (large, vine-ripened)"))


class TestBuildRecipeJsonLd(unittest.TestCase):
    def test_shape_is_a_schema_org_recipe(self):
        data = export_menu.build_recipe_jsonld(cook_event([("Beef mince", 600)]))
        self.assertEqual(data["@context"], "https://schema.org")
        self.assertEqual(data["@type"], "Recipe")
        self.assertEqual(data["name"], "Test Dish")
        self.assertIsInstance(data["recipeIngredient"], list)

    def test_yield_is_the_batch_not_one_serving(self):
        """A bulk cook feeding three dinners must not be reported at 3x per
        serving — Cronometer divides by recipeYield."""
        data = export_menu.build_recipe_jsonld(cook_event([("Beef", 900)], portions=6))
        self.assertEqual(data["recipeYield"], "6 servings")

    def test_ingredient_strings_are_grams_plus_matcher_name(self):
        data = export_menu.build_recipe_jsonld(
            cook_event([("Garlic, minced", 30), ("Green peas, frozen", 90)])
        )
        self.assertIn("30 g Garlic", data["recipeIngredient"])
        self.assertIn("90 g Green peas, frozen", data["recipeIngredient"])

    def test_water_is_dropped(self):
        data = export_menu.build_recipe_jsonld(
            cook_event([("Water", 500), ("Whey protein powder", 60)])
        )
        self.assertEqual(data["recipeIngredient"], ["60 g Whey protein powder"])

    def test_instructions_become_howtostep_objects(self):
        data = export_menu.build_recipe_jsonld(
            cook_event([("Beef", 100)], instructions=("Brown the beef.", "Simmer."))
        )
        self.assertEqual(
            data["recipeInstructions"],
            [
                {"@type": "HowToStep", "text": "Brown the beef."},
                {"@type": "HowToStep", "text": "Simmer."},
            ],
        )

    def test_no_nutrition_block(self):
        """Cronometer computing its own macros is the whole point (F5)."""
        data = export_menu.build_recipe_jsonld(cook_event([("Beef", 100)]))
        self.assertNotIn("nutrition", data)


class TestRenderRecipePage(unittest.TestCase):
    def test_embedded_jsonld_parses(self):
        page = export_menu.render_recipe_page(cook_event([("Beef", 100)]))
        block = page.split('application/ld+json">', 1)[1].split("</script>", 1)[0]
        self.assertEqual(json.loads(block)["@type"], "Recipe")

    def test_a_dish_name_cannot_break_out_of_the_script_block(self):
        page = export_menu.render_recipe_page(
            cook_event([("Beef", 100)], name="Pan & <Grill> </script><script>evil")
        )
        # The only real </script> is the one closing the ld+json block.
        self.assertEqual(page.count("</script>"), 1)
        # Visible body escapes it.
        self.assertNotIn("<Grill>", page)
        self.assertIn("Pan &amp; &lt;Grill&gt;", page)
        # And the block still parses with the name intact.
        block = page.split('application/ld+json">', 1)[1].split("</script>", 1)[0]
        self.assertEqual(json.loads(block)["name"], "Pan & <Grill> </script><script>evil")

    def test_app_macros_ride_in_a_comment_not_the_jsonld(self):
        page = export_menu.render_recipe_page(cook_event([("Beef", 300)], portions=3))
        self.assertIn("<!-- App per-serving macros", page)
        block = page.split('application/ld+json">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("nutrition", block)
        self.assertNotIn("calories", block)


class TestWriteRecipeExports(unittest.TestCase):
    def setUp(self):
        self.plan = week_plan()
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def test_one_file_per_cook_event(self):
        written, failed = export_menu.write_recipe_exports(self.plan, out_dir=self.out)
        self.assertEqual(failed, {})
        self.assertEqual(len(written), len(self.plan.cook_events))
        for path in written:
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(path.endswith(".html"))

    def test_leftover_skip_and_failed_slots_produce_no_file(self):
        """The plan has 8 slots across all four modes but only two cooks —
        the export walks `cook_events`, the same set the PDF/HTML recipe
        pages use."""
        written, _ = export_menu.write_recipe_exports(self.plan, out_dir=self.out)
        self.assertEqual(len(os.listdir(self.out)), 2)
        names = sorted(os.path.basename(p) for p in written)
        self.assertTrue(any("green-chicken-curry" in n for n in names))
        self.assertTrue(any("smoked-salmon-scramble" in n for n in names))

    def test_a_recipe_that_fails_to_render_does_not_stop_the_rest(self):
        real = export_menu.render_recipe_page

        def flaky(event):
            if event.slot_id == "Monday:breakfast":
                raise RuntimeError("bad recipe")
            return real(event)

        with mock.patch.object(export_menu, "render_recipe_page", side_effect=flaky):
            written, failed = export_menu.write_recipe_exports(self.plan, out_dir=self.out)

        self.assertEqual(list(failed), ["Monday:breakfast"])
        self.assertEqual(len(written), 1)
        self.assertTrue(os.path.basename(written[0]).startswith("monday-dinner-"))

    def test_default_out_dir_is_under_data_never_a_tracked_dir(self):
        default = os.path.join(export_menu.DATA_DIR, export_menu.RECIPE_EXPORT_DIRNAME)
        self.assertTrue(default.endswith(os.path.join("data", "recipe_exports")))


if __name__ == "__main__":
    unittest.main()

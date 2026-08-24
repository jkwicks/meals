"""Tests for the deterministic week mechanics — the API-free half of planning.

`week.py` resolves the entire shape of a week before a single token is
generated: which slots cook, how many portions each cook must yield, which
shopping trip pays for it, and whether the grid is coherent at all. None of it
touches the network or a model, and all of it is load-bearing — `portions_for`
in particular is the derived-portions rule the whole cook-events architecture
rests on, and the reason there is deliberately no "batch multiplier" setting.

Covers what the end-to-end review found untested: `portions_for`,
`validate_week`, `shopping_windows`, `spread_batch`, `day_multiplicity`,
`carried_macros`, `storage_note`, `next_choice`, `meal_overrides_for`,
`training_pin_budget`, and the two shopping helpers `collect_unique_plants`
and `round_ingredient_quantity`.

`unittest` and the `sys.path` insert match `test_week_composition.py`; see its
docstring for why.
"""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import planner  # noqa: E402
import shopping  # noqa: E402
import week as wk  # noqa: E402
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, WeekSpec  # noqa: E402

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]


def spec_with(modes=None, servings_per_meal=2, days=None, **kw) -> WeekSpec:
    """A full 7x4 grid of cook slots, with `modes` overriding named slot ids.

    Every slot cooks by default because that is the shape every rule below is
    stated against; the interesting cases are all departures from it.
    """
    days = days or DAYS
    modes = modes or {}
    slots = []
    for day in days:
        for meal_type in MEAL_TYPES:
            sid = wk.slot_id(day, meal_type)
            override = modes.get(sid, {})
            slots.append(
                SlotSpec(
                    day=day,
                    meal_type=meal_type,
                    mode=override.get("mode", MODE_COOK),
                    source=override.get("source"),
                    style=override.get("style"),
                    cuisine=override.get("cuisine"),
                    extra_portions=override.get("extra_portions", 0),
                )
            )
    return WeekSpec(days=days, slots=slots, servings_per_meal=servings_per_meal, **kw)


class TestPortionsAreDerived(unittest.TestCase):
    """The rule the architecture rests on: a batch size *is* the number of
    slots pointing at it x household size. Never entered by hand, so a batch
    can never silently disagree with the meals it has to cover."""

    def test_a_lone_cook_yields_one_meal_for_the_household(self):
        spec = spec_with()
        self.assertEqual(wk.portions_for(spec)["Monday:dinner"], 2)

    def test_each_claiming_slot_grows_the_batch(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertEqual(wk.portions_for(spec)["Monday:dinner"], 4)

    def test_extra_portions_are_added_on_top(self):
        """Deliberate extras to freeze, not a multiplier."""
        spec = spec_with({"Monday:dinner": {"mode": MODE_COOK, "extra_portions": 3}})
        self.assertEqual(wk.portions_for(spec)["Monday:dinner"], 5)

    def test_household_size_scales_every_cook(self):
        spec = spec_with(servings_per_meal=4)
        self.assertEqual(wk.portions_for(spec)["Monday:dinner"], 4)

    def test_a_leftover_slot_has_no_portions_of_its_own(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertNotIn("Tuesday:lunch", wk.portions_for(spec))

    def test_claim_counts_and_eaten_on_agree(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
            "Wednesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertEqual(wk.claim_counts(spec)["Monday:dinner"], 3)
        self.assertEqual(
            wk.eaten_on(spec)["Monday:dinner"],
            ["Monday:dinner", "Tuesday:lunch", "Wednesday:lunch"],
        )


class TestValidateWeek(unittest.TestCase):
    """The gate on every generation. Returns messages rather than raising so
    the UI can show all problems at once."""

    CONFIG = {
        "cuisines": ["thai", "greek"],
        "cuisine_meal_types": ["dinner"],
        "meal_types": MEAL_TYPES,
        "meal_styles": {"breakfast": {"eggs_salmon": "..."}, "dinner": {"curry": "..."}},
    }

    def errors_for(self, modes):
        return wk.validate_week(spec_with(modes), self.CONFIG)

    def test_a_plain_cooking_week_is_clean(self):
        self.assertEqual(wk.validate_week(spec_with(), self.CONFIG), [])

    def test_a_leftover_needs_a_source(self):
        errors = self.errors_for({"Tuesday:lunch": {"mode": MODE_LEFTOVER}})
        self.assertTrue(any("no source meal chosen" in e for e in errors))

    def test_a_leftover_cannot_point_at_a_later_day(self):
        """The ordering guarantee generation depends on — days are walked in
        week order, so a source must already exist when its macros are read."""
        errors = self.errors_for({
            "Monday:lunch": {"mode": MODE_LEFTOVER, "source": "Friday:dinner"},
        })
        self.assertTrue(any("later in the" in e for e in errors))

    def test_a_leftover_cannot_point_at_another_leftover(self):
        errors = self.errors_for({
            "Tuesday:dinner": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
            "Wednesday:lunch": {"mode": MODE_LEFTOVER, "source": "Tuesday:dinner"},
        })
        self.assertTrue(any("isn't a cooked meal" in e for e in errors))

    def test_a_leftover_cannot_point_at_a_missing_slot(self):
        errors = self.errors_for({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:brunch"},
        })
        self.assertTrue(any("not a slot in this week" in e for e in errors))

    def test_a_style_must_belong_to_its_meal_type(self):
        errors = self.errors_for({"Monday:dinner": {"mode": MODE_COOK, "style": "eggs_salmon"}})
        self.assertTrue(any("isn't a dinner style" in e for e in errors))

    def test_a_cuisine_must_be_known_and_apply_to_that_meal_type(self):
        unknown = self.errors_for({"Monday:dinner": {"mode": MODE_COOK, "cuisine": "martian"}})
        self.assertTrue(any("not in config cuisines" in e for e in unknown))

        wrong_type = self.errors_for({"Monday:lunch": {"mode": MODE_COOK, "cuisine": "thai"}})
        self.assertTrue(any("cuisine themes only apply" in e for e in wrong_type))

    def test_extra_portions_need_a_cooking_slot(self):
        errors = self.errors_for({
            "Monday:lunch": {"mode": MODE_SKIP, "extra_portions": 2},
        })
        self.assertTrue(any("extra portions only apply" in e for e in errors))

    def test_a_week_with_nothing_to_cook_is_rejected(self):
        spec = spec_with({
            wk.slot_id(day, meal_type): {"mode": MODE_SKIP}
            for day in DAYS for meal_type in MEAL_TYPES
        })
        errors = wk.validate_week(spec, self.CONFIG)
        self.assertTrue(any("Nothing to cook" in e for e in errors))

    def test_every_problem_is_reported_at_once(self):
        """Not fail-fast: the UI shows the whole list and keeps Generate
        disabled until the grid is coherent."""
        errors = self.errors_for({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER},
            "Monday:dinner": {"mode": MODE_COOK, "cuisine": "martian"},
        })
        self.assertGreaterEqual(len(errors), 2)


class TestShoppingWindows(unittest.TestCase):
    """Windows group by *cook* day. A Sunday batch eaten Wednesday belongs
    entirely to the Sunday trip — grouping by eating day would split one
    recipe's ingredients across two lists."""

    def test_one_shop_day_covers_the_whole_week(self):
        windows = wk.shopping_windows(DAYS, ["Monday"])
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].days, DAYS)

    def test_two_shop_days_split_the_week_between_them(self):
        windows = wk.shopping_windows(DAYS, ["Monday", "Thursday"])
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].days, ["Monday", "Tuesday", "Wednesday"])
        self.assertEqual(windows[1].days, ["Thursday", "Friday", "Saturday", "Sunday"])

    def test_every_day_lands_in_exactly_one_window(self):
        for shop_days in (["Monday"], ["Monday", "Friday"], ["Tuesday", "Thursday", "Sunday"]):
            with self.subTest(shop_days=shop_days):
                covered = [day for w in wk.shopping_windows(DAYS, shop_days) for day in w.days]
                self.assertEqual(sorted(covered), sorted(DAYS))


class TestSpreadBatch(unittest.TestCase):
    """Anchor selection for the bulk-prep and long-cook toggles."""

    def test_it_links_forward_slots_to_one_anchor(self):
        spec, anchor = wk.spread_batch(spec_with(), "dinner", 6)
        self.assertEqual(anchor, "Monday:dinner")
        self.assertGreater(wk.claim_counts(spec)[anchor], 1)

    def test_the_earliest_eligible_day_wins(self):
        """Deterministic, and it leaves the most week left to spread across."""
        _, anchor = wk.spread_batch(spec_with(), "dinner", 6)
        self.assertEqual(wk.parse_slot_id(anchor)[0], "Monday")

    def test_excluded_days_are_skipped(self):
        """So a second call for the week's other toggle picks a different day."""
        _, anchor = wk.spread_batch(spec_with(), "dinner", 6, exclude_days={"Monday"})
        self.assertNotEqual(wk.parse_slot_id(anchor)[0], "Monday")

    def test_no_eligible_anchor_returns_none_rather_than_raising(self):
        """Callers treat it as "nothing to do this run", not an error."""
        spec = spec_with({
            wk.slot_id(day, "dinner"): {"mode": MODE_SKIP} for day in DAYS
        })
        out, anchor = wk.spread_batch(spec, "dinner", 6)
        self.assertIsNone(anchor)
        self.assertIs(out, spec)

    def test_the_batch_never_exceeds_three_claims(self):
        """So a small household's arithmetic doesn't spread one dish across
        half the week."""
        spec, anchor = wk.spread_batch(spec_with(servings_per_meal=1), "dinner", 99)
        self.assertLessEqual(wk.claim_counts(spec)[anchor], 3)

    def test_links_are_valid_leftovers(self):
        """It works entirely through `link_leftover`, so the result must pass
        the same validation any hand-built grid does."""
        spec, _ = wk.spread_batch(spec_with(), "dinner", 6)
        self.assertEqual(
            wk.validate_week(spec, TestValidateWeek.CONFIG), []
        )

    def test_a_lunch_anchor_claims_only_lunches(self):
        """The shape the batch toggles actually use: one meal type straight
        across the front of the week. The (anchor_meal_type, "lunch") pair
        dedupes, so a lunch anchor never falls through to dinners."""
        spec, anchor = wk.spread_batch(spec_with(), "lunch", 6)
        self.assertEqual(anchor, "Monday:lunch")
        self.assertEqual(
            wk.eaten_on(spec)[anchor],
            ["Monday:lunch", "Tuesday:lunch", "Wednesday:lunch"],
        )
        self.assertEqual(spec.by_id()["Tuesday:dinner"].mode, MODE_COOK)

    def test_its_own_links_are_marked_so_they_can_be_cleared(self):
        """`clear_batch_links` needs to tell a toggle's link from a user's."""
        spec, anchor = wk.spread_batch(spec_with(), "dinner", 6)
        linked = [s for s in spec.slots if s.mode == MODE_LEFTOVER]
        self.assertTrue(linked)
        self.assertTrue(all(s.link_origin == wk.LINK_ORIGIN_BATCH for s in linked))

    def test_a_second_run_on_its_own_output_re_spreads_rather_than_freezing(self):
        """The bug this was written for. `spread_batch` only ever *adds*
        claims and counts what the anchor already has, so its own previous
        output satisfies the next run's target: it links nothing, returns the
        same anchor, and the batch shape plus its anchor day freeze forever.
        A real week reproduced this exactly — Monday:dinner anchored every
        run, spread to Tuesday and Wednesday *dinners*, and `prefer_lunch_
        links` could never take effect because there was nothing left to
        link. Clearing between runs is what makes the preference reachable."""
        first, anchor = wk.spread_batch(spec_with(), "dinner", 6)
        self.assertEqual(wk.eaten_on(first)[anchor], [
            "Monday:dinner", "Tuesday:dinner", "Wednesday:dinner",
        ])

        # Re-running on the un-cleared grid: nothing moves, whatever we ask for.
        frozen, frozen_anchor = wk.spread_batch(first, "dinner", 6)
        self.assertEqual(frozen_anchor, anchor)
        self.assertEqual(wk.eaten_on(frozen), wk.eaten_on(first))

        # Cleared first, the same call genuinely re-spreads: the anchor is
        # free to move again, which the frozen grid above cannot do.
        cleared = wk.clear_batch_links(first)
        second, second_anchor = wk.spread_batch(
            cleared, "dinner", 6, exclude_days={"Monday"}
        )
        self.assertEqual(second_anchor, "Tuesday:dinner")
        self.assertEqual(wk.eaten_on(second)[second_anchor], [
            "Tuesday:dinner", "Wednesday:dinner", "Thursday:dinner",
        ])

    def test_clearing_leaves_a_users_own_link_alone(self):
        """A hand-made "Link to next lunch" is a structural edit the user made
        on purpose — the same carve-out `clear_styles` documents."""
        spec = wk.link_leftover(spec_with(), "Tuesday:lunch", "Monday:dinner")
        cleared = wk.clear_batch_links(spec)
        self.assertEqual(cleared.by_id()["Tuesday:lunch"].mode, MODE_LEFTOVER)
        self.assertEqual(cleared.by_id()["Tuesday:lunch"].source, "Monday:dinner")

    def test_clearing_a_batch_leaves_the_grid_generatable(self):
        """Cleared slots go back to MODE_COOK, not to a leftover with no
        source — which `validate_week` rejects outright."""
        spec, _ = wk.spread_batch(spec_with(), "dinner", 6)
        cleared = wk.clear_batch_links(spec)
        self.assertEqual(wk.validate_week(cleared, TestValidateWeek.CONFIG), [])

    def test_unlink_turns_a_leftover_back_into_a_cook(self):
        spec = wk.link_leftover(spec_with(), "Tuesday:lunch", "Monday:dinner")
        out = wk.unlink_leftover(spec, "Tuesday:lunch")
        slot = out.by_id()["Tuesday:lunch"]
        self.assertEqual(slot.mode, MODE_COOK)
        self.assertIsNone(slot.source)

    def test_unlink_clears_the_batch_marker(self):
        """A stale True would make the next `clear_batch_links` discard a link
        the user has since made by hand on that slot."""
        spec, _ = wk.spread_batch(spec_with(), "dinner", 6)
        target = next(s.id for s in spec.slots if s.link_origin == wk.LINK_ORIGIN_BATCH)
        out = wk.unlink_leftover(spec, target)
        self.assertEqual(out.by_id()[target].link_origin, wk.LINK_ORIGIN_USER)
        relinked = wk.link_leftover(out, target, "Monday:dinner")
        self.assertEqual(wk.clear_batch_links(relinked).by_id()[target].mode, MODE_LEFTOVER)

    def location_linked(self):
        """A grid whose Tuesday lunch eats Monday's dinner by location rule —
        the shipped Office-lunch shape, in miniature, sitting in the middle of
        the row the lunch batch wants."""
        return wk.link_leftover(
            spec_with(), "Tuesday:lunch", "Monday:dinner",
            origin=wk.LINK_ORIGIN_LOCATION,
        )

    def test_a_batch_may_repoint_a_location_link(self):
        """`apply_location_modes` says an Office lunch *is* a leftover without
        saying whose — "the previous day's dinner" is how that was resolved,
        not an intent — so a batch may take it. Without this the shipped grid
        has room for exactly one batch: the location rules link Thursday and
        Friday lunches before either toggle runs, and `leftover_link_error`
        then refuses every dinner that feeds one of them."""
        out, anchor = wk.spread_batch(self.location_linked(), "lunch", 6)
        self.assertEqual(anchor, "Monday:lunch")
        self.assertEqual(
            wk.eaten_on(out)[anchor],
            ["Monday:lunch", "Tuesday:lunch", "Wednesday:lunch"],
        )
        self.assertEqual(out.by_id()["Tuesday:lunch"].link_origin, wk.LINK_ORIGIN_BATCH)

    def test_a_repointed_slot_stays_a_leftover(self):
        """Re-pointing must still satisfy the location rule that made it one —
        it changes whose leftovers, never whether."""
        out, _ = wk.spread_batch(self.location_linked(), "lunch", 6)
        self.assertEqual(out.by_id()["Tuesday:lunch"].mode, MODE_LEFTOVER)
        self.assertEqual(wk.validate_week(out, TestValidateWeek.CONFIG), [])

    def test_a_batch_never_repoints_a_users_link(self):
        """That one names a specific dinner on purpose, so the batch skips it
        and grows past it instead."""
        spec = wk.link_leftover(spec_with(), "Tuesday:lunch", "Monday:dinner")
        out, anchor = wk.spread_batch(spec, "lunch", 6)
        self.assertEqual(out.by_id()["Tuesday:lunch"].source, "Monday:dinner")
        self.assertEqual(out.by_id()["Tuesday:lunch"].link_origin, wk.LINK_ORIGIN_USER)
        self.assertNotIn("Tuesday:lunch", wk.eaten_on(out)[anchor])

    def test_an_anchor_that_cannot_claim_anything_is_passed_over(self):
        """The reachability filter asks whether a target this anchor may
        actually take exists, not merely whether an unexcluded day does — a
        day can be eligible and have nothing claimable on it, and choosing it
        strands the toggle."""
        # Monday is excluded, so Tuesday is the earliest candidate — but the
        # only two days within its reach (Wednesday, Thursday) are skipped, so
        # it can claim nothing. Without the filter it would be picked and the
        # call would return None instead of batching on Saturday.
        modes = {wk.slot_id(day, "lunch"): {"mode": MODE_SKIP} for day in DAYS}
        modes.update({
            wk.slot_id(day, "dinner"): {"mode": MODE_SKIP} for day in DAYS[2:5]
        })
        out, anchor = wk.spread_batch(
            spec_with(modes), "dinner", 6, exclude_days={"Monday"}, max_span_days=2
        )
        self.assertEqual(anchor, "Saturday:dinner")
        self.assertIn("Sunday:dinner", wk.eaten_on(out)[anchor])

    def test_a_blocking_location_link_is_released_rather_than_walked_past(self):
        """The bug behind "prep meals keep landing on Thursday and Friday".
        `leftover_link_error` refuses a cook that already feeds something, so
        an Office rule pointing Thursday's lunch at Wednesday's dinner put
        that Wednesday dinner — an early-week slot the batch wanted — out of
        reach, and the walk carried on forward into Thursday and Friday
        instead. The location link is released so the near slot can be taken."""
        spec = wk.link_leftover(
            spec_with(), "Thursday:lunch", "Wednesday:dinner",
            origin=wk.LINK_ORIGIN_LOCATION,
        )
        out, anchor = wk.spread_batch(
            spec, "dinner", 6, exclude_days={"Monday"}, max_day_index=2
        )
        self.assertEqual(anchor, "Tuesday:dinner")
        self.assertIn("Wednesday:dinner", wk.eaten_on(out)[anchor])
        # Released, so it cooks — the same state apply_location_modes itself
        # falls back to when a day's previous dinner isn't a cook.
        self.assertEqual(out.by_id()["Thursday:lunch"].mode, MODE_COOK)
        self.assertEqual(wk.validate_week(out, TestValidateWeek.CONFIG), [])

    def test_a_blocking_user_link_is_never_released(self):
        """A user's link names that dinner on purpose, so the batch leaves
        both it and the slot it protects alone."""
        spec = wk.link_leftover(spec_with(), "Thursday:lunch", "Wednesday:dinner")
        out, anchor = wk.spread_batch(
            spec, "dinner", 6, exclude_days={"Monday"}, max_day_index=2
        )
        self.assertEqual(out.by_id()["Thursday:lunch"].mode, MODE_LEFTOVER)
        self.assertNotIn("Wednesday:dinner", wk.eaten_on(out).get(anchor, []))

    def test_max_day_index_bounds_the_batch_from_prep_day(self):
        """`max_span_days` counts from the anchor's own day, but a prep-session
        batch is cooked the day *before* the week starts — so a Tuesday anchor
        reaching Friday is 3 days by that bound and 5 days out of the fridge.
        This is the bound that actually keeps Sunday-cooked food off Friday."""
        modes = {wk.slot_id(day, "lunch"): {"mode": MODE_SKIP} for day in DAYS}
        out, anchor = wk.spread_batch(spec_with(modes), "dinner", 6, max_day_index=2)
        claimed = wk.eaten_on(out)[anchor]
        self.assertTrue(all(DAYS.index(wk.parse_slot_id(c)[0]) <= 2 for c in claimed), claimed)

    def test_the_anchor_itself_respects_the_prep_day_bound(self):
        """Unlike every other bound here, this one covers the anchor: the
        anchor's own eating day is also days-since-prep, so an anchor outside
        the window is already unsafe before it spreads anywhere. With every
        in-window dinner excluded it reports no batch rather than reaching
        past the window for somewhere to anchor."""
        _, anchor = wk.spread_batch(
            spec_with(), "dinner", 6,
            exclude_days={"Monday", "Tuesday", "Wednesday"}, max_day_index=2,
        )
        self.assertIsNone(anchor)

    def test_two_toggles_together_take_one_row_each(self):
        """What `ui_generation.apply_batch_selections` actually does: bulk prep
        takes the lunches, long cook the dinners, both from day 1. They cannot
        collide (different rows) and neither can drift late (both start at the
        earliest day), so no exclusion, preference or ordering dance is needed
        between them at all."""
        spec, bulk_prep_anchor = wk.spread_batch(spec_with(), "lunch", 6, max_day_index=2)
        spec, long_cook_anchor = wk.spread_batch(spec, "dinner", 6, max_day_index=2)
        self.assertEqual(bulk_prep_anchor, "Monday:lunch")
        self.assertEqual(long_cook_anchor, "Monday:dinner")
        self.assertEqual(wk.eaten_on(spec)[bulk_prep_anchor], [
            "Monday:lunch", "Tuesday:lunch", "Wednesday:lunch",
        ])
        self.assertEqual(wk.eaten_on(spec)[long_cook_anchor], [
            "Monday:dinner", "Tuesday:dinner", "Wednesday:dinner",
        ])
        # Exactly batch_target_servings each, and nothing past Wednesday.
        portions = wk.portions_for(spec)
        self.assertEqual(portions[bulk_prep_anchor], 6)
        self.assertEqual(portions[long_cook_anchor], 6)


class TestDayMultiplicityAndCarriedMacros(unittest.TestCase):
    def test_multiplicity_counts_same_day_claims(self):
        spec = spec_with({
            "Monday:dinner": {"mode": MODE_COOK},
            "Monday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertEqual(planner.day_multiplicity(spec, "Monday")["Monday:dinner"], 2)

    def test_a_later_days_claim_does_not_count_toward_today(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        self.assertEqual(planner.day_multiplicity(spec, "Monday")["Monday:dinner"], 1)

    def test_carried_macros_are_empty_without_a_generated_source(self):
        spec = spec_with({
            "Tuesday:lunch": {"mode": MODE_LEFTOVER, "source": "Monday:dinner"},
        })
        totals, descriptions = planner.carried_macros(spec, "Tuesday", {})
        self.assertEqual(descriptions, [])
        self.assertEqual(totals["calories"], 0.0)


class TestStorageNote(unittest.TestCase):
    def test_a_single_serving_eaten_today_says_nothing(self):
        self.assertEqual(planner.storage_note(1, 0), "")
        self.assertEqual(planner.storage_note(4, 0), "")

    def test_a_short_lived_batch_says_refrigerate(self):
        note = planner.storage_note(4, 2)
        self.assertIn("refrigerate in airtight containers", note)
        self.assertTrue(note.startswith(planner.STORAGE_NOTE_PREFIX))

    def test_a_long_lived_batch_says_freeze_the_rest(self):
        self.assertIn("freeze the rest", planner.storage_note(6, 30))

    def test_the_fridge_threshold_comes_from_config(self):
        config = {"inventory_rules": {"fridge_safe_days": 2}}
        self.assertIn("freeze the rest", planner.storage_note(4, 3, config))


class TestNextChoice(unittest.TestCase):
    """Strict LRU, not "unused in the last N" — the latter starves the tail of
    the list, cycling through the first 4 of 5 styles forever."""

    OPTIONS = ["a", "b", "c", "d", "e"]

    def test_an_unused_option_is_preferred(self):
        self.assertEqual(planner.next_choice(self.OPTIONS, ["a", "b"]), "c")

    def test_ties_break_on_config_order(self):
        self.assertEqual(planner.next_choice(self.OPTIONS, []), "a")

    def test_the_least_recently_used_wins_once_all_are_used(self):
        self.assertEqual(planner.next_choice(self.OPTIONS, ["a", "b", "c", "d", "e"]), "a")

    def test_repeated_picks_walk_the_whole_list_before_repeating(self):
        recent, picked = [], []
        for _ in range(len(self.OPTIONS)):
            choice = planner.next_choice(self.OPTIONS, recent)
            picked.append(choice)
            recent.append(choice)
        self.assertEqual(sorted(picked), sorted(self.OPTIONS))

    def test_no_options_is_none_rather_than_an_error(self):
        self.assertIsNone(planner.next_choice([], ["a"]))

    def test_history_naming_options_that_no_longer_exist_is_ignored(self):
        self.assertEqual(planner.next_choice(["a", "b"], ["deleted", "a"]), "b")


class TestMealOverridesFor(unittest.TestCase):
    def config(self, overrides):
        return {
            "weekly_schedule": {"Monday": {"meal_overrides": overrides}},
            "meal_types": MEAL_TYPES,
        }

    def test_fat_is_derived_when_not_given(self):
        out = planner.meal_overrides_for(
            "Monday", self.config({"breakfast": {"calories": 450, "protein_g": 45,
                                                 "net_carbs_g": 25}})
        )
        self.assertAlmostEqual(
            out["breakfast"]["fat_g"], planner.derive_fat_g(450, 45, 25), places=6
        )

    def test_an_explicit_fat_figure_is_kept(self):
        out = planner.meal_overrides_for(
            "Monday", self.config({"breakfast": {"calories": 450, "fat_g": 12}})
        )
        self.assertEqual(out["breakfast"]["fat_g"], 12)

    def test_an_unknown_meal_type_is_dropped_not_raised(self):
        """A config typo must not cost a day of generation."""
        out = planner.meal_overrides_for("Monday", self.config({"brunch": {"calories": 450}}))
        self.assertEqual(out, {})

    def test_an_override_without_calories_is_dropped(self):
        out = planner.meal_overrides_for("Monday", self.config({"breakfast": {"protein_g": 40}}))
        self.assertEqual(out, {})

    def test_a_day_with_no_overrides_is_empty(self):
        config = {"weekly_schedule": {"Monday": {}}, "meal_types": MEAL_TYPES}
        self.assertEqual(planner.meal_overrides_for("Monday", config), {})
        self.assertEqual(planner.meal_overrides_for("Friday", config), {})


class TestTrainingPinBudget(unittest.TestCase):
    """The post-workout pin, recomputed after hydration replaces the targets it
    was originally derived from — left alone it claimed 49 g of protein worked
    out from a day that no longer exists."""

    TARGETS = {"calories": 2000, "protein_g": 144, "net_carbs_g": 120, "fat_g": 89}
    WEIGHTS = {"breakfast": 0.30, "lunch": 0.30, "dinner": 0.30, "snack": 0.10}

    def test_the_pin_tracks_its_share_of_the_day(self):
        budget = planner.training_pin_budget(self.TARGETS, "snack", self.WEIGHTS)
        self.assertAlmostEqual(budget["protein_g"], 144 * 0.10, places=6)

    def test_a_bigger_meal_type_gets_a_bigger_pin(self):
        snack = planner.training_pin_budget(self.TARGETS, "snack", self.WEIGHTS)
        dinner = planner.training_pin_budget(self.TARGETS, "dinner", self.WEIGHTS)
        self.assertGreater(dinner["calories"], snack["calories"])

    def test_the_budget_carries_all_four_macros(self):
        budget = planner.training_pin_budget(self.TARGETS, "snack", self.WEIGHTS)
        for key in planner.MACRO_KEYS:
            self.assertIn(key, budget)


class TestShoppingHelpers(unittest.TestCase):
    def test_plants_are_counted_once_per_normalised_name(self):
        """Diversity, not quantity — and "Spinach" and "Baby spinach, washed"
        are one plant, keyed the same way shopping lines are combined."""
        self.assertEqual(
            shopping.normalize_name("Baby spinach, washed"),
            shopping.normalize_name("Baby spinach"),
        )

    def test_a_non_plant_department_is_not_counted(self):
        self.assertNotIn(
            shopping.categorize_department("Chicken breast"), shopping.PLANT_DEPARTMENTS
        )
        self.assertIn(shopping.categorize_department("Spinach"), shopping.PLANT_DEPARTMENTS)

    def test_count_unit_ingredients_round_to_whole_items(self):
        """You cannot buy 1.4 eggs."""
        rounded = shopping.round_ingredient_quantity("Eggs", 132.0, "Dairy & Eggs")
        self.assertEqual(rounded, round(rounded))

    def test_rounding_never_returns_zero_for_a_real_quantity(self):
        for name, grams, dept in [
            ("Olive oil", 3.0, "Pantry"),
            ("Salt", 0.4, "Herbs & Spices"),
            ("Chicken breast", 7.0, "Meat & Poultry"),
        ]:
            with self.subTest(name=name):
                self.assertGreater(
                    shopping.round_ingredient_quantity(name, grams, dept), 0
                )


def cook_event(day, meal_type, ingredients, name=None, portions=2):
    """A `CookEvent` carrying the named ingredients at batch quantities.

    `ingredients` is `[(name, grams), ...]`; macros are filler, since every
    assertion below is about which lines appear, how they combine, and which
    trip pays for them — never about energy.
    """
    return planner.CookEvent(
        slot_id=wk.slot_id(day, meal_type),
        day=day,
        meal_type=meal_type,
        portions=portions,
        recipe=planner.Recipe(
            name=name or f"{day} {meal_type}",
            meal_type=meal_type,
            ingredients=[
                planner.Ingredient(
                    name=n, quantity_g=g, nova_group=1,
                    calories=g * 1.2, protein_g=g * 0.1,
                    net_carbs_g=g * 0.05, fat_g=g * 0.03,
                )
                for n, g in ingredients
            ],
            instructions=["Cook it."],
            prep_time_minutes=20,
            servings=portions,
        ),
    )


class TestAggregateCookEvents(unittest.TestCase):
    """The shopping list proper: cook events in, departments out.

    Aggregation is over **cook events, not days**, so a batch's full quantity
    lands on the trip that pays for it rather than being split across the days
    that eat it.
    """

    def lines(self, shopping_list):
        return {item.name: item for item in shopping_list.items()}

    def test_the_same_ingredient_across_two_cooks_becomes_one_line(self):
        events = [
            cook_event("Monday", "dinner", [("Chicken breast", 400)]),
            cook_event("Tuesday", "dinner", [("Chicken breast", 300)]),
        ]
        lines = self.lines(shopping.aggregate_cook_events(events, DAYS))
        chicken = [item for name, item in lines.items() if "hicken" in name]
        self.assertEqual(len(chicken), 1)
        self.assertEqual(chicken[0].total_amount_g, 700)

    def test_prep_variants_combine_but_state_variants_do_not(self):
        """"Cucumber, diced" and "Cucumber, sliced" are one purchase; quinoa
        dry and cooked are not, because state changes what a gram means."""
        diced = shopping.aggregate_cook_events(
            [cook_event("Monday", "lunch", [("Cucumber, diced", 100),
                                            ("Cucumber, sliced", 50)])], DAYS
        )
        self.assertEqual(len(self.lines(diced)), 1)

        quinoa = shopping.aggregate_cook_events(
            [cook_event("Monday", "lunch", [("Quinoa, dry", 100),
                                            ("Quinoa, cooked", 200)])], DAYS
        )
        self.assertEqual(len(self.lines(quinoa)), 2)

    def test_water_never_reaches_the_list(self):
        """A "Water: 300g" line makes the rest of the list look untrustworthy."""
        out = shopping.aggregate_cook_events(
            [cook_event("Monday", "dinner", [("Water", 300), ("Chicken breast", 200)])], DAYS
        )
        self.assertEqual(len(self.lines(out)), 1)

    def test_items_are_grouped_into_departments(self):
        out = shopping.aggregate_cook_events(
            [cook_event("Monday", "dinner", [("Chicken breast", 400), ("Spinach", 100)])], DAYS
        )
        self.assertGreaterEqual(len(out.categories), 2)

    def test_the_offset_records_how_late_an_item_is_needed(self):
        """`buy_late` reads this to flag a perishable bought on day 1 for a
        day 5 cook. It annotates only — moving it to another trip is the
        shopper's call."""
        events = [cook_event("Friday", "dinner", [("Salmon fillet", 300)])]
        item = self.lines(shopping.aggregate_cook_events(events, DAYS))
        self.assertEqual(next(iter(item.values())).latest_cook_offset, DAYS.index("Friday"))

    def test_a_cook_outside_the_window_is_offset_zero(self):
        events = [cook_event("Friday", "dinner", [("Salmon fillet", 300)])]
        item = self.lines(shopping.aggregate_cook_events(events, ["Monday", "Tuesday"]))
        self.assertEqual(next(iter(item.values())).latest_cook_offset, 0)

    def test_no_events_is_an_empty_list_not_an_error(self):
        self.assertEqual(shopping.aggregate_cook_events([], DAYS).items(), [])


class TestCollectUniquePlants(unittest.TestCase):
    """The telemetry header's plant count — diversity, not quantity."""

    def test_a_plant_used_twice_counts_once(self):
        events = [
            cook_event("Monday", "dinner", [("Spinach", 100)]),
            cook_event("Tuesday", "dinner", [("Baby spinach, washed", 80)]),
        ]
        self.assertEqual(len(shopping.collect_unique_plants(events)), 1)

    def test_distinct_plants_each_count(self):
        events = [cook_event("Monday", "dinner",
                             [("Spinach", 100), ("Carrot", 80), ("Walnuts", 20)])]
        self.assertEqual(len(shopping.collect_unique_plants(events)), 3)

    def test_meat_and_dairy_are_not_plants(self):
        events = [cook_event("Monday", "dinner",
                             [("Chicken breast", 300), ("Greek yoghurt", 100),
                              ("Spinach", 50)])]
        self.assertEqual(shopping.collect_unique_plants(events), ["Spinach"])

    def test_the_result_is_sorted_and_stable(self):
        events = [cook_event("Monday", "dinner",
                             [("Walnuts", 20), ("Carrot", 80), ("Spinach", 100)])]
        plants = shopping.collect_unique_plants(events)
        self.assertEqual(plants, sorted(plants))

    def test_no_events_is_an_empty_list(self):
        self.assertEqual(shopping.collect_unique_plants([]), [])


if __name__ == "__main__":
    unittest.main()

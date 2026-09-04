"""Tests for the block layer — `dev/design-01-presets-and-blocks.md` §4 and
`dev/PROMPT-13.md` step 1: the fixed field list, the `BlockFailure`-shaped
pure validator, `active_block`'s date-parameter resolver, and the
supplemental repository store (`dev/task-queue-modified.md`'s 3.1a).

Later subtasks (mid-week resolution into `hydrate_dynamic_targets`, the
frozen protein floor, the `transition` ramp, the Settings surfaces) are not
exercised here. Nothing here touches the network, a model, or the clock,
except `active_block_today`'s own test, which follows CLAUDE.md's Tests
section: a fixture may call `date.today()`, but no assertion may depend on
what it returned.
"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from blocks import (  # noqa: E402
    active_block,
    active_block_today,
    is_restriction_block,
    validate_blocks,
)
from repository import CONFIG_FILES, LocalJSONRepository, run_sync  # noqa: E402


def make_block(**overrides) -> dict:
    fields = dict(
        name="fast-800-kickstart",
        starts_on="2026-09-07",
        ends_on="2026-09-10",
        body_goal="lose 8 kg",
        fitness_goal="maintain",
    )
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# The pure validator: file shape and the byte-identical-compatibility claim
# ---------------------------------------------------------------------------


class TestNoBlocksFile(unittest.TestCase):
    def test_none_has_no_failures(self):
        self.assertEqual(validate_blocks(None), [])

    def test_an_empty_list_has_no_failures(self):
        self.assertEqual(validate_blocks([]), [])

    def test_missing_file_resolves_to_no_active_block(self):
        """The repository's tolerant `[]` default and `active_block` agree:
        a missing file means no declared blocks, byte-identical to today."""
        self.assertIsNone(active_block([], date.today()))


class TestAMinimalBlockIsValid(unittest.TestCase):
    def test_the_five_required_fields_are_enough(self):
        self.assertEqual(validate_blocks([make_block()]), [])


class TestTheFileShape(unittest.TestCase):
    def test_a_non_list_file_fails(self):
        failures = validate_blocks({"blocks": [make_block()]})
        self.assertEqual(len(failures), 1)
        self.assertIn("JSON array", failures[0].message)

    def test_a_non_object_entry_fails(self):
        failures = validate_blocks(["not-a-block"])
        self.assertEqual(len(failures), 1)
        self.assertIn("must be an object", failures[0].message)


class TestRequiredFields(unittest.TestCase):
    def test_missing_name_fails(self):
        block = make_block()
        del block["name"]
        failures = validate_blocks([block])
        self.assertTrue(any("name" in f.problem for f in failures))

    def test_empty_name_fails(self):
        failures = validate_blocks([make_block(name="")])
        self.assertTrue(any("name" in f.problem for f in failures))

    def test_body_goal_and_fitness_goal_are_independently_required(self):
        """The two-goal split is the point of §4.1 — collapsing them into
        one was the error the design corrects. Losing either independently
        must fail, not just losing both together."""
        no_body = make_block()
        del no_body["body_goal"]
        no_fitness = make_block()
        del no_fitness["fitness_goal"]
        body_failures = validate_blocks([no_body])
        fitness_failures = validate_blocks([no_fitness])
        self.assertTrue(any("body_goal" in f.problem for f in body_failures))
        self.assertTrue(any("fitness_goal" in f.problem for f in fitness_failures))


class TestDates(unittest.TestCase):
    def test_a_malformed_starts_on_fails(self):
        failures = validate_blocks([make_block(starts_on="07/09/2026")])
        self.assertTrue(any("starts_on" in f.problem for f in failures))

    def test_a_malformed_ends_on_fails(self):
        failures = validate_blocks([make_block(ends_on="not-a-date")])
        self.assertTrue(any("ends_on" in f.problem for f in failures))

    def test_ends_on_before_starts_on_fails(self):
        failures = validate_blocks(
            [make_block(starts_on="2026-09-10", ends_on="2026-09-07")]
        )
        self.assertTrue(any("before" in f.problem for f in failures))

    def test_ends_on_equal_to_starts_on_is_fine(self):
        """A one-day block."""
        self.assertEqual(
            validate_blocks([make_block(starts_on="2026-09-07", ends_on="2026-09-07")]), []
        )

    def test_an_expired_block_is_never_a_validation_problem(self):
        """design-01 §4.3: an expired block is kept and inert, never a load
        failure — it is the record of what actually happened, paired later
        against a "did the block work?" readout."""
        self.assertEqual(
            validate_blocks([make_block(starts_on="2020-01-01", ends_on="2020-01-04")]), []
        )


class TestThePresetFieldIsForbidden(unittest.TestCase):
    def test_a_preset_key_fails_naming_the_block_and_only_that(self):
        """design-01 §2's own JSON sketch — pre-dating the 2026-09-01
        correction that removed `preset` from the field list — is the exact
        shape this must refuse, cleanly (not also flagged as an
        unrecognised field)."""
        block = make_block(preset="simple_repeat")
        failures = validate_blocks([block])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].block, "fast-800-kickstart")
        self.assertIn("preset", failures[0].message)


class TestUnknownFields(unittest.TestCase):
    def test_an_unrecognised_field_fails_naming_the_block(self):
        block = make_block(**{"some_invented_key": True})
        failures = validate_blocks([block])
        self.assertTrue(any("some_invented_key" in f.problem for f in failures))
        self.assertEqual(failures[0].block, "fast-800-kickstart")


class TestDietStyles(unittest.TestCase):
    def test_a_list_is_accepted(self):
        self.assertEqual(validate_blocks([make_block(diet_styles=["fast_800"])]), [])

    def test_a_non_list_fails(self):
        failures = validate_blocks([make_block(diet_styles="fast_800")])
        self.assertTrue(any("diet_styles" in f.problem for f in failures))


class TestProteinFloor(unittest.TestCase):
    """`skip_transition=True` on every case here isolates the field being
    tested from the required-successor check a protein_floor also triggers
    — see TestTheRequiredSuccessor for that behaviour on its own."""

    def test_a_well_formed_floor_is_accepted(self):
        block = make_block(
            protein_floor={"multiplier": 2.0, "basis": "ffm"}, skip_transition=True
        )
        self.assertEqual(validate_blocks([block]), [])

    def test_a_non_object_floor_fails(self):
        block = make_block(protein_floor=165, skip_transition=True)
        failures = validate_blocks([block])
        self.assertTrue(any("protein_floor" in f.problem for f in failures))

    def test_an_unknown_basis_fails(self):
        block = make_block(
            protein_floor={"multiplier": 2.0, "basis": "body_weight"}, skip_transition=True
        )
        failures = validate_blocks([block])
        self.assertTrue(any("basis" in f.problem for f in failures))

    def test_every_known_basis_is_accepted(self):
        for basis in ("target_weight", "ffm", "current_weight", "grams"):
            block = make_block(
                protein_floor={"multiplier": 2.0, "basis": basis}, skip_transition=True
            )
            self.assertEqual(validate_blocks([block]), [], basis)

    def test_a_non_positive_multiplier_fails(self):
        block = make_block(
            protein_floor={"multiplier": 0, "basis": "ffm"}, skip_transition=True
        )
        failures = validate_blocks([block])
        self.assertTrue(any("multiplier" in f.problem for f in failures))


class TestTargetRate(unittest.TestCase):
    def test_a_number_is_accepted(self):
        block = make_block(target_rate_kg_per_week=1.2, skip_transition=True)
        self.assertEqual(validate_blocks([block]), [])

    def test_a_non_number_fails(self):
        block = make_block(target_rate_kg_per_week="fast", skip_transition=True)
        failures = validate_blocks([block])
        self.assertTrue(any("target_rate_kg_per_week" in f.problem for f in failures))


class TestFreeTextFields(unittest.TestCase):
    def test_training_intent_peak_day_and_notes_accept_strings(self):
        block = make_block(training_intent="fat_loss", peak_day="Sunday", notes="four days")
        self.assertEqual(validate_blocks([block]), [])

    def test_training_intent_rejects_a_non_string(self):
        failures = validate_blocks([make_block(training_intent=123)])
        self.assertTrue(any("training_intent" in f.problem for f in failures))


class TestBlockType(unittest.TestCase):
    def test_transition_is_accepted(self):
        self.assertEqual(validate_blocks([make_block(block_type="transition")]), [])

    def test_an_unknown_block_type_fails(self):
        failures = validate_blocks([make_block(block_type="reintroduction")])
        self.assertTrue(any("block_type" in f.problem for f in failures))


class TestDuplicateNames(unittest.TestCase):
    def test_two_blocks_sharing_a_name_fail(self):
        first = make_block(name="dupe", starts_on="2026-09-01", ends_on="2026-09-03")
        second = make_block(name="dupe", starts_on="2026-10-01", ends_on="2026-10-03")
        failures = validate_blocks([first, second])
        self.assertTrue(any("unique" in f.problem for f in failures))


# ---------------------------------------------------------------------------
# Overlap — refused, naming both blocks and the range; a gap is normal
# ---------------------------------------------------------------------------


class TestOverlap(unittest.TestCase):
    def test_two_overlapping_blocks_fail_naming_both_and_the_range(self):
        first = make_block(name="first", starts_on="2026-09-07", ends_on="2026-09-14")
        second = make_block(name="second", starts_on="2026-09-12", ends_on="2026-09-20")
        failures = validate_blocks([first, second])
        overlap = next(f for f in failures if f.other_block is not None)
        self.assertEqual({overlap.block, overlap.other_block}, {"first", "second"})
        self.assertIn("2026-09-12", overlap.message)
        self.assertIn("2026-09-14", overlap.message)

    def test_adjacent_non_overlapping_blocks_are_fine(self):
        first = make_block(name="first", starts_on="2026-09-07", ends_on="2026-09-10")
        second = make_block(name="second", starts_on="2026-09-11", ends_on="2026-09-14")
        self.assertEqual(validate_blocks([first, second]), [])

    def test_a_shared_boundary_day_overlaps(self):
        first = make_block(name="first", starts_on="2026-09-07", ends_on="2026-09-10")
        second = make_block(name="second", starts_on="2026-09-10", ends_on="2026-09-14")
        failures = validate_blocks([first, second])
        self.assertTrue(any(f.other_block is not None for f in failures))

    def test_a_gap_between_blocks_is_normal(self):
        """design-01 §4.3: a gap resolves to preset + base."""
        first = make_block(name="first", starts_on="2026-09-07", ends_on="2026-09-10")
        second = make_block(name="second", starts_on="2026-10-01", ends_on="2026-10-04")
        self.assertEqual(validate_blocks([first, second]), [])

    def test_a_malformed_block_does_not_cascade_into_an_overlap_check(self):
        good = make_block(name="good", starts_on="2026-09-07", ends_on="2026-09-10")
        bad = make_block(name="bad", starts_on="not-a-date", ends_on="2026-09-10")
        failures = validate_blocks([good, bad])
        self.assertFalse(any(f.other_block is not None for f in failures))


# ---------------------------------------------------------------------------
# is_restriction_block — the predicate the successor requirement gates on
# ---------------------------------------------------------------------------


class TestIsRestrictionBlock(unittest.TestCase):
    def test_a_protein_floor_is_a_restriction(self):
        block = make_block(protein_floor={"multiplier": 2.0, "basis": "ffm"})
        self.assertTrue(is_restriction_block(block))

    def test_a_positive_target_rate_is_a_restriction(self):
        block = make_block(target_rate_kg_per_week=1.2)
        self.assertTrue(is_restriction_block(block))

    def test_a_zero_target_rate_is_not_a_restriction(self):
        self.assertFalse(is_restriction_block(make_block(target_rate_kg_per_week=0)))

    def test_a_negative_target_rate_is_not_a_restriction(self):
        """A negative rate ramps calories *up* — the transition's own
        shape, not a further restriction."""
        self.assertFalse(is_restriction_block(make_block(target_rate_kg_per_week=-0.5)))

    def test_diet_styles_alone_is_not_a_restriction(self):
        """dev/PROMPT-13.md step 4's own predicate is a protein_floor and/or
        a deficit-increasing rate — not every dated exception. Fast 800 for
        four days, the design's flagship example, is diet_styles-only."""
        self.assertFalse(is_restriction_block(make_block(diet_styles=["fast_800"])))

    def test_a_transition_block_is_exempt_even_with_a_protein_floor(self):
        """design-01 §4.7: a transition block resolves its own protein
        floor to hold protein constant *while calories ramp up* — the
        opposite of a new restriction, so it must not itself demand a
        further successor."""
        block = make_block(
            block_type="transition", protein_floor={"multiplier": 2.0, "basis": "grams"}
        )
        self.assertFalse(is_restriction_block(block))


# ---------------------------------------------------------------------------
# The required successor
# ---------------------------------------------------------------------------


class TestTheRequiredSuccessor(unittest.TestCase):
    def test_a_restriction_block_with_neither_fails_naming_the_block(self):
        block = make_block(protein_floor={"multiplier": 2.0, "basis": "ffm"})
        failures = validate_blocks([block])
        self.assertTrue(any("restriction block" in f.problem for f in failures))
        self.assertEqual(failures[0].block, "fast-800-kickstart")

    def test_a_named_successor_satisfies_it(self):
        restriction = make_block(
            name="kickstart",
            protein_floor={"multiplier": 2.0, "basis": "ffm"},
            next_block="reverse-diet",
        )
        successor = make_block(
            name="reverse-diet",
            starts_on="2026-09-11",
            ends_on="2026-09-24",
            block_type="transition",
        )
        self.assertEqual(validate_blocks([restriction, successor]), [])

    def test_skip_transition_true_satisfies_it_with_no_successor(self):
        block = make_block(
            protein_floor={"multiplier": 2.0, "basis": "ffm"}, skip_transition=True
        )
        self.assertEqual(validate_blocks([block]), [])

    def test_skip_transition_false_does_not_satisfy_the_requirement(self):
        """An explicit 'false' is not the same as 'true' — it must still
        fail, or a hand-edit flipping the flag off would silently pass."""
        block = make_block(
            protein_floor={"multiplier": 2.0, "basis": "ffm"}, skip_transition=False
        )
        failures = validate_blocks([block])
        self.assertTrue(any("restriction block" in f.problem for f in failures))

    def test_next_block_naming_an_unknown_block_fails(self):
        block = make_block(
            protein_floor={"multiplier": 2.0, "basis": "ffm"}, next_block="does-not-exist"
        )
        failures = validate_blocks([block])
        self.assertTrue(any("does-not-exist" in f.problem for f in failures))

    def test_next_block_naming_itself_fails(self):
        block = make_block(
            name="loop", protein_floor={"multiplier": 2.0, "basis": "ffm"}, next_block="loop"
        )
        failures = validate_blocks([block])
        self.assertTrue(any("itself" in f.problem for f in failures))

    def test_a_non_restriction_block_needs_neither(self):
        self.assertEqual(validate_blocks([make_block(diet_styles=["fast_800"])]), [])

    def test_a_transition_block_needs_no_successor_of_its_own(self):
        self.assertEqual(validate_blocks([make_block(block_type="transition")]), [])

    def test_the_two_states_are_distinguishable_on_disk(self):
        """design-01 §4.7: `skip_transition: true` and a bare absence must
        never look the same — a recorded decision and an oversight are
        different facts, and only the loader can tell them apart if the
        data itself keeps them apart."""
        skipped = make_block(
            protein_floor={"multiplier": 2.0, "basis": "ffm"}, skip_transition=True
        )
        unset = make_block(protein_floor={"multiplier": 2.0, "basis": "ffm"})
        self.assertIs(skipped.get("skip_transition"), True)
        self.assertIsNone(unset.get("skip_transition"))
        self.assertEqual(validate_blocks([skipped]), [])
        self.assertTrue(validate_blocks([unset]))


# ---------------------------------------------------------------------------
# active_block — the date parameter, never the clock itself
# ---------------------------------------------------------------------------


class TestActiveBlock(unittest.TestCase):
    def setUp(self):
        self.blocks = [make_block(starts_on="2026-09-07", ends_on="2026-09-10")]

    def test_a_date_inside_the_span_resolves(self):
        found = active_block(self.blocks, date(2026, 9, 8))
        self.assertEqual(found["name"], "fast-800-kickstart")

    def test_the_start_date_is_inclusive(self):
        self.assertIsNotNone(active_block(self.blocks, date(2026, 9, 7)))

    def test_the_end_date_is_inclusive(self):
        self.assertIsNotNone(active_block(self.blocks, date(2026, 9, 10)))

    def test_a_date_before_the_span_resolves_to_none(self):
        self.assertIsNone(active_block(self.blocks, date(2026, 9, 6)))

    def test_a_date_after_the_span_resolves_to_none(self):
        self.assertIsNone(active_block(self.blocks, date(2026, 9, 11)))

    def test_an_empty_list_resolves_to_none(self):
        self.assertIsNone(active_block([], date(2026, 9, 8)))

    def test_a_block_with_an_unparseable_date_is_skipped_not_raised(self):
        malformed = [make_block(starts_on="not-a-date")]
        self.assertIsNone(active_block(malformed, date(2026, 9, 8)))

    def test_an_expired_block_resolves_inert_for_a_current_date(self):
        expired = [make_block(starts_on="2020-01-01", ends_on="2020-01-04")]
        self.assertIsNone(active_block(expired, date.today()))

    def test_the_date_is_a_parameter_active_block_never_reads_the_clock(self):
        """`active_block` itself takes no clock reading of any kind — only
        the convenience wrapper does. Proven by construction (no `date.today`
        call anywhere in `active_block`), and here by giving it a span wide
        enough that any `on_date` the caller supplies resolves the same way."""
        wide_span = [make_block(starts_on="2020-01-01", ends_on="2099-12-31")]
        self.assertIsNotNone(active_block(wide_span, date(2026, 9, 8)))
        self.assertIsNone(active_block(wide_span, date(1999, 1, 1)))

    def test_active_block_today_defers_to_active_block(self):
        """The seam CLAUDE.md's Tests section pays for: a fixture may call
        `date.today()` (as this one does, via `active_block_today`), but no
        assertion may depend on what it returned — this compares the
        wrapper's answer to the pure function's own answer for the same
        `date.today()`, never to a hardcoded date."""
        wide_span = [make_block(starts_on="2020-01-01", ends_on="2099-12-31")]
        self.assertEqual(active_block_today(wide_span), active_block(wide_span, date.today()))


# ---------------------------------------------------------------------------
# The repository — supplemental store, upsert-by-name
# ---------------------------------------------------------------------------


class BlocksStorageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalJSONRepository(config_dir=self.tmp.name)
        self.addCleanup(self.tmp.cleanup)


class TestBlocksRepositoryRoundTrip(BlocksStorageCase):
    def test_a_missing_file_reads_as_an_empty_list(self):
        self.assertEqual(run_sync(self.repo.load_blocks()), [])

    def test_an_added_block_round_trips(self):
        run_sync(self.repo.save_block(make_block()))
        loaded = run_sync(self.repo.load_blocks())
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "fast-800-kickstart")
        self.assertEqual(loaded[0]["body_goal"], "lose 8 kg")

    def test_saving_again_with_the_same_name_updates_rather_than_appends(self):
        run_sync(self.repo.save_block(make_block(notes="draft")))
        run_sync(self.repo.save_block(make_block(notes="final")))
        loaded = run_sync(self.repo.load_blocks())
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["notes"], "final")

    def test_two_blocks_are_two_rows(self):
        run_sync(self.repo.save_block(make_block(name="first")))
        run_sync(self.repo.save_block(make_block(name="second")))
        loaded = run_sync(self.repo.load_blocks())
        self.assertEqual({row["name"] for row in loaded}, {"first", "second"})

    def test_deleting_removes_exactly_that_row(self):
        run_sync(self.repo.save_block(make_block(name="keep")))
        run_sync(self.repo.save_block(make_block(name="drop")))
        run_sync(self.repo.delete_block("drop"))
        loaded = run_sync(self.repo.load_blocks())
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "keep")

    def test_deleting_an_unknown_name_is_a_no_op(self):
        run_sync(self.repo.save_block(make_block()))
        run_sync(self.repo.delete_block("no-such-block"))
        loaded = run_sync(self.repo.load_blocks())
        self.assertEqual(len(loaded), 1)

    def test_updating_one_row_preserves_its_unrelated_neighbours(self):
        run_sync(self.repo.save_block(make_block(name="first", notes="a")))
        run_sync(self.repo.save_block(make_block(name="second", notes="b")))
        run_sync(self.repo.save_block(make_block(name="first", notes="updated")))
        loaded = {row["name"]: row for row in run_sync(self.repo.load_blocks())}
        self.assertEqual(loaded["first"]["notes"], "updated")
        self.assertEqual(loaded["second"]["notes"], "b")

    def test_a_block_without_a_name_is_refused(self):
        """Loudly, rather than filed under a key nothing reads back — the
        same direction `save_freezer_item` takes for a missing `id`."""
        block = make_block()
        del block["name"]
        with self.assertRaises(ValueError):
            run_sync(self.repo.save_block(block))

    def test_a_hand_added_block_survives_a_write_to_a_different_one(self):
        """The whole point of the upsert-by-name shape over a whole-file
        merge — the task's own acceptance test: writing one block must not
        disturb one this code never parsed."""
        hand_added = {"name": "hand-added", "notes": "typed straight into the file"}
        run_sync(self.repo.save_block(hand_added))
        run_sync(self.repo.save_block(make_block(name="app-written")))
        loaded = {row["name"]: row for row in run_sync(self.repo.load_blocks())}
        self.assertEqual(loaded["hand-added"], hand_added)


class TestBlocksFileLocation(BlocksStorageCase):
    def test_the_file_lives_under_config_dir(self):
        run_sync(self.repo.save_block(make_block()))
        self.assertTrue(Path(self.repo.paths.blocks).exists())
        self.assertEqual(Path(self.repo.paths.blocks).parent, Path(self.tmp.name))

    def test_it_is_not_in_the_core_config_manifest(self):
        """`config/blocks.json` is supplemental, never merged into the five
        core files — it must not appear in the manifest `save_config_keys`
        and preset overrides are checked against."""
        self.assertNotIn("blocks.json", CONFIG_FILES)


if __name__ == "__main__":
    unittest.main()

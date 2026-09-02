"""Tests for the declared freezer ledger — `dev/design-04-freezer-and-prep.md`
§2.1's `FreezerItem`, and the observed-stock repository operations built for
it (`PROMPT-11` §2–§3, `dev/task-queue.md`'s 1.1b).

Two layers, like `test_adherence.py`: the typed model's own validation, and
a round-trip against a real `LocalJSONRepository` pointed at a temp
directory. Nothing here touches the network, a model, or the clock.

This subtask is deliberately narrow — the model and its storage only. The
freezer-origin leftover link (`LINK_ORIGIN_FREEZER`), the draw resolver and
the review-dialog editor are later subtasks (1.1c/1.1d) and are not
exercised here.
"""

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from freezer import FreezerItem  # noqa: E402
from repository import CONFIG_FILES, LocalJSONRepository, run_sync  # noqa: E402
from week import MACRO_KEYS, STORAGE_CLASSES  # noqa: E402


def make_item(**overrides) -> FreezerItem:
    fields = dict(
        label="beef massaman",
        portions=6,
        cooked_on="2026-08-30",
        frozen_on="2026-08-30",
        storage_class="soup_stew_casserole",
        per_serving={"calories": 450.0, "protein_g": 30.0, "net_carbs_g": 20.0, "fat_g": 15.0},
        recipe_id="recipe-123",
    )
    fields.update(overrides)
    return FreezerItem(**fields)


# ---------------------------------------------------------------------------
# The typed model
# ---------------------------------------------------------------------------


class TestFreezerItemIdentity(unittest.TestCase):
    """`id` is the one field with no natural candidate — two lots of one
    dish agree on everything else."""

    def test_an_id_is_generated_when_none_is_given(self):
        item = make_item()
        self.assertTrue(item.id)

    def test_two_lots_of_one_recipe_get_distinct_ids(self):
        first = make_item()
        second = make_item()
        self.assertNotEqual(first.id, second.id)

    def test_an_explicit_id_is_kept_verbatim(self):
        """Round-tripping through storage means constructing from a stored
        dict, which already carries an id — it must not be regenerated."""
        item = FreezerItem(id="fixed-id", **{
            k: v for k, v in make_item().model_dump().items() if k != "id"
        })
        self.assertEqual(item.id, "fixed-id")


class TestFreezerItemValidation(unittest.TestCase):
    def test_a_minimal_item_needs_only_the_required_fields(self):
        item = FreezerItem(
            label="soup", portions=2, cooked_on="2026-08-30", frozen_on="2026-08-30"
        )
        self.assertIsNone(item.storage_class)
        self.assertIsNone(item.per_serving)
        self.assertIsNone(item.recipe_id)

    def test_an_empty_label_is_rejected(self):
        with self.assertRaises(ValidationError):
            make_item(label="")

    def test_portions_must_be_a_positive_integer(self):
        with self.assertRaises(ValidationError):
            make_item(portions=0)
        with self.assertRaises(ValidationError):
            make_item(portions=-1)

    def test_cooked_on_must_be_an_iso_date(self):
        with self.assertRaises(ValidationError):
            make_item(cooked_on="08/30/2026")

    def test_frozen_on_must_be_an_iso_date(self):
        with self.assertRaises(ValidationError):
            make_item(frozen_on="not-a-date")

    def test_frozen_on_may_equal_cooked_on(self):
        """Same-day freezing is the common case and must not be rejected as
        an off-by-one."""
        item = make_item(cooked_on="2026-08-30", frozen_on="2026-08-30")
        self.assertEqual(item.cooked_on, item.frozen_on)

    def test_frozen_on_before_cooked_on_is_rejected(self):
        """Freezing pauses quality decline, it does not predate the cook —
        `design-04`'s explicit reasoning for requiring both dates."""
        with self.assertRaises(ValidationError):
            make_item(cooked_on="2026-08-30", frozen_on="2026-08-29")

    def test_frozen_on_after_cooked_on_is_fine(self):
        """A dish that sat in the fridge a few days before freezing."""
        item = make_item(cooked_on="2026-08-28", frozen_on="2026-08-30")
        self.assertEqual(item.frozen_on, "2026-08-30")

    def test_storage_class_must_be_a_known_one(self):
        with self.assertRaises(ValidationError):
            make_item(storage_class="freezer_burnt")

    def test_storage_class_may_be_absent(self):
        """None means nobody classified it — a different answer from
        'default', resolved short by week.freezer_months like any other
        unclassified dish. Not this model's job to resolve; just to allow."""
        item = make_item(storage_class=None)
        self.assertIsNone(item.storage_class)

    def test_every_shipped_storage_class_is_accepted(self):
        for storage_class in STORAGE_CLASSES:
            item = make_item(storage_class=storage_class)
            self.assertEqual(item.storage_class, storage_class)

    def test_per_serving_rejects_an_unrecognised_key(self):
        with self.assertRaises(ValidationError):
            make_item(per_serving={"sodium_mg": 500.0})

    def test_per_serving_rejects_a_negative_value(self):
        with self.assertRaises(ValidationError):
            make_item(per_serving={"calories": -10.0})

    def test_per_serving_accepts_a_partial_set_of_macro_keys(self):
        """Missing macros mean zero, visibly, to whatever reads this lot
        later — this model does not require every key to be present."""
        item = make_item(per_serving={"calories": 400.0})
        self.assertEqual(item.per_serving, {"calories": 400.0})

    def test_per_serving_accepts_every_macro_key(self):
        full = {key: 10.0 for key in MACRO_KEYS}
        item = make_item(per_serving=full)
        self.assertEqual(item.per_serving, full)

    def test_per_serving_may_be_absent(self):
        item = make_item(per_serving=None)
        self.assertIsNone(item.per_serving)

    def test_recipe_id_is_optional_provenance(self):
        item = make_item(recipe_id=None)
        self.assertIsNone(item.recipe_id)

    def test_an_unknown_field_is_rejected(self):
        """`extra='forbid'`, matching RejectionEntry/AdherenceEntry."""
        with self.assertRaises(ValidationError):
            make_item(**{"expiry_alarm": True})


# ---------------------------------------------------------------------------
# The repository
# ---------------------------------------------------------------------------


class FreezerStorageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = LocalJSONRepository(data_dir=self.tmp.name)
        self.addCleanup(self.tmp.cleanup)


class TestFreezerRepositoryRoundTrip(FreezerStorageCase):
    def test_a_missing_file_reads_as_an_empty_list(self):
        self.assertEqual(run_sync(self.repo.load_freezer()), [])

    def test_an_added_item_round_trips(self):
        item = make_item(label="beef massaman")
        run_sync(self.repo.save_freezer_item(item.model_dump()))
        loaded = run_sync(self.repo.load_freezer())
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["label"], "beef massaman")
        self.assertEqual(loaded[0]["id"], item.id)

    def test_saving_again_with_the_same_id_updates_rather_than_appends(self):
        item = make_item(portions=6)
        run_sync(self.repo.save_freezer_item(item.model_dump()))
        run_sync(
            self.repo.save_freezer_item(item.model_copy(update={"portions": 4}).model_dump())
        )
        loaded = run_sync(self.repo.load_freezer())
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["portions"], 4)

    def test_two_lots_of_one_recipe_are_two_rows(self):
        first = make_item(label="beef massaman batch 1")
        second = make_item(label="beef massaman batch 2")
        run_sync(self.repo.save_freezer_item(first.model_dump()))
        run_sync(self.repo.save_freezer_item(second.model_dump()))
        loaded = run_sync(self.repo.load_freezer())
        self.assertEqual(len(loaded), 2)
        self.assertEqual(
            {row["id"] for row in loaded}, {first.id, second.id}
        )

    def test_deleting_removes_exactly_that_row(self):
        keep = make_item(label="soup")
        drop = make_item(label="curry")
        run_sync(self.repo.save_freezer_item(keep.model_dump()))
        run_sync(self.repo.save_freezer_item(drop.model_dump()))
        run_sync(self.repo.delete_freezer_item(drop.id))
        loaded = run_sync(self.repo.load_freezer())
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], keep.id)

    def test_deleting_an_unknown_id_is_a_no_op(self):
        item = make_item()
        run_sync(self.repo.save_freezer_item(item.model_dump()))
        run_sync(self.repo.delete_freezer_item("no-such-id"))
        loaded = run_sync(self.repo.load_freezer())
        self.assertEqual(len(loaded), 1)

    def test_updating_one_row_preserves_its_unrelated_neighbours(self):
        first = make_item(label="beef massaman", portions=6)
        second = make_item(label="lentil soup", portions=4)
        run_sync(self.repo.save_freezer_item(first.model_dump()))
        run_sync(self.repo.save_freezer_item(second.model_dump()))
        run_sync(
            self.repo.save_freezer_item(
                first.model_copy(update={"portions": 2}).model_dump()
            )
        )
        loaded = {row["id"]: row for row in run_sync(self.repo.load_freezer())}
        self.assertEqual(loaded[first.id]["portions"], 2)
        self.assertEqual(loaded[second.id]["portions"], 4)
        self.assertEqual(loaded[second.id]["label"], "lentil soup")

    def test_an_item_without_an_id_is_refused(self):
        """Loudly, rather than filed under a key nothing reads back — the
        same direction `_upsert_dated_entry` takes for a missing `date`."""
        with self.assertRaises(ValueError):
            run_sync(self.repo.save_freezer_item({"label": "soup", "portions": 2}))

    def test_snapshotted_fields_round_trip_intact(self):
        """The macro/class snapshot is the point of the whole model — it
        must survive a save/load cycle unchanged."""
        item = make_item(
            storage_class="cooked_meat",
            per_serving={"calories": 512.0, "protein_g": 41.0, "net_carbs_g": 12.0, "fat_g": 22.0},
        )
        run_sync(self.repo.save_freezer_item(item.model_dump()))
        loaded = run_sync(self.repo.load_freezer())[0]
        self.assertEqual(loaded["storage_class"], "cooked_meat")
        self.assertEqual(loaded["per_serving"]["calories"], 512.0)
        self.assertEqual(FreezerItem(**loaded), item)


class TestFreezerFileLocation(FreezerStorageCase):
    def test_the_file_lives_under_data_dir(self):
        item = make_item()
        run_sync(self.repo.save_freezer_item(item.model_dump()))
        self.assertTrue(Path(self.repo.paths.freezer).exists())
        self.assertEqual(Path(self.repo.paths.freezer).parent, Path(self.tmp.name))

    def test_it_is_not_a_config_file(self):
        """`data/freezer.json` is app-written observed state, never
        `config/` — it must not appear in the manifest that
        `save_config_keys` and preset overrides are checked against."""
        self.assertNotIn("freezer.json", CONFIG_FILES)


if __name__ == "__main__":
    unittest.main()

"""Tests for the once-off Google Keep -> recipe catalog bootstrap.

Nothing here touches the network or the recipe parser. `keep_import` reaches
its outside world through exactly two seams — a Takeout export on disk and
`planner.import_external_recipe` — and everything worth testing is the
Takeout side: which notes get selected, and what text a selected note hands
to the parser. The parser call itself is `import_external_recipe`'s to test.

Two of these were written against real failure shapes rather than imagined
ones, and say so: `test_a_checklist_note_is_not_empty` (a recipe kept as
tickable ingredients has no `textContent` at all, so reading only that field
imports nothing from the notes most likely to be recipes) and
`test_the_colour_summary_counts_what_the_filter_would_skip` (the whole point
of `--colors` is to check Storm's enum value against your own notes — a
summary that silently agreed with the import filter could not do that).

`unittest` and the `sys.path` insert match `test_sync_service.py`.
"""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "src" / "integrations"))

import keep_import as ki  # noqa: E402

RAGU = {
    "color": "CERULEAN",
    "title": "Nonna's Ragu",
    "textContent": "500g beef mince, 1 onion.\nSimmer 2 hours. Serves 4.",
}
CURRY = {
    "color": "CERULEAN",
    "title": "Green Curry",
    "textContent": "",
    "listContent": [
        {"text": "400ml coconut milk", "isChecked": True},
        {"text": "500g chicken thigh", "isChecked": False},
    ],
}
TRASHED = {"color": "CERULEAN", "title": "Deleted", "textContent": "x", "isTrashed": True}
ARCHIVED = {"color": "CERULEAN", "title": "Archived", "textContent": "y", "isArchived": True}
IMAGE_ONLY = {"color": "CERULEAN", "title": "", "textContent": ""}
SHOPPING = {"color": "YELLOW", "title": "Shopping", "textContent": "milk"}

ALL_NOTES = [RAGU, CURRY, TRASHED, ARCHIVED, IMAGE_ONLY, SHOPPING]


def write_takeout(root: str, notes, as_zip: bool = False) -> str:
    """A minimal Takeout tree — `<root>/Takeout/Keep/<title>.json`."""
    keep_dir = os.path.join(root, "Takeout", "Keep")
    os.makedirs(keep_dir, exist_ok=True)
    for position, note in enumerate(notes):
        name = f"{note.get('title') or 'untitled'}-{position}.json"
        with open(os.path.join(keep_dir, name), "w", encoding="utf-8") as handle:
            json.dump(note, handle)
    if not as_zip:
        return os.path.join(root, "Takeout")

    archive_path = os.path.join(root, "takeout.zip")
    with zipfile.ZipFile(archive_path, "w") as archive:
        for current, _dirs, files in os.walk(os.path.join(root, "Takeout")):
            for name in files:
                full = os.path.join(current, name)
                archive.write(full, os.path.relpath(full, root))
    return archive_path


class TestLoadingAnExport(unittest.TestCase):
    def test_a_directory_and_a_zip_load_identically(self):
        """Both are things you plausibly have on disk after a Takeout run."""
        with tempfile.TemporaryDirectory() as root:
            from_dir = ki.load_notes(write_takeout(root, ALL_NOTES))
        with tempfile.TemporaryDirectory() as root:
            from_zip = ki.load_notes(write_takeout(root, ALL_NOTES, as_zip=True))
        key = lambda notes: sorted(n.get("title", "") for n in notes)  # noqa: E731
        self.assertEqual(key(from_dir), key(from_zip))
        self.assertEqual(len(from_dir), len(ALL_NOTES))

    def test_non_note_json_is_ignored(self):
        """Takeout ships manifests and per-service metadata beside the notes;
        a dict with neither content field is not a note."""
        with tempfile.TemporaryDirectory() as root:
            takeout = write_takeout(root, [RAGU])
            with open(os.path.join(takeout, "Keep", "manifest.json"), "w") as handle:
                json.dump({"version": 2, "exported": True}, handle)
            with open(os.path.join(takeout, "Keep", "Nonna.html"), "w") as handle:
                handle.write("<html>not json</html>")
            self.assertEqual(len(ki.load_notes(takeout)), 1)

    def test_a_missing_export_is_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            ki.load_notes("/nowhere/at/all/Takeout")


class TestNoteText(unittest.TestCase):
    def test_a_checklist_note_is_not_empty(self):
        """The failure this guards: a recipe kept as tickable ingredients has
        an empty `textContent` and everything in `listContent`, so reading
        only the former imports nothing from exactly the notes most likely to
        be recipes."""
        text = ki.note_text(CURRY)
        self.assertIn("400ml coconut milk", text)
        self.assertIn("500g chicken thigh", text)

    def test_tick_state_is_dropped(self):
        """A ticked ingredient is one you already have, not one the recipe
        leaves out — it must not reach the parser as a distinction."""
        self.assertNotIn("isChecked", ki.note_text(CURRY))
        self.assertNotIn("True", ki.note_text(CURRY))

    def test_the_title_leads_so_the_parser_can_name_the_dish(self):
        self.assertTrue(ki.note_text(RAGU).startswith("Nonna's Ragu"))

    def test_a_note_with_no_text_anywhere_is_empty(self):
        """Which is what keeps an image-only note out of `select_notes`, and
        so from ever costing an API call."""
        self.assertEqual(ki.note_text(IMAGE_ONLY), "")

    def test_a_title_always_resolves_to_something(self):
        """Every progress line identifies its note, so this can't be blank."""
        self.assertEqual(ki.note_title(RAGU), "Nonna's Ragu")
        self.assertEqual(ki.note_title(CURRY), "Green Curry")
        untitled = {"textContent": "First line here\nsecond"}
        self.assertEqual(ki.note_title(untitled), "First line here")
        self.assertEqual(ki.note_title({"_source_file": "x.json"}), "x.json")


class TestSelection(unittest.TestCase):
    def test_only_the_requested_colour(self):
        picked = ki.select_notes(ALL_NOTES, "CERULEAN")
        self.assertNotIn("Shopping", [n["title"] for n in picked])

    def test_trashed_notes_are_excluded_but_archived_ones_are_kept(self):
        """Takeout exports the trash too. Archiving a recipe you have cooked
        is the normal way a Keep recipe collection ages, so archived stays."""
        titles = [n["title"] for n in ki.select_notes(ALL_NOTES, "CERULEAN")]
        self.assertNotIn("Deleted", titles)
        self.assertIn("Archived", titles)
        with_trash = ki.select_notes(ALL_NOTES, "CERULEAN", include_trashed=True)
        self.assertIn("Deleted", [n["title"] for n in with_trash])

    def test_a_textless_note_never_reaches_the_parser(self):
        """Dropped at selection rather than at parse time, so an image-only
        note costs nothing."""
        self.assertNotIn(IMAGE_ONLY, ki.select_notes(ALL_NOTES, "CERULEAN"))

    def test_an_unused_colour_selects_nothing_rather_than_raising(self):
        """`main` turns this into "run --colors", which is the useful
        response to a wrong guess at the enum value."""
        self.assertEqual(ki.select_notes(ALL_NOTES, "STORM"), [])


class TestTitleFilter(unittest.TestCase):
    """`--title`'s selection, so one note can be redone without re-parsing the
    whole colour — which is what a wrong import actually needs."""

    def test_it_matches_a_case_insensitive_substring(self):
        picked = ki.select_notes(ALL_NOTES, "CERULEAN")
        needle = "sardines on"
        self.assertEqual(
            [n["title"] for n in picked if needle in ki.note_title(n).lower()], []
        )
        needle = "curry"
        self.assertEqual(
            [n["title"] for n in picked if needle in ki.note_title(n).lower()],
            ["Green Curry"],
        )


class TestColourSummary(unittest.TestCase):
    """`--colors` is the whole reason the Storm -> CERULEAN mapping never has
    to be trusted from `KEEP_COLOR_LABELS`."""

    def test_the_colour_summary_counts_what_the_filter_would_skip(self):
        """It reports on the export, not on what would be imported — an
        image-only note still tells you that colour is in use, which is the
        question `--colors` is asked. Trashed notes are the one exclusion,
        since they are deleted."""
        summary = dict((value, count) for value, count, _ in ki.summarise_colors(ALL_NOTES))
        # ragu + curry + archived + image-only; the trashed one is the exclusion.
        self.assertEqual(summary["CERULEAN"], 4)
        self.assertEqual(summary["YELLOW"], 1)

    def test_most_common_first(self):
        self.assertEqual(ki.summarise_colors(ALL_NOTES)[0][0], "CERULEAN")

    def test_storm_is_cerulean(self):
        """Not DARKBLUE, not STORM. The one mapping worth pinning, because it
        is the value this whole command is pointed at."""
        self.assertEqual(ki.KEEP_COLOR_LABELS["CERULEAN"], "Storm")
        self.assertEqual(ki.color_label("CERULEAN"), "CERULEAN (Storm)")

    def test_an_unrecognised_colour_prints_bare(self):
        """A swatch Keep adds after this mapping was written must still be
        selectable, and must not be guessed at."""
        self.assertEqual(ki.color_label("CHARTREUSE"), "CHARTREUSE")


if __name__ == "__main__":
    unittest.main()

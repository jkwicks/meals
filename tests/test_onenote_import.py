"""Tests for the once-off OneNote -> recipe catalog bootstrap.

Nothing here touches the network or the recipe parser. `onenote_import`
reaches its outside world through exactly two seams — a directory of `.txt`
files on disk and `planner.import_external_recipe` — and everything worth
testing is the directory side: which files get read, and what text a page
hands to the parser. The parser call itself is `import_external_recipe`'s to
test, same division `test_keep_import.py` draws.

`unittest` and the `sys.path` insert match `test_keep_import.py`.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "src" / "integrations"))

import onenote_import as oi  # noqa: E402


def write_pages(root: str, files: dict) -> str:
    """A directory of `.txt` files, `{filename: content}`."""
    for name, content in files.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write(content)
    return root


class TestLoadingADirectory(unittest.TestCase):
    def test_only_txt_files_are_read(self):
        with tempfile.TemporaryDirectory() as root:
            write_pages(
                root,
                {
                    "Apple Pear and Hazelnut Yogurt.txt": "Yogurt, apple, pear, hazelnuts.",
                    "notes.docx": "not a text export",
                    ".DS_Store": "",
                },
            )
            pages = oi.load_pages(root)
            self.assertEqual(len(pages), 1)
            self.assertEqual(pages[0]["title"], "Apple Pear and Hazelnut Yogurt")

    def test_a_subdirectory_is_not_descended_into(self):
        """One run targets one exported section; a nested folder is more
        plausibly a second section than a filing detail to flatten."""
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "Other Section"))
            write_pages(root, {"Ragu.txt": "Beef, onion, tomato."})
            with open(os.path.join(root, "Other Section", "Curry.txt"), "w") as handle:
                handle.write("Chicken, coconut milk.")
            pages = oi.load_pages(root)
            self.assertEqual([p["title"] for p in pages], ["Ragu"])

    def test_files_come_back_sorted_by_name(self):
        with tempfile.TemporaryDirectory() as root:
            write_pages(root, {"Zebra.txt": "z", "Apple.txt": "a", "Mango.txt": "m"})
            self.assertEqual(
                [p["title"] for p in oi.load_pages(root)], ["Apple", "Mango", "Zebra"]
            )

    def test_body_is_stripped_of_surrounding_whitespace(self):
        with tempfile.TemporaryDirectory() as root:
            write_pages(root, {"Ragu.txt": "\n\n  Beef, onion.  \n\n"})
            self.assertEqual(oi.load_pages(root)[0]["body"], "Beef, onion.")

    def test_an_empty_file_has_an_empty_body_not_an_error(self):
        with tempfile.TemporaryDirectory() as root:
            write_pages(root, {"Blank.txt": "   \n  "})
            self.assertEqual(oi.load_pages(root)[0]["body"], "")

    def test_a_missing_directory_is_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            oi.load_pages("/nowhere/at/all/onenote_import")


class TestPageTitle(unittest.TestCase):
    def test_extension_is_dropped(self):
        self.assertEqual(oi.page_title("Nonna's Ragu.txt"), "Nonna's Ragu")

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(oi.page_title("  Ragu.txt"), "Ragu")


class TestPageText(unittest.TestCase):
    def test_a_title_already_leading_the_body_is_not_repeated(self):
        """A page copied whole from OneNote usually already starts with its
        own title — doubling it would just be noise ahead of the parser."""
        page = {"title": "Nonna's Ragu", "body": "Nonna's Ragu\n\n500g beef mince."}
        self.assertEqual(oi.page_text(page), "Nonna's Ragu\n\n500g beef mince.")

    def test_a_body_missing_its_title_gets_it_prepended(self):
        """The failure this guards: a page copied starting mid-recipe (the
        title line didn't survive the paste) must still tell the model what
        dish it's parsing."""
        page = {"title": "Nonna's Ragu", "body": "500g beef mince, 1 onion."}
        self.assertEqual(oi.page_text(page), "Nonna's Ragu\n\n500g beef mince, 1 onion.")

    def test_an_empty_body_falls_back_to_the_title_alone(self):
        page = {"title": "Nonna's Ragu", "body": ""}
        self.assertEqual(oi.page_text(page), "Nonna's Ragu")

    def test_the_title_match_is_case_insensitive(self):
        page = {"title": "nonna's ragu", "body": "Nonna's Ragu\n\n500g beef mince."}
        self.assertEqual(oi.page_text(page), "Nonna's Ragu\n\n500g beef mince.")


class TestTitleFilter(unittest.TestCase):
    """`--title`'s selection, mirrored here at the list level since `main`
    just filters `load_pages`'s output the same way `keep_import` filters
    `select_notes`'s."""

    def test_it_matches_a_case_insensitive_substring(self):
        with tempfile.TemporaryDirectory() as root:
            write_pages(root, {"Green Curry.txt": "x", "Ragu.txt": "y"})
            pages = oi.load_pages(root)
            needle = "curry"
            matched = [p for p in pages if needle in p["title"].lower()]
            self.assertEqual([p["title"] for p in matched], ["Green Curry"])


if __name__ == "__main__":
    unittest.main()

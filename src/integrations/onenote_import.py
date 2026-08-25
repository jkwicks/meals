"""Bootstrap the recipe catalog from a OneNote section exported page-by-page.

A once-off, same spirit as `keep_import.py`: recipes sitting in a OneNote
section, pulled into `data/recipes_master.json` so
`planner.select_favorite_assignments` has something to claim slots with.

    ./venv/bin/python src/integrations/onenote_import.py --dir data/onenote_import --dry-run
    ./venv/bin/python src/integrations/onenote_import.py --dir data/onenote_import

**There is no Takeout-equivalent bulk export for OneNote**, and no consumer
API worth authenticating against for a job that happens once — the live
option is the Microsoft Graph OneNote API, which needs an Azure app
registration and an OAuth consent flow to read a personal notebook, for a
credential that would then sit unused after this one run. So the input this
script reads is the cheapest thing to produce by hand: one `.txt` file per
OneNote page, named after the page (select-all the page's content in
OneNote, copy, paste into `<page title>.txt` inside the target directory).
That is the same trade `keep_import.py`'s docstring makes for the Keep API
versus Takeout, pushed one step further since OneNote doesn't even offer a
Takeout-shaped download.

**The filename is the title, and matters for resuming.** Unlike a Keep note,
a page's text has no separate title field once it's pasted into a `.txt`
file, so the filename is what a re-run matches against the catalog before
paying for a parse call — same reasoning as `keep_import.select_notes`'s
title pre-filter. `--force` turns it off, same flag, same meaning.

**A page that fails to parse must not end the run.** Same policy as "a
failed meal must not fail the week": one badly-OCR'd or oddly-formatted page
must not cost the rest of a batch. Failures are named at the end for a
`--force` re-run after fixing the file.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# See `sync_service.py`: `src/integrations/` is one level below the flat module
# layout the rest of the app relies on, so `src/` has to go on the path by hand
# before any sibling import. Insert rather than append — a stray `repository.py`
# elsewhere on the path must not win over the project's.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from planner import (  # noqa: E402
    api_key_error,
    configure_logging,
    import_external_recipe,
    load_config_with_models,
    short_error,
)
from repository import LocalJSONRepository  # noqa: E402

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is in requirements.txt
    load_dotenv = None


def page_title(filename: str) -> str:
    """The filename, minus its extension, as the page's display title."""
    return Path(filename).stem.strip()


def load_pages(directory: str) -> List[Dict[str, str]]:
    """Every `.txt` file directly inside `directory`, one per OneNote page.

    Not recursive, and deliberately so: one run targets one exported section
    (here, Fast 800), and a subfolder is more plausibly a second section
    somebody will want to import separately than a filing detail to walk
    through automatically.
    """
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"No such directory: {directory}")
    pages: List[Dict[str, str]] = []
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(".txt"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            body = handle.read().strip()
        pages.append({"title": page_title(name), "body": body, "_source_file": name})
    return pages


def page_text(page: Dict[str, str]) -> str:
    """Title plus body, handed to the parser.

    A page pasted from OneNote usually already has its own title as the first
    line, so this doesn't repeat it in that case — but a page copied starting
    mid-recipe (no title line survived the paste) still needs the dish named
    for the model, so the title leads whenever the body doesn't already start
    with it. Same join `keep_import.note_text` does for a Keep note's title
    and body.
    """
    title = page["title"]
    body = page["body"]
    if not body:
        return title
    if body.lower().startswith(title.lower()):
        return body
    return f"{title}\n\n{body}"


def log(message: str, stream=sys.stdout) -> None:
    """Print a progress line and flush it — see `keep_import.log` for why
    this can't be a bare `print`."""
    print(message, file=stream, flush=True)


async def import_pages(
    pages: List[Dict[str, str]],
    repository: LocalJSONRepository,
    favorite: bool = True,
    force: bool = False,
) -> Dict[str, List[str]]:
    """Parse each page into a `Recipe` and add it to the catalog.

    Sequential on purpose — see `keep_import.import_notes`'s docstring, same
    reasoning: `recipe_parser_model` is often a free route, and a burst of
    concurrent calls is the reliable way to turn a working import into a wall
    of 429s halfway through a batch that can't cheaply be resumed mid-page.
    """
    config = await load_config_with_models(repository)
    catalog = await repository.load_recipe_catalog()
    existing = {
        ((record.get("recipe") or {}).get("name") or "").strip().lower()
        for record in catalog
    }

    result: Dict[str, List[str]] = {"imported": [], "skipped": [], "failed": []}
    for position, page in enumerate(pages, start=1):
        title = page["title"]
        if not page["body"]:
            log(f"[{position}/{len(pages)}] skip (empty file): {title}")
            result["skipped"].append(title)
            continue
        if not force and title.strip().lower() in existing:
            log(f"[{position}/{len(pages)}] skip (already in catalog): {title}")
            result["skipped"].append(title)
            continue

        log(f"[{position}/{len(pages)}] parsing: {title}")
        try:
            recipe = await import_external_recipe(
                page_text(page), config=config, repository=repository
            )
        except Exception as exc:
            # One unparseable page must not cost the rest of the run — see
            # the module docstring.
            log(f"    failed: {short_error(exc)}", stream=sys.stderr)
            result["failed"].append(title)
            continue

        await repository.import_recipe(recipe.model_dump(), favorite=favorite)
        log(f"    imported as: {recipe.name} ({recipe.meal_type}, {recipe.servings} serving/s)")
        result["imported"].append(recipe.name)

    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import OneNote recipe pages (exported as one .txt file per page) "
            "into data/recipes_master.json."
        ),
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="Directory containing one .txt file per OneNote page.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the pages that would be imported without calling the parser.",
    )
    parser.add_argument(
        "--no-favorite",
        action="store_true",
        help=(
            "Add to the catalog without favoriting. Favoriting is the default "
            "because only favorites are eligible for slot pre-assignment."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-parse pages whose filename already matches a catalog recipe.",
    )
    parser.add_argument(
        "--title",
        help=(
            "Only pages whose filename contains this (case-insensitive). Pair "
            "with --force to redo one page without re-parsing the whole batch."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Import at most this many pages — worth one pass before the full run.",
    )
    args = parser.parse_args(argv)

    if load_dotenv is not None:
        load_dotenv()

    # See keep_import.main: this CLI is its own entry point and has to attach
    # the log handler itself, or a failed/slow page leaves no record in
    # logs/meals.log to diagnose it from.
    configure_logging()

    try:
        pages = load_pages(os.path.expanduser(args.dir))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not pages:
        print(
            f"No .txt files found directly inside {args.dir}. One file per "
            "OneNote page, named after the page.",
            file=sys.stderr,
        )
        return 1

    if args.title:
        needle = args.title.strip().lower()
        pages = [p for p in pages if needle in p["title"].lower()]
        if not pages:
            print(f"No page filename contains {args.title!r}.", file=sys.stderr)
            return 1
    if args.limit:
        pages = pages[: args.limit]

    print(f"{len(pages)} page(s) in {args.dir}:")
    for page in pages:
        note = "  (empty)" if not page["body"] else ""
        print(f"  - {page['title']}{note}")

    if args.dry_run:
        print("\nDry run — nothing parsed, nothing written.")
        return 0

    key_error = api_key_error()
    if key_error:
        # Up front, not once per page: this is a misconfiguration that will
        # fail every call, and the per-page handler would turn it into one
        # identical failure per recipe after a long wait.
        print(key_error, file=sys.stderr)
        return 1

    repository = LocalJSONRepository()
    print()
    result = asyncio.run(
        import_pages(
            pages,
            repository,
            favorite=not args.no_favorite,
            force=args.force,
        )
    )

    print(
        f"\nImported {len(result['imported'])}, skipped {len(result['skipped'])}, "
        f"failed {len(result['failed'])}."
    )
    if result["failed"]:
        print("Failed pages (re-run with --force after fixing the .txt file):")
        for title in result["failed"]:
            print(f"  - {title}")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())

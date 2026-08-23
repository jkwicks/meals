"""Bootstrap the recipe catalog from colour-tagged Google Keep notes.

A once-off: the recipes that have been sitting in Keep for years, marked with
one note colour, pulled into `data/recipes_master.json` so
`planner.select_favorite_assignments` can start claiming slots with them
instead of asking the model to invent a breakfast it already knows you want.

    ./venv/bin/python src/integrations/keep_import.py --takeout ~/Downloads/Takeout --colors
    ./venv/bin/python src/integrations/keep_import.py --takeout ~/Downloads/Takeout --color CERULEAN --dry-run
    ./venv/bin/python src/integrations/keep_import.py --takeout ~/Downloads/Takeout --color CERULEAN

Five things here are decisions, not detail.

**It reads a Google Takeout export, not the Keep API.** `keep.googleapis.com`
is Workspace-only and needs domain-wide delegation through a service account
— it cannot see a consumer `@gmail.com` account at all, which is the account
these notes are in. The remaining live option is `gkeepapi`, an unofficial
client driving the mobile protocol behind a `gpsoauth` master token: a real
credential to store, an unpinned reverse-engineered dependency to carry, and
a thing that breaks whenever Google ships. Takeout costs one download, needs
no credential, and cannot break — and for a job that by definition happens
once, "repeatable" is not worth paying anything for. If this ever needs to be
a standing sync, `gkeepapi` is the module to reach for and this file's
`note_text`/`import_notes` split is where it would plug in: only `load_notes`
is Takeout-shaped.

**`--colors` exists so the colour mapping is never assumed.** Keep's UI names
its colours (Storm, Sage, Fog) but Takeout writes the *internal* enum
(`CERULEAN`, `TEAL`, `BLUE`), and the two lists do not line up in any order
you would guess — Storm is `CERULEAN`, not `DARKBLUE` or `STORM`. `--colors`
prints every colour actually present with a few note titles under each, so
the right value is read off your own notes in one command rather than trusted
from `KEEP_COLOR_LABELS` below, which is a hint and explicitly not authority.

**A note that fails to parse must not end the run.** Same policy as "a failed
meal must not fail the week": handwritten notes are exactly the input a parser
chokes on, and losing forty good recipes to the one shopping list that got
the colour by accident is the worst outcome after a twenty-minute run. Each
failure is counted, named at the end, and skipped.

**Titles are pre-filtered against the catalog, before any API call.** The
repository already folds a duplicate by `recipe_content_key`, but that is
decided *after* the parse has been paid for — so a re-run after a crash would
re-parse everything it already has. Matching the note title against catalog
recipe names is cheap, needs no new storage, and makes the command resumable.
`--force` turns it off.

**Checklist notes carry their content in `listContent`, not `textContent`.**
A recipe kept as tickable ingredients has an empty `textContent`, and reading
only that field silently imports nothing from exactly the notes most likely
to be recipes. `note_text` reads both.
"""

import argparse
import asyncio
import io
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


# Keep's UI colour names against the enum Takeout actually writes. A hint for
# reading `--colors` output, deliberately NOT the thing the filter matches on:
# `--color` takes the raw enum value, so a Keep release that renames a swatch
# or adds one cannot silently point this at the wrong notes. Storm is
# CERULEAN — verify with `--colors` before trusting it.
KEEP_COLOR_LABELS = {
    "DEFAULT": "White",
    "RED": "Coral",
    "ORANGE": "Peach",
    "YELLOW": "Sand",
    "GREEN": "Mint",
    "TEAL": "Sage",
    "BLUE": "Fog",
    "CERULEAN": "Storm",
    "PURPLE": "Dusk",
    "PINK": "Blossom",
    "BROWN": "Clay",
    "GRAY": "Chalk",
}


def color_label(value: str) -> str:
    """`CERULEAN` -> `CERULEAN (Storm)`, or the bare value when unrecognised.

    An unknown value is printed as-is rather than guessed at — a colour Keep
    added after this mapping was written must still be selectable.
    """
    label = KEEP_COLOR_LABELS.get(value)
    return f"{value} ({label})" if label else value


def load_notes(takeout: str) -> List[Dict[str, Any]]:
    """Every Keep note in a Takeout export, as parsed JSON.

    Accepts the downloaded `.zip` or an unzipped directory, because both are
    things you plausibly have on disk and telling them apart is four lines.
    Searches for a `Keep/` directory anywhere in the tree rather than assuming
    `Takeout/Keep`: the folder is localised in some exports, and a path that
    only works in English is a bad thing to discover half an hour in.

    Notes that fail to parse as JSON are skipped rather than raising — the
    export also contains `.html` siblings and the odd stray file.
    """
    notes: List[Dict[str, Any]] = []

    def add(name: str, raw: bytes) -> None:
        if not name.lower().endswith(".json"):
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        # A Keep note always has these; Takeout's other JSON (archive
        # manifests, per-service metadata) has neither.
        if isinstance(payload, dict) and (
            "textContent" in payload or "listContent" in payload
        ):
            payload.setdefault("_source_file", os.path.basename(name))
            notes.append(payload)

    if zipfile.is_zipfile(takeout):
        with zipfile.ZipFile(takeout) as archive:
            for entry in archive.namelist():
                if "/Keep/" in entry or entry.startswith("Keep/"):
                    add(entry, archive.read(entry))
        return notes

    if not os.path.isdir(takeout):
        raise FileNotFoundError(f"No such Takeout export: {takeout}")

    for root, _dirs, files in os.walk(takeout):
        if os.path.basename(root) != "Keep":
            continue
        for name in files:
            path = os.path.join(root, name)
            with io.open(path, "rb") as handle:
                add(path, handle.read())
    return notes


def note_text(note: Dict[str, Any]) -> str:
    """The text handed to the parser: title, body, and any checklist items.

    A recipe kept as tickable ingredients has an empty `textContent` and all
    of its content in `listContent`, so reading only the former imports
    nothing from exactly the notes most likely to be recipes. Tick state is
    dropped — a ticked ingredient is one you have, not one the recipe omits.
    """
    parts: List[str] = []
    title = (note.get("title") or "").strip()
    if title:
        parts.append(title)
    body = (note.get("textContent") or "").strip()
    if body:
        parts.append(body)
    items = [
        (item.get("text") or "").strip()
        for item in note.get("listContent") or []
        if (item.get("text") or "").strip()
    ]
    if items:
        parts.append("\n".join(items))
    return "\n\n".join(parts).strip()


def note_title(note: Dict[str, Any]) -> str:
    """A human label for logs — the title, else the first body line, else the
    filename. Never empty, because every line this prints identifies which
    note it is talking about."""
    title = (note.get("title") or "").strip()
    if title:
        return title
    text = note_text(note)
    if text:
        first = text.splitlines()[0].strip()
        if first:
            return first[:80]
    return note.get("_source_file") or "(untitled)"


def select_notes(
    notes: Iterable[Dict[str, Any]],
    color: str,
    include_trashed: bool = False,
    include_archived: bool = True,
) -> List[Dict[str, Any]]:
    """Notes of `color` that still have text in them.

    Trashed notes are excluded by default (they are deleted, and Takeout
    exports them anyway); archived ones are kept, since archiving a recipe you
    have cooked is the normal way a Keep recipe collection ages. A note with
    no extractable text is dropped here rather than at parse time so it never
    costs an API call — an image-only note has nothing to parse.
    """
    selected = []
    for note in notes:
        if (note.get("color") or "DEFAULT") != color:
            continue
        if note.get("isTrashed") and not include_trashed:
            continue
        if note.get("isArchived") and not include_archived:
            continue
        if not note_text(note):
            continue
        selected.append(note)
    return selected


def summarise_colors(notes: Iterable[Dict[str, Any]]) -> List[Tuple[str, int, List[str]]]:
    """Every colour present, with counts and a few titles, most common first.

    This is what makes the Storm -> CERULEAN mapping checkable against your
    own notes instead of trusted from `KEEP_COLOR_LABELS`.
    """
    counts: Counter = Counter()
    samples: Dict[str, List[str]] = defaultdict(list)
    for note in notes:
        if note.get("isTrashed"):
            continue
        value = note.get("color") or "DEFAULT"
        counts[value] += 1
        if len(samples[value]) < 4:
            samples[value].append(note_title(note))
    return [(value, count, samples[value]) for value, count in counts.most_common()]


def log(message: str, stream=sys.stdout) -> None:
    """Print a progress line and flush it.

    Python block-buffers stdout whenever it is not a TTY, so a run piped to a
    file or a log shows nothing at all until it finishes — which for a
    sequential 50-note import is fifteen minutes of apparent hang, on the one
    command whose whole reassurance is watching the titles go past. The
    per-note lines are the progress bar; they have to arrive per note.
    """
    print(message, file=stream, flush=True)


async def import_notes(
    notes: List[Dict[str, Any]],
    repository: LocalJSONRepository,
    favorite: bool = True,
    force: bool = False,
) -> Dict[str, List[str]]:
    """Parse each note into a `Recipe` and add it to the catalog.

    Sequential on purpose. `recipe_parser_model` is whatever
    `config/models.json` names, often a free route, and a burst of concurrent
    calls is the reliable way to turn a working import into a wall of 429s
    halfway through — which on a once-off bootstrap costs the whole run's
    remaining notes. A minute of extra wall-clock is the cheaper side of that
    trade.

    Returns imported/skipped/failed titles, so `main` can report each
    independently rather than reducing the run to a single exit code.
    """
    config = await load_config_with_models(repository)
    catalog = await repository.load_recipe_catalog()
    # Seeded from the catalog once and deliberately NOT extended as the run
    # imports: a name collision *within* one run is not a duplicate note, it
    # is a generic list note ("Meals", "Bean Salads") whose extracted dish
    # happens to be named the same as a later note that owns that recipe
    # properly — and skipping the later one keeps the worse copy. That is not
    # hypothetical: "Meals" produced a two-ingredient "Sardines on Toast",
    # which then suppressed the dedicated "Sardines on toast" note thirty
    # notes later. A genuine in-run duplicate now costs one parse call and is
    # folded by `recipe_content_key` in `import_recipe` anyway, which is the
    # far cheaper error of the two.
    existing = {
        ((record.get("recipe") or {}).get("name") or "").strip().lower()
        for record in catalog
    }

    result: Dict[str, List[str]] = {"imported": [], "skipped": [], "failed": []}
    for position, note in enumerate(notes, start=1):
        title = note_title(note)
        if not force and title.strip().lower() in existing:
            log(f"[{position}/{len(notes)}] skip (already in catalog): {title}")
            result["skipped"].append(title)
            continue

        log(f"[{position}/{len(notes)}] parsing: {title}")
        try:
            recipe = await import_external_recipe(
                note_text(note), config=config, repository=repository
            )
        except Exception as exc:
            # One unparseable note must not cost the rest of the run — see the
            # module docstring. Handwritten notes are exactly the input a
            # parser chokes on.
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
            "Import colour-tagged Google Keep recipes from a Takeout export "
            "into data/recipes_master.json."
        ),
    )
    parser.add_argument(
        "--takeout",
        required=True,
        help="Path to the Takeout .zip, or to an unzipped Takeout directory.",
    )
    parser.add_argument(
        "--colors",
        action="store_true",
        help=(
            "List every note colour in the export with counts and sample titles, "
            "then exit. Run this first: Keep's UI name (Storm) is not the value "
            "Takeout writes (CERULEAN)."
        ),
    )
    parser.add_argument(
        "--color",
        help="Takeout colour enum to import, e.g. CERULEAN for Keep's Storm.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the notes that would be imported without calling the parser.",
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
        help="Re-parse notes whose title already matches a catalog recipe.",
    )
    parser.add_argument(
        "--include-trashed",
        action="store_true",
        help="Include notes in the Keep trash, which Takeout exports too.",
    )
    parser.add_argument(
        "--title",
        help=(
            "Only notes whose title contains this (case-insensitive). Pair with "
            "--force to redo one note without re-parsing the whole colour."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Import at most this many notes — worth one pass before the full run.",
    )
    args = parser.parse_args(argv)

    if load_dotenv is not None:
        load_dotenv()

    # `logs/meals.log` is where per-call latency, finish_reason and token
    # counts land (see CLAUDE.md, "Diagnosing a slow or failed call") — and
    # `import_external_recipe` already logs all three. It only reaches the
    # file if a handler is attached, which `planner.main()` and `ui_app.py`
    # each do at import time and this CLI, being a third entry point, has to
    # do for itself. Without it a fifty-note run leaves no record of which
    # note was slow or why one failed, which is exactly the run you want it
    # for.
    configure_logging()

    try:
        notes = load_notes(os.path.expanduser(args.takeout))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not notes:
        print(
            f"No Keep notes found in {args.takeout}. The export must contain a "
            "'Keep' folder — re-run Takeout with Keep selected.",
            file=sys.stderr,
        )
        return 1
    print(f"Found {len(notes)} Keep note(s) in {args.takeout}.\n")

    if args.colors:
        print("Colour                     Notes  Examples")
        for value, count, samples in summarise_colors(notes):
            print(f"{color_label(value):<26} {count:>5}  {'; '.join(samples)}")
        print("\nPass the left-hand value to --color.")
        return 0

    if not args.color:
        parser.error("Nothing to do: pass --color VALUE, or --colors to see what's there.")

    selected = select_notes(notes, args.color, include_trashed=args.include_trashed)
    if args.title:
        needle = args.title.strip().lower()
        selected = [note for note in selected if needle in note_title(note).lower()]
        if not selected:
            print(f"No {color_label(args.color)} note title contains {args.title!r}.", file=sys.stderr)
            return 1
    if not selected:
        print(
            f"No notes coloured {color_label(args.color)}. Run --colors to see "
            "which values this export actually uses.",
            file=sys.stderr,
        )
        return 1
    if args.limit:
        selected = selected[: args.limit]

    print(f"{len(selected)} note(s) coloured {color_label(args.color)}:")
    for note in selected:
        print(f"  - {note_title(note)}")

    if args.dry_run:
        print("\nDry run — nothing parsed, nothing written.")
        return 0

    key_error = api_key_error()
    if key_error:
        # Up front, not once per note: this is a misconfiguration that will
        # fail every call, and the per-note handler would turn it into one
        # identical failure per recipe after a long wait.
        print(key_error, file=sys.stderr)
        return 1

    repository = LocalJSONRepository()
    print()
    result = asyncio.run(
        import_notes(
            selected,
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
        print("Failed notes (re-run with --force after editing them in Keep):")
        for title in result["failed"]:
            print(f"  - {title}")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())

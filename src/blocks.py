"""The block layer — a dated, one-off exception laid over the standing
preset, never a second preset-shaped mechanism.

`dev/design-01-presets-and-blocks.md` §4: once a preset is a **weekly** pick,
a **block** is the pre-commitment device that suspends it — "Fast 800 for
four days" is the design's own example. It carries a small, fixed set of
*intents* (a body goal, a fitness goal, a diet-style activation, a protein
floor, a deficit rate, a training intent, a peak day, notes), each of which
feeds something that already exists. It does **not** carry a `preset` field:
§4.1a's argument is that every other field is a per-day *number*
(`hydrate_dynamic_targets` already computes each of them once per day), while
a preset replaces whole config leaves consumed once, before hydration — so a
block that could pin a preset would need one flat `AppConfig` to be two
different objects inside a single `default_week_spec` call, which nothing in
this codebase does. A block naming `preset` is refused at load, not silently
ignored.

**This module builds the schema, the pure validator and the date resolver
only** — task 3.1a of `dev/task-queue-modified.md`. Mid-week resolution into
`hydrate_dynamic_targets` (3.1b), the frozen protein floor (3.1c), the
`transition` block type's ramp (3.1d) and the Settings surfaces (3.1e) are
later work that imports this module rather than duplicating it; nothing here
reaches into `planner.py` or `nutrition_engine.py`; even `active_block`'s
"today" convenience wrapper never reads the clock inside the pure function
itself — the same seam `build_rejection_rule(today=...)` and
`select_favorite_assignments` already use.

### Storage: a bare list, `data/freezer.json`'s shape, not `presets.json`'s

`design-01` §2's own JSON sketch wraps `blocks.json` as `{"blocks": [...]}`,
mirroring `presets.json`. That sketch also still carries a `"preset"` field on
its example block — it predates the 2026-09-01 correction that removed
`preset` from the field list entirely, so its file-wrapper choice is not
treated as binding here either. What decides the *storage* shape is `design-01`
§4's own later framing: **"a block is a stable-identity record too"** — the
same description `FreezerItem` earns from being addressed by a stable `id`.
Unlike `presets.json`, which pairs a `presets` map with a single `active`
pick that must be read and validated *together*, a block has no file-level
companion key: every block is independently addressable by `name`, and
"which block is active" is derived from dates, never chosen and stored. So
`config/blocks.json` is a bare JSON array, matching `data/freezer.json`
exactly (just relocated to `config/`, since a block is authored by hand or by
the Blocks panel rather than observed), and the repository exposes
`load_blocks`/`save_block`/`delete_block` following `load_freezer`/
`save_freezer_item`/`delete_freezer_item`'s upsert-by-id shape rather than
`save_presets_config`'s whole-object merge — that is what lets a hand-added
block survive an app-driven write to a *different* block untouched.

### Pure, and returning failures rather than raising — same split as `presets.py`

`validate_blocks` computes; it never raises and never touches disk. A future
loader raises on its failures (this app's fail-loudly-at-load policy for
hand-edited config) and the Blocks panel (3.1e) renders the same failures and
declines to write — one function, two presentations, exactly as `presets.py`
already establishes for the reason its own module docstring gives: a second,
separately-written validator would be a second interpretation of "valid",
free to disagree about a file one accepted and the other refused.
"""

from dataclasses import dataclass
from datetime import date as date_type
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# The fixed field list — design-01 §4.1, with the 2026-09-01 corrections
# already folded in: body_goal/fitness_goal both required, preset absent.
# ---------------------------------------------------------------------------

NAME_KEY = "name"
STARTS_ON_KEY = "starts_on"
ENDS_ON_KEY = "ends_on"
BODY_GOAL_KEY = "body_goal"
FITNESS_GOAL_KEY = "fitness_goal"
DIET_STYLES_KEY = "diet_styles"
PROTEIN_FLOOR_KEY = "protein_floor"
TARGET_RATE_KEY = "target_rate_kg_per_week"
TRAINING_INTENT_KEY = "training_intent"
PEAK_DAY_KEY = "peak_day"
NOTES_KEY = "notes"
NEXT_BLOCK_KEY = "next_block"
SKIP_TRANSITION_KEY = "skip_transition"
BLOCK_TYPE_KEY = "block_type"

# The four fields with no safe default — a block missing any of these cannot
# express what design-01 §4.1 says a block *is*.
REQUIRED_STRING_FIELDS: Tuple[str, ...] = (NAME_KEY, BODY_GOAL_KEY, FITNESS_GOAL_KEY)
REQUIRED_DATE_FIELDS: Tuple[str, ...] = (STARTS_ON_KEY, ENDS_ON_KEY)
REQUIRED_FIELDS: Tuple[str, ...] = REQUIRED_STRING_FIELDS + REQUIRED_DATE_FIELDS

# name/starts_on/ends_on/body_goal/fitness_goal plus the six optional fields
# design-01 §4.1 names, plus next_block/skip_transition/block_type — the
# successor mechanism §4.7 needs and the `transition` type §4.7 names.
ALLOWED_FIELDS: Tuple[str, ...] = REQUIRED_FIELDS + (
    DIET_STYLES_KEY,
    PROTEIN_FLOOR_KEY,
    TARGET_RATE_KEY,
    TRAINING_INTENT_KEY,
    PEAK_DAY_KEY,
    NOTES_KEY,
    NEXT_BLOCK_KEY,
    SKIP_TRANSITION_KEY,
    BLOCK_TYPE_KEY,
)

# A block cannot pin a preset — design-01 §4.1a. Named separately from
# ALLOWED_FIELDS so a block naming it gets a message that explains *why*,
# not just "unrecognised field".
FORBIDDEN_FIELDS: Tuple[str, ...] = ("preset",)

# The one block type this design specifies beyond an ordinary/restriction
# block — §4.7's ramp, implemented in 3.1d. Anything else in `block_type` is
# a typo, not a third kind nobody has designed.
TRANSITION_BLOCK_TYPE = "transition"
KNOWN_BLOCK_TYPES: Tuple[str, ...] = (TRANSITION_BLOCK_TYPE,)

# `protein_floor.basis` — design-01 §6.
PROTEIN_FLOOR_BASES: Tuple[str, ...] = ("target_weight", "ffm", "current_weight", "grams")


@dataclass(frozen=True)
class BlockFailure:
    """One reason `blocks.json` cannot be used, addressed to a human.

    Mirrors `presets.PresetFailure`'s `problem`/name-carrying shape, with
    `other_block` beside `block` rather than `preset`/`path`: the one failure
    that names two records is an overlap, not a nested path.
    """

    problem: str
    block: Optional[str] = None
    other_block: Optional[str] = None

    @property
    def message(self) -> str:
        if self.block is not None and self.other_block is not None:
            where = f"blocks '{self.block}' and '{self.other_block}': "
        elif self.block is not None:
            where = f"block '{self.block}': "
        else:
            where = ""
        return f"{where}{self.problem}"

    def __str__(self) -> str:  # so "\n".join(failures) reads correctly
        return self.message


def _fail(problem: str, block: Optional[str] = None, other_block: Optional[str] = None) -> BlockFailure:
    return BlockFailure(problem=problem, block=block, other_block=other_block)


def _parse_date(value: Any) -> Optional[date_type]:
    """`date.fromisoformat`, tolerant — returns None rather than raising, so
    callers that must not raise (`active_block`) can skip an unparseable
    entry instead of crashing on data `validate_blocks` will separately
    report."""
    if not isinstance(value, str):
        return None
    try:
        return date_type.fromisoformat(value)
    except ValueError:
        return None


def is_restriction_block(block: dict) -> bool:
    """Whether `block` is a restriction block under design-01 §4.7 — the
    predicate `dev/PROMPT-13.md` step 4 leaves to implementation, with the
    one stated constraint that it must not be "every block".

    A block declaring a `protein_floor`, or a `target_rate_kg_per_week` that
    *increases* the deficit (a positive rate: losing weight faster than
    maintenance), is the shape the research's post-restriction risk applies
    to — see §4.7's adipostat/proteinstat argument. A block naming only
    `diet_styles`/`training_intent`/`peak_day`/`notes` is not: it has nothing
    for a transition to ramp back from.

    A `transition`-type block is exempt even if it happens to declare its
    own `protein_floor` — §4.7 says a transition block resolves one to hold
    protein constant *while calories ramp back up*, which is the opposite of
    a new restriction and must not itself demand a further successor.
    """
    if block.get(BLOCK_TYPE_KEY) == TRANSITION_BLOCK_TYPE:
        return False
    if block.get(PROTEIN_FLOOR_KEY) is not None:
        return True
    rate = block.get(TARGET_RATE_KEY)
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        return False
    return rate > 0


def _protein_floor_failures(protein_floor: Any, display: str) -> List[BlockFailure]:
    if not isinstance(protein_floor, dict):
        return [
            _fail(
                f"'{PROTEIN_FLOOR_KEY}' must be an object ({{multiplier, basis}}), "
                f"got {type(protein_floor).__name__}.",
                block=display,
            )
        ]
    failures: List[BlockFailure] = []
    basis = protein_floor.get("basis")
    if basis not in PROTEIN_FLOOR_BASES:
        failures.append(
            _fail(
                f"'{PROTEIN_FLOOR_KEY}.basis' {basis!r} is not one of {PROTEIN_FLOOR_BASES}.",
                block=display,
            )
        )
    multiplier = protein_floor.get("multiplier")
    if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or multiplier <= 0:
        failures.append(
            _fail(f"'{PROTEIN_FLOOR_KEY}.multiplier' must be a positive number.", block=display)
        )
    return failures


def _record_failures(block: dict, display: str, known_names: set) -> List[BlockFailure]:
    """Everything wrong with one block entry, in isolation from the rest of
    the file (overlap and duplicate-name checks need the whole list and live
    in `validate_blocks`)."""
    failures: List[BlockFailure] = []

    for key in FORBIDDEN_FIELDS:
        if key in block:
            failures.append(
                _fail(
                    f"carries a '{key}' field — a block cannot pin a preset "
                    "(design-01 §4.1a): the preset stays the weekly pick, "
                    "unmoved by any block.",
                    block=display,
                )
            )

    unknown = sorted(set(block) - set(ALLOWED_FIELDS) - set(FORBIDDEN_FIELDS))
    for key in unknown:
        failures.append(_fail(f"has an unrecognised field '{key}'.", block=display))

    for key in REQUIRED_STRING_FIELDS:
        value = block.get(key)
        if not isinstance(value, str) or not value.strip():
            failures.append(_fail(f"needs a non-empty '{key}'.", block=display))

    starts = block.get(STARTS_ON_KEY)
    ends = block.get(ENDS_ON_KEY)
    parsed_starts = _parse_date(starts)
    parsed_ends = _parse_date(ends)
    if parsed_starts is None:
        failures.append(_fail(f"'{STARTS_ON_KEY}' must be an ISO date, got {starts!r}.", block=display))
    if parsed_ends is None:
        failures.append(_fail(f"'{ENDS_ON_KEY}' must be an ISO date, got {ends!r}.", block=display))
    if parsed_starts is not None and parsed_ends is not None and parsed_ends < parsed_starts:
        failures.append(
            _fail(f"'{ENDS_ON_KEY}' ({ends}) is before '{STARTS_ON_KEY}' ({starts}).", block=display)
        )

    if DIET_STYLES_KEY in block and not isinstance(block[DIET_STYLES_KEY], list):
        failures.append(
            _fail(
                f"'{DIET_STYLES_KEY}' must be a list — the same shape "
                "dietary_rules.active_diet_styles takes.",
                block=display,
            )
        )

    protein_floor = block.get(PROTEIN_FLOOR_KEY)
    if protein_floor is not None:
        failures.extend(_protein_floor_failures(protein_floor, display))

    rate = block.get(TARGET_RATE_KEY)
    if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float))):
        failures.append(
            _fail(f"'{TARGET_RATE_KEY}' must be a number, got {type(rate).__name__}.", block=display)
        )

    for key in (TRAINING_INTENT_KEY, PEAK_DAY_KEY, NOTES_KEY):
        value = block.get(key)
        if value is not None and not isinstance(value, str):
            failures.append(_fail(f"'{key}' must be a string, got {type(value).__name__}.", block=display))

    block_type = block.get(BLOCK_TYPE_KEY)
    if block_type is not None and block_type not in KNOWN_BLOCK_TYPES:
        failures.append(
            _fail(f"'{BLOCK_TYPE_KEY}' {block_type!r} is not one of {KNOWN_BLOCK_TYPES}.", block=display)
        )

    next_block = block.get(NEXT_BLOCK_KEY)
    name = block.get(NAME_KEY)
    if next_block is not None:
        if not isinstance(next_block, str) or not next_block.strip():
            failures.append(_fail(f"'{NEXT_BLOCK_KEY}' must be a non-empty block name.", block=display))
        elif next_block == name:
            failures.append(_fail(f"'{NEXT_BLOCK_KEY}' cannot name itself.", block=display))
        elif next_block not in known_names:
            failures.append(
                _fail(
                    f"'{NEXT_BLOCK_KEY}' names {next_block!r}, which is not a block in this file.",
                    block=display,
                )
            )

    skip_transition = block.get(SKIP_TRANSITION_KEY)
    if skip_transition is not None and not isinstance(skip_transition, bool):
        failures.append(
            _fail(
                f"'{SKIP_TRANSITION_KEY}' must be a boolean, got {type(skip_transition).__name__}.",
                block=display,
            )
        )

    # The required successor — design-01 §4.7. `skip_transition: true` and a
    # missing successor must never look the same on disk, which is exactly
    # why this checks for the *field's presence*, not "does the week look
    # fine without one": an oversight and a recorded decision are different
    # facts and this is the one place that can tell them apart.
    if is_restriction_block(block):
        has_successor = isinstance(next_block, str) and bool(next_block.strip())
        explicitly_skipped = skip_transition is True
        if not has_successor and not explicitly_skipped:
            failures.append(
                _fail(
                    "is a restriction block (declares a protein_floor and/or a "
                    "target_rate_kg_per_week that increases the deficit) and must "
                    f"name a '{NEXT_BLOCK_KEY}' or set '{SKIP_TRANSITION_KEY}: "
                    "true' — neither is present. design-01 §4.7: the end of a "
                    "restriction block is the highest-risk moment in the "
                    "protocol, so skipping the transition takes an explicit, "
                    "recorded override, never a silent absence.",
                    block=display,
                )
            )

    return failures


def validate_blocks(blocks_config: Optional[List[dict]]) -> List[BlockFailure]:
    """Everything wrong with `blocks.json`, structurally.

    **Every block is checked, not only the one covering today** — the same
    policy `presets.validate_presets_config` states for the same reason: a
    block that starts next month is worth knowing is broken now, and the
    Blocks panel (3.1e) needs the identical verdict for a block it is about
    to save.

    `blocks_config` is the file's parsed contents (or `None`/`[]` for a
    missing/empty file, both the same "no declared blocks" answer). Pure —
    never raises, never touches disk; a future loader raises on its
    failures and the panel renders them, exactly as `presets.py` already
    does for its own file.
    """
    if blocks_config in (None, []):
        return []
    if not isinstance(blocks_config, list):
        return [_fail(f"blocks.json must contain a JSON array, got {type(blocks_config).__name__}.")]

    known_names = {
        block.get(NAME_KEY)
        for block in blocks_config
        if isinstance(block, dict) and isinstance(block.get(NAME_KEY), str) and block.get(NAME_KEY).strip()
    }

    failures: List[BlockFailure] = []
    name_counts: Dict[str, int] = {}
    dated: List[Tuple[str, date_type, date_type]] = []

    for index, block in enumerate(blocks_config):
        placeholder = f"<entry {index}>"
        if not isinstance(block, dict):
            failures.append(_fail(f"must be an object, got {type(block).__name__}.", block=placeholder))
            continue

        name = block.get(NAME_KEY)
        display = name if isinstance(name, str) and name.strip() else placeholder
        failures.extend(_record_failures(block, display, known_names))

        if isinstance(name, str) and name.strip():
            name_counts[name] = name_counts.get(name, 0) + 1

        starts = _parse_date(block.get(STARTS_ON_KEY))
        ends = _parse_date(block.get(ENDS_ON_KEY))
        if starts is not None and ends is not None and starts <= ends:
            dated.append((display, starts, ends))

    for name, count in name_counts.items():
        if count > 1:
            failures.append(_fail(f"is used by {count} blocks — a block name must be unique.", block=name))

    # Overlap: two blocks covering one date fail, naming both and the
    # overlapping range — never pick a winner (design-01 §4.3, the same
    # answer `reconcile_adaptive_tdee` gives two disagreeing TDEE figures).
    # Compared pairwise over every well-dated block, expiry included: an
    # expired block is inert going forward but its stored dates still
    # overlapped whatever they overlapped, which is a fact about the file,
    # not about the clock.
    for i in range(len(dated)):
        name_a, starts_a, ends_a = dated[i]
        for j in range(i + 1, len(dated)):
            name_b, starts_b, ends_b = dated[j]
            overlap_start = max(starts_a, starts_b)
            overlap_end = min(ends_a, ends_b)
            if overlap_start <= overlap_end:
                failures.append(
                    _fail(
                        f"overlap {overlap_start.isoformat()} to {overlap_end.isoformat()}.",
                        block=name_a,
                        other_block=name_b,
                    )
                )

    return failures


def active_block(blocks: List[dict], on_date: date_type) -> Optional[dict]:
    """The block covering `on_date`, or `None` if no block does.

    Pure — `on_date` is a parameter, never read from the clock here (see
    `active_block_today` for the convenience wrapper). `validate_blocks`
    already refuses an overlapping file, so at most one block should ever
    match; this returns the first regardless, and tolerates unparseable
    dates by skipping them rather than raising, since a resolver reading
    genuinely invalid data has an honest answer ("no block covers this
    day") where raising would take an unrelated day's planning down with a
    typo in someone else's block.

    A day covered by no block is not a special case: it resolves to
    preset + base, the same path a config with no `blocks.json` at all
    takes (design-01 §4.3 — "a gap is the normal state").
    """
    for block in blocks:
        if not isinstance(block, dict):
            continue
        starts = _parse_date(block.get(STARTS_ON_KEY))
        ends = _parse_date(block.get(ENDS_ON_KEY))
        if starts is not None and ends is not None and starts <= on_date <= ends:
            return block
    return None


def active_block_today(blocks: List[dict]) -> Optional[dict]:
    """`active_block` against the real clock — the one place in this module
    that reads it. The convenience wrapper `design-01` §4.4 asks for,
    matching the seam `build_rejection_rule(today=...)` and
    `select_favorite_assignments` already use: a fixture may call
    `date.today()`, but no assertion may depend on what it returned."""
    return active_block(blocks, date_type.today())

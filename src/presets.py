"""The preset layer — one named set of overrides, laid over the merged config.

`config/` holds one implicit profile smeared across five files. A **preset**
names it: a label plus a map of dotted leaf paths to the values that week
wants instead. `presets.json` is a *supplemental* file (like `models.json` and
`integrations.json`), so it is deliberately absent from `CONFIG_FILES` and its
keys never appear in `AppConfig` — the two-tier rule decides it, and the test
is what a missing file means. A missing core file is fatal because every core
key has no safe default; a missing supplemental file resolves to `{}` because
every value in it has an in-code fallback. **A checkout with no `presets.json`
plans exactly as it did before this module existed.**

The load order this sits in the middle of:

    1. merge the five core files      -> base dict   (CONFIG_FILES manifest)
    2. resolve the active preset      -> preset layer  (here)
    3. validate                       -> AppConfig (extra="forbid")

**Validation moves to after the layer, and that is the real change.** A preset
overriding a key *after* validation could introduce a state `AppConfig` would
have rejected, so validating last is the only ordering where `extra="forbid"`
still means anything.

### Leaf paths, not top-level keys

An override addresses one leaf — `"dietary_rules.allowed_nova_groups"` — and
replaces **that leaf**, whole. There is no recursive merge anywhere: a merge
cannot express deletion, and it makes "what does this preset actually plan
against" unanswerable without replaying it. An override valued `[]` or `{}` is
an explicit value, never an absence.

The granularity was a bug once and is worth keeping the refutation for. The
first design said *whole-key* replacement, borrowing `CONFIG_FILES`'
granularity, and the shipped `comfort` preset broke it on its first line:

    "comfort": {"keys": {"dietary_rules": {"allowed_nova_groups": [1,2,3,4]}}}

`DietaryRules` has no required fields — all three carry a `default_factory` —
so that object **validates cleanly** as the week's whole `dietary_rules` and
silently discards 17 `banned_ingredients` entries and `active_diet_styles`. A
"take it easy" preset would unban every ingredient the user had ever excluded,
with no error and nothing in the pick's diff line to show it. The manifest's
granularity answers *"which file owns this key"*; a preset answers *"what is
this preset's opinion"*, and `dietary_rules` bundles three unrelated opinions
that merely share a file.

### The baseline is the base config, never the preset named `default`

`default` is **a row in the file, not a built-in**. Nothing here treats any
name as special: `default` reproduces today's behaviour because its
`overrides` are empty, not because the code falls back to it. That is also
what keeps it deletable — diffing against another *row* would go blank the
moment that row was edited away, and `active` would dangle with it. Every
comparison in this module is against the base config, which cannot be deleted
because it is the thing presets layer over.

### Pure, and returning failures rather than raising

`resolve_config` computes; it never raises on a bad file and never touches
disk. The *loader* raises on its failures (this app's fail-loudly-at-load
policy for hand-edited files) and the preset editor renders the same failures
and declines to write — **same check, two presentations**. A resolver here and
a separate validator in the editor would be two interpretations of "valid",
free to disagree about a file one accepted and the other refused.

Nothing in here imports NiceGUI, `PlannerState` or `planner`, so `api.py` and
the editor can both reach it.
"""

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from repository import CONFIG_KEY_OWNER

# The three keys `presets.json` itself uses. Named rather than spelled at each
# site for the reason `ui_theme.APP_NAME` is: `active` is read by the loader,
# written by the weekly pick and offered by the editor, and a fourth literal
# is a fourth thing to miss when the shape moves.
ACTIVE_KEY = "active"
PRESETS_KEY = "presets"
OVERRIDES_KEY = "overrides"
LABEL_KEY = "label"

# Where the resolved pick rides on the config dict — runtime-injected, in
# memory only, exactly like `nudge_foods`, `target_locks` and `storage_spans`.
# It is added *after* `AppConfig` validation (which would strip an unknown
# key on `model_dump`) and is never written to `config/` by
# `save_config_keys`, which merges only the named core keys it is handed.
ACTIVE_PRESET_CONFIG_KEY = "active_preset"


@dataclass(frozen=True)
class PresetFailure:
    """One reason a presets file cannot be used, addressed to a human.

    `preset` and `path` are optional because the three failure classes have
    different scopes: the file itself can be malformed (neither), a preset
    entry can be (preset only), and an override path can be (both).
    """

    problem: str
    preset: Optional[str] = None
    path: Optional[str] = None

    @property
    def message(self) -> str:
        where = ""
        if self.preset is not None and self.path is not None:
            where = f"preset '{self.preset}', override '{self.path}': "
        elif self.preset is not None:
            where = f"preset '{self.preset}': "
        return f"{where}{self.problem}"

    def __str__(self) -> str:  # so "\n".join(failures) reads correctly
        return self.message


@dataclass(frozen=True)
class PresetResolution:
    """What the layer produced: the config, which preset made it, what broke.

    `config` is the base dict untouched when nothing applied — not a copy —
    so the no-presets path is byte-identical to not having called this at all.
    """

    config: dict
    active: Optional[str] = None
    failures: Tuple[PresetFailure, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures


def _fail(problem: str, preset: Optional[str] = None, path: Optional[str] = None):
    return PresetFailure(problem=problem, preset=preset, path=path)


def preset_entries(presets_config: Optional[dict]) -> Dict[str, dict]:
    """The `presets` map, or `{}` for an absent or malformed file.

    Tolerant on purpose: this is the reader every *display* surface uses, and
    a listing that raised would take the editor down with the file it exists
    to repair. `validate_presets_config` is where malformedness is reported.
    """
    if not isinstance(presets_config, dict):
        return {}
    entries = presets_config.get(PRESETS_KEY)
    if not isinstance(entries, dict):
        return {}
    return {name: entry for name, entry in entries.items() if isinstance(entry, dict)}


def preset_label(presets_config: Optional[dict], name: Optional[str]) -> Optional[str]:
    """A preset's own `label`, falling back to its name.

    The name is the identity (it is what `active` stores and what a week
    records); the label is what a reader sees. A preset with no label is
    legal and shows as its name rather than as nothing.
    """
    if name is None:
        return None
    entry = preset_entries(presets_config).get(name)
    if not isinstance(entry, dict):
        return name
    label = entry.get(LABEL_KEY)
    return label if isinstance(label, str) and label.strip() else name


def active_preset_name(presets_config: Optional[dict]) -> Optional[str]:
    """This week's pick, or None when the file names none.

    `active` absent and `active` null are the same answer — nobody has
    chosen — which is the state a checkout with no file is already in.
    """
    if not isinstance(presets_config, dict):
        return None
    active = presets_config.get(ACTIVE_KEY)
    return active if isinstance(active, str) and active else None


def preset_overrides(presets_config: Optional[dict], name: Optional[str]) -> dict:
    """One preset's `overrides` map, or `{}`."""
    entry = preset_entries(presets_config).get(name or "")
    overrides = entry.get(OVERRIDES_KEY) if isinstance(entry, dict) else None
    return overrides if isinstance(overrides, dict) else {}


def validate_presets_config(presets_config: Optional[dict]) -> List[PresetFailure]:
    """Everything wrong with the file, structurally — before any base config.

    **Every preset is checked, not only the active one.** A preset you might
    pick next Monday is worth knowing is broken now rather than at the moment
    you reach for it, and the editor needs the same verdict for a preset it is
    about to save. It costs the usual price of this codebase's loud-at-load
    policy: a typo in a preset nobody is using still stops the app, exactly as
    a typo in `profile.json` does.
    """
    failures: List[PresetFailure] = []
    if presets_config in (None, {}):
        return failures
    if not isinstance(presets_config, dict):
        return [_fail(f"presets.json must contain a JSON object, got "
                      f"{type(presets_config).__name__}.")]

    entries = presets_config.get(PRESETS_KEY, {})
    if not isinstance(entries, dict):
        failures.append(_fail(f"'{PRESETS_KEY}' must be an object mapping a preset "
                              f"name to its definition, got {type(entries).__name__}."))
        entries = {}

    for name, entry in entries.items():
        if not isinstance(entry, dict):
            failures.append(_fail(
                f"must be an object, got {type(entry).__name__}.", preset=name))
            continue
        overrides = entry.get(OVERRIDES_KEY, {})
        if not isinstance(overrides, dict):
            failures.append(_fail(
                f"'{OVERRIDES_KEY}' must be an object mapping a dotted config path "
                f"to its value, got {type(overrides).__name__}.", preset=name))
            continue
        for path in overrides:
            failures.extend(_path_failures(name, path))

    active = presets_config.get(ACTIVE_KEY)
    if active is not None and not isinstance(active, str):
        failures.append(_fail(f"'{ACTIVE_KEY}' must be a preset name or null, got "
                              f"{type(active).__name__}."))
    elif isinstance(active, str) and active and active not in entries:
        known = ", ".join(sorted(entries)) or "none"
        failures.append(_fail(
            f"'{ACTIVE_KEY}' names '{active}', which is not a preset in this file. "
            f"Known presets: {known}."))
    return failures


def _path_failures(preset: str, path: Any) -> List[PresetFailure]:
    """Whether `path` is a usable dotted path into the merged config.

    Only the **first** segment is a `CONFIG_FILES` question, because only the
    first segment is a question about file ownership. A typo'd first segment
    is checked here and loudly, because the alternative is a preset that
    appears to be applied and is not — strictly worse than one that refuses to
    load, and the same argument `CONFIG_FILES` already makes about a key in
    the wrong file.
    """
    if not isinstance(path, str) or not path.strip():
        return [_fail("an override path must be a non-empty string.", preset=preset,
                      path=str(path))]
    segments = path.split(".")
    if any(not segment.strip() for segment in segments):
        return [_fail("an override path may not contain an empty segment.",
                      preset=preset, path=path)]
    root = segments[0]
    if root not in CONFIG_KEY_OWNER:
        known = ", ".join(sorted(CONFIG_KEY_OWNER))
        return [_fail(
            f"'{root}' is not a known config key. The first segment of an override "
            f"path must be a top-level key CONFIG_FILES owns. Known keys: {known}.",
            preset=preset, path=path)]
    return []


def apply_overrides(
    base: dict, overrides: dict, preset: Optional[str] = None
) -> Tuple[dict, List[PresetFailure]]:
    """`base` with each override's leaf replaced whole. Pure; `base` untouched.

    Every segment *before* the last must already exist and be an object: a
    path describing a branch that is not there is structurally wrong, and
    creating it silently would write a value into a branch nothing reads —
    the "appears applied and is not" failure again. The **last** segment need
    not exist, because that is how a preset states an optional key the base
    file leaves at its `AppConfig` default.
    """
    failures: List[PresetFailure] = []
    if not overrides:
        return base, failures

    resolved = copy.deepcopy(base)
    for path, value in overrides.items():
        path_failures = _path_failures(preset or "", path)
        if path_failures:
            failures.extend(path_failures)
            continue
        segments = str(path).split(".")
        node = resolved
        walked: List[str] = []
        for segment in segments[:-1]:
            walked.append(segment)
            child = node.get(segment) if isinstance(node, dict) else None
            if not isinstance(child, dict):
                failures.append(_fail(
                    f"'{'.'.join(walked)}' is not an object in the base config, so "
                    f"there is nothing at this path to override.",
                    preset=preset, path=path))
                node = None
                break
            node = child
        if node is None:
            continue
        # Replaced whole, and deep-copied: the value now belongs to the config
        # and must not alias the presets document, which the editor may go on
        # to write back to disk.
        node[segments[-1]] = copy.deepcopy(value)
    return resolved, failures


def resolve_config(base: dict, presets_config: Optional[dict]) -> PresetResolution:
    """Lay the active preset over `base`. The one resolver; nothing else layers.

    Returns the base dict *itself* when nothing applies, so the no-file and
    empty-`default` paths are byte-identical to never having layered at all —
    which is the compatibility claim this whole arm is accepted against.
    """
    failures = validate_presets_config(presets_config)
    if failures:
        return PresetResolution(config=base, active=None, failures=tuple(failures))

    active = active_preset_name(presets_config)
    if active is None:
        return PresetResolution(config=base, active=None)

    overrides = preset_overrides(presets_config, active)
    resolved, apply_failures = apply_overrides(base, overrides, preset=active)
    if apply_failures:
        return PresetResolution(config=base, active=None, failures=tuple(apply_failures))
    return PresetResolution(config=resolved, active=active)


# --------------------------------------------------------------------------
# What a pick changed — the diff a reader is owed
# --------------------------------------------------------------------------


def format_value(value: Any) -> str:
    """A value, short enough for one line of a pick's diff.

    Generic rather than per-key on purpose: a phrase table mapping
    `allowed_nova_groups` to "NOVA 4 allowed" would be exactly the hard-coded
    knowledge about presets §3.4 rules out, and it goes stale the moment a
    preset states a key the table never heard of.
    """
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None:
        return "none"
    if isinstance(value, list):
        if not value:
            return "empty"
        # Four before truncating, not three: `[1, 2, 3, 4]` is the shipped
        # `comfort` preset's whole statement, and "1, 2, 3, +1 more" reads as
        # a list cut short rather than as the one group it added.
        if len(value) <= 4:
            return ", ".join(format_value(item) for item in value)
        shown = [format_value(item) for item in value[:3]]
        shown.append(f"+{len(value) - 3} more")
        return ", ".join(shown)
    if isinstance(value, dict):
        if not value:
            return "empty"
        keys = list(value)
        shown = ", ".join(str(key) for key in keys[:3])
        return shown + (f" +{len(keys) - 3} more" if len(keys) > 3 else "")
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def value_at(config: dict, path: str) -> Tuple[bool, Any]:
    """`(found, value)` for a dotted path — `found` distinguishes a stored
    `None` from a path the base config does not carry at all."""
    node: Any = config
    for segment in str(path).split("."):
        if not isinstance(node, dict) or segment not in node:
            return False, None
        node = node[segment]
    return True, node


def preset_changes(base: dict, presets_config: Optional[dict], name: Optional[str]):
    """One line per override that actually differs from the **base config**.

    Never against the preset named `default` — see the module docstring. An
    override restating what the base already says produces no line, so a
    preset whose overrides are all no-ops reads as changing nothing, which is
    the honest answer and is exactly what makes `default` visibly data.

    A mode whose effect you cannot see is the stale-config problem wearing a
    new hat, which is why this exists at all rather than the pick being a bare
    name in a select.
    """
    lines: List[str] = []
    for path, value in preset_overrides(presets_config, name).items():
        found, current = value_at(base, path)
        if found and current == value:
            continue
        lines.append(f"{path} → {format_value(value)}")
    return lines

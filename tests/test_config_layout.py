"""The safety net for the config/reference/data/logs split.

The refactor moves `data/config.json` into six files under `config/` and has
`LocalJSONRepository.load_config()` merge them back into the one flat dict
`AppConfig` has always validated. Every consumer downstream — `planner`,
`week`, `ui_app` — keeps reading `config["weekly_schedule"]`,
`config["meal_styles"]` and the rest, so the whole refactor is correct exactly
when that merged dict is unchanged.

`fixtures/config_snapshot.json` is that dict, captured before the first file
moved. A key that lands in the wrong file, gets dropped by the merge, or
changes type on the way through fails here — one assertion naming the key —
rather than surfacing weeks later as a week generated against a silently
defaulted value.

Regenerate the fixture only when a config value is *deliberately* changed, and
in the same commit as the change, so the diff shows exactly which keys moved:

    source venv/bin/activate && python tests/test_config_layout.py --update

Additions are allowed and removals are not: `config/schedule.json` brings the
`base_schedule`/`location_rules`/`regional` keys in with it, declared on
`AppConfig` before anything reads them, and a new key can't break a caller
that doesn't know it exists. A *missing* key always can.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "config_snapshot.json"


def merged_config() -> dict:
    """The shipped config as every caller downstream sees it.

    Deliberately `load_config()` and not `load_config_with_models()`:
    models.json is reshaped by this refactor on purpose (`meal_generation_model`,
    per-model metadata), so pinning its contents here would turn an intended
    change into a test failure. What must not change is config.json's own
    merged output.
    """
    return planner.load_app_config(run_sync(LocalJSONRepository().load_config()))


class TestMergedConfigIsUnchanged(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = json.loads(SNAPSHOT.read_text())
        self.config = merged_config()

    def test_no_key_was_lost(self):
        missing = sorted(set(self.snapshot) - set(self.config))
        self.assertEqual(
            missing,
            [],
            f"config keys vanished in the split: {missing}. Either the key "
            f"landed in no file, or its file is absent from the merge manifest.",
        )

    def test_every_value_survived_intact(self):
        # Per-key subTest rather than one dict comparison: a 200-line
        # weekly_schedule diff buries which key actually moved, and the whole
        # point of this file is to name it.
        for key, expected in sorted(self.snapshot.items()):
            with self.subTest(key=key):
                self.assertEqual(self.config.get(key), expected)

    def test_snapshot_is_not_empty(self):
        # Guards the failure mode where a regeneration runs against a broken
        # or half-written config and quietly bakes in an empty baseline,
        # after which every other test here passes vacuously.
        self.assertGreaterEqual(len(self.snapshot), 20)
        self.assertIn("weekly_schedule", self.snapshot)


if __name__ == "__main__":
    if "--update" in sys.argv:
        SNAPSHOT.write_text(json.dumps(merged_config(), indent=2, sort_keys=True) + "\n")
        print(f"Wrote {SNAPSHOT.relative_to(Path.cwd())}")
    else:
        unittest.main()

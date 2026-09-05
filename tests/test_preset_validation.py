"""`planner.resolve_preset_layer` — the loader's check, made available to the
editor without a raise.

PROMPT-9's rule is that the editor imports this rather than writing a second
validator: a resolver that decides validity at load and a validator that
decides it at save are two interpretations of one word, free to disagree
about a file one accepts and the other refuses. So the tests here assert the
two are the *same* check in two presentations —

- `resolve_preset_layer` returns `PresetFailure`s where `apply_preset_layer`
  raises, on the same inputs;
- it runs both halves the loader runs: `presets.resolve_config`'s structural
  and path checks, **and** `AppConfig` schema validation on the resolved dict;

plus the one cross-field rule the editor is the reason for:
`favorite_reuse_days` may not exceed `history_max_entries`.
"""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from planner import (  # noqa: E402
    AppConfig,
    PlanningRules,
    apply_preset_layer,
    load_app_config,
    resolve_preset_layer,
)
from presets import PresetFailure  # noqa: E402
from repository import LocalJSONRepository, run_sync  # noqa: E402


def shipped_base() -> dict:
    return run_sync(LocalJSONRepository().load_config())


def one_preset(name: str, overrides: dict) -> dict:
    return {"active": name, "presets": {name: {"label": name, "overrides": overrides}}}


class TestOneFunctionTwoPresentations(unittest.TestCase):
    def setUp(self) -> None:
        self.base = shipped_base()

    def test_a_clean_preset_returns_a_config_and_no_failures(self):
        config, failures = resolve_preset_layer(
            self.base, one_preset("lean", {"dietary_rules.allowed_nova_groups": [1, 2]})
        )
        self.assertEqual(failures, [])
        self.assertEqual(config["dietary_rules"]["allowed_nova_groups"], [1, 2])
        self.assertEqual(config["active_preset"], "lean")

    def test_no_presets_is_the_validated_base_and_no_failures(self):
        config, failures = resolve_preset_layer(self.base, None)
        self.assertEqual(failures, [])
        # No preset applied, so the result is exactly what the loader produces
        # for the bare config — and carries no `active_preset` stamp.
        self.assertEqual(config, load_app_config(self.base))
        self.assertNotIn("active_preset", config)

    def test_a_structural_failure_comes_back_not_raised(self):
        config, failures = resolve_preset_layer(self.base, one_preset("bad", {"nope.k": 1}))
        self.assertIsNone(config)
        self.assertTrue(failures)
        self.assertIsInstance(failures[0], PresetFailure)
        self.assertIn("nope", failures[0].message)

    def test_a_schema_failure_comes_back_as_a_preset_failure(self):
        """A preset whose resolved dict `AppConfig` rejects — here a
        `min_baseline_cuisine_share` of 2, outside `le=1.0` — is a failure the
        editor must render, not an exception it must catch."""
        config, failures = resolve_preset_layer(
            self.base, one_preset("oops", {"planning_rules.min_baseline_cuisine_share": 2})
        )
        self.assertIsNone(config)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], PresetFailure)
        self.assertEqual(failures[0].preset, "oops")
        self.assertIn("min_baseline_cuisine_share", failures[0].message)

    def test_the_loader_raises_on_exactly_what_this_returns(self):
        for overrides in (
            {"nope.k": 1},
            {"planning_rules.min_baseline_cuisine_share": 2},
            {"planning_rules.favorite_reuse_days": {"breakfast": 7, "lunch": 40, "dinner": 21}},
        ):
            doc = one_preset("x", overrides)
            _config, failures = resolve_preset_layer(self.base, doc)
            self.assertTrue(failures, overrides)
            with self.assertRaises(ValueError, msg=overrides):
                apply_preset_layer(self.base, doc)


class TestProtectedTrainingFacts(unittest.TestCase):
    """design-06 §3, Task 4.1b, through the full loader path (`resolve_config`
    *and* `AppConfig`) rather than `presets.py` alone — `resolve_preset_layer`
    is what the loader raises on and what the editor's preview renders, so
    the protection has to hold at this seam too, not only in `presets.py`'s
    own tests."""

    GYM_PROGRAM = {
        "label": "Functional hypertrophy",
        "primary_goal": "hypertrophy_and_function",
        "architecture": "full_body",
        "working_sets": 3,
        "compound_rep_range": [8, 12],
        "accessory_rep_range": [10, 15],
        "target_rir": 2,
        "progression": "double_progression",
    }

    def setUp(self) -> None:
        self.base = dict(
            shipped_base(), gym_programs={"functional_hypertrophy": self.GYM_PROGRAM}
        )

    def test_selecting_a_known_program_resolves_cleanly(self):
        config, failures = resolve_preset_layer(
            self.base,
            one_preset("bulk", {"active_gym_program": "functional_hypertrophy"}),
        )
        self.assertEqual(failures, [])
        self.assertEqual(config["active_gym_program"], "functional_hypertrophy")
        self.assertEqual(config["gym_programs"], load_app_config(self.base)["gym_programs"])

    def test_emptying_the_training_profile_fails_both_presentations(self):
        doc = one_preset("x", {"training_profile": {}})
        config, failures = resolve_preset_layer(self.base, doc)
        self.assertIsNone(config)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].preset, "x")
        self.assertEqual(failures[0].path, "training_profile")
        with self.assertRaises(ValueError) as caught:
            apply_preset_layer(self.base, doc)
        self.assertIn("training_profile", str(caught.exception))

    def test_mutating_the_catalog_fails_both_presentations(self):
        doc = one_preset("x", {"gym_programs.functional_hypertrophy.working_sets": 10})
        config, failures = resolve_preset_layer(self.base, doc)
        self.assertIsNone(config)
        self.assertEqual(failures[0].path, "gym_programs.functional_hypertrophy.working_sets")
        with self.assertRaises(ValueError):
            apply_preset_layer(self.base, doc)

    def test_the_shipped_personal_constraint_survives_an_unrelated_preset(self):
        """Re-layering from base still works exactly as today: an unrelated
        override must not disturb the real, hand-authored constraint."""
        config, failures = resolve_preset_layer(
            self.base, one_preset("lean", {"dietary_rules.allowed_nova_groups": [1, 2]})
        )
        self.assertEqual(failures, [])
        self.assertEqual(config["training_profile"], self.base["training_profile"])


class TestReuseWindowsFitHistory(unittest.TestCase):
    """The first cross-field check the editor needs — `favorite_reuse_days`
    past `history_max_entries` silently stops binding, so it fails at load."""

    def test_the_shipped_planning_rules_pass(self):
        # {7, 21, 21} against a 28-day history depth.
        PlanningRules()  # does not raise
        load_app_config(run_sync(LocalJSONRepository().load_config()))

    def test_a_window_past_history_depth_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            PlanningRules(favorite_reuse_days={"breakfast": 7, "lunch": 40, "dinner": 21})
        self.assertIn("favorite_reuse_days", str(caught.exception))
        self.assertIn("history_max_entries", str(caught.exception))

    def test_it_moves_with_a_raised_history_depth(self):
        # 40 is fine once the history window is wide enough to see it.
        PlanningRules(
            history_max_entries=56,
            favorite_reuse_days={"breakfast": 7, "lunch": 40, "dinner": 21},
        )

    def test_a_preset_that_breaks_it_fails_the_layer(self):
        base = run_sync(LocalJSONRepository().load_config())
        _config, failures = resolve_preset_layer(
            base,
            one_preset(
                "greedy",
                {"planning_rules.favorite_reuse_days": {"breakfast": 7, "lunch": 40, "dinner": 40}},
            ),
        )
        self.assertTrue(failures)
        self.assertIn("favorite_reuse_days", failures[0].message)


if __name__ == "__main__":
    unittest.main()

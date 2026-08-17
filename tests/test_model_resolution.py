"""Tests for which model each role runs on, and the quirks that ride with it.

`unittest`, like the rest of tests/. Nothing here makes an API call: every
function under test is pure config arithmetic, which is exactly why the bug
these were written for went unnoticed — it lived entirely in the gap between
two keys of one JSON file and only surfaced as a provider's 400.

The bug: `recipe_parser_model` named `google/gemini-3.6-flash` while the
`models` table described only `google/gemini-3.7-flash`. `model_metadata`
returns `{}` for an unknown id — indistinguishable from a model with no
quirks — so `reasoning_extra_body` sent `{"reasoning": {"enabled": False}}`
to a provider that rejects that key outright, and every recipe import failed
in under a second, three retries deep. See CLAUDE.md, "Some providers reject
the disable switch outright".

`TestShippedConfig` at the bottom is the one that would have caught it: it
asserts the real `config/models.json` is internally consistent, rather than
testing the resolvers against fixtures alone.
"""

import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from planner import (  # noqa: E402
    model_metadata,
    reasoning_extra_body,
    resolve_planner_model,
    resolve_recipe_parser_model,
    selectable_models,
)

# A models.json with both roles set and every named id described. Deliberately
# gives the two roles *different* models, since a fixture where they coincide
# cannot tell "read the right key" from "read either key".
GOOD = {
    "meal_generation_model": "vendor/big",
    "recipe_parser_model": "vendor/small",
    "request_timeout_seconds": 120.0,
    "models": {
        "vendor/big": {},
        "vendor/small": {"reasoning_required": True},
    },
}


def config(models: dict) -> dict:
    """The shape the app threads around: models.json under a "models" key.

    Note the doubled nesting this produces — `config["models"]["models"]` is
    the table — which is why `model_metadata` reaches through two levels.
    """
    return {"models": models}


class TestRoleSelection(unittest.TestCase):
    def test_each_role_reads_its_own_key(self):
        self.assertEqual(resolve_planner_model(config(GOOD)), "vendor/big")
        self.assertEqual(resolve_recipe_parser_model(config(GOOD)), "vendor/small")

    def test_the_parser_ignores_the_per_run_model_override(self):
        """`--model` and the drawer select are the *planner's* per-run choice.

        Parsing pasted recipe text is a second role, not a second opinion
        about the same one — a pricey generation model must not drag the
        cheap mechanical parse along with it.
        """
        cfg = dict(config(GOOD), openrouter_model="vendor/big")
        self.assertEqual(resolve_recipe_parser_model(cfg), "vendor/small")

    def test_the_parser_falls_back_to_the_generation_model(self):
        models = dict(GOOD)
        del models["recipe_parser_model"]
        self.assertEqual(resolve_recipe_parser_model(config(models)), "vendor/big")

    def test_an_unset_role_names_the_key_the_reader_must_edit(self):
        models = {"models": {}}
        with self.assertRaises(ValueError) as caught:
            resolve_recipe_parser_model(config(models))
        message = str(caught.exception)
        self.assertIn("recipe_parser_model", message)
        self.assertIn("meal_generation_model", message)

    def test_no_generation_model_anywhere_raises(self):
        with self.assertRaises(ValueError):
            resolve_planner_model(config({"models": {}}))


class TestUnknownModelGuard(unittest.TestCase):
    """The regression guard for the recipe-import bug."""

    def test_a_parser_model_absent_from_the_table_raises(self):
        models = dict(GOOD, recipe_parser_model="vendor/undescribed")
        with self.assertRaises(ValueError) as caught:
            resolve_recipe_parser_model(config(models))
        message = str(caught.exception)
        # Both halves of the mismatch, so the reader doesn't have to guess
        # which of the two lines in models.json is the wrong one.
        self.assertIn("recipe_parser_model", message)
        self.assertIn("vendor/undescribed", message)
        self.assertIn("vendor/big", message)

    def test_a_generation_model_absent_from_the_table_raises(self):
        models = dict(GOOD, meal_generation_model="vendor/undescribed")
        with self.assertRaises(ValueError) as caught:
            resolve_planner_model(config(models))
        self.assertIn("meal_generation_model", str(caught.exception))

    def test_a_hand_typed_model_override_stays_free_form(self):
        """`--model` exists to try an id nobody has recorded anything about.

        Validating it would defeat the flag's whole purpose, so the guard is
        deliberately limited to the standing choices written in the file.
        """
        cfg = dict(config(GOOD), openrouter_model="vendor/never-seen-before")
        self.assertEqual(resolve_planner_model(cfg), "vendor/never-seen-before")


class TestReasoningSwitch(unittest.TestCase):
    def test_reasoning_is_disabled_by_default(self):
        self.assertEqual(
            reasoning_extra_body("vendor/big", config(GOOD)),
            {"reasoning": {"enabled": False}},
        )

    def test_a_reasoning_required_model_omits_the_key_entirely(self):
        """Not `enabled: True` — omitted.

        The task needs no deliberation either way; the provider merely
        refuses to be told so. Sending `enabled: True` would opt into the
        latency and token blow-up the default exists to prevent.
        """
        self.assertEqual(reasoning_extra_body("vendor/small", config(GOOD)), {})

    def test_an_unknown_model_reads_as_having_no_quirks(self):
        """The behaviour that made the bug silent, pinned deliberately.

        This is correct for a hand-typed `--model` and catastrophic for a
        standing choice — which is why the guard lives in the resolvers
        rather than here.
        """
        self.assertEqual(model_metadata(config(GOOD), "vendor/unknown"), {})
        self.assertEqual(
            reasoning_extra_body("vendor/unknown", config(GOOD)),
            {"reasoning": {"enabled": False}},
        )


class TestShippedConfig(unittest.TestCase):
    """Against the real config/models.json, not a fixture.

    Same intent as `test_week_composition.TestRealConfig`: the fixtures above
    prove the code is right, and this proves the file is.
    """

    @classmethod
    def setUpClass(cls):
        cls.models = json.loads((_ROOT / "config" / "models.json").read_text())
        cls.config = config(cls.models)

    def test_both_roles_resolve(self):
        self.assertTrue(resolve_planner_model(self.config))
        self.assertTrue(resolve_recipe_parser_model(self.config))

    def test_every_role_names_a_described_model(self):
        """The assertion that would have caught the import bug outright."""
        table = self.models["models"]
        for role in ("meal_generation_model", "recipe_parser_model"):
            with self.subTest(role=role):
                self.assertIn(
                    self.models[role],
                    table,
                    f"{role} names a model the 'models' table does not describe, "
                    "so its per-model quirks would be silently dropped",
                )

    def test_the_drawer_offers_every_described_model(self):
        self.assertEqual(selectable_models(self.models), list(self.models["models"]))

    def test_the_timeout_has_headroom_for_a_slow_free_route(self):
        """A full meal-plan prompt has been measured at ~58s on a free route.

        A timeout that fires mid-request doesn't retry the response —
        `instructor` re-runs the whole generation, so a tight one turns a
        slow-but-fine call into a multi-minute hang. See the
        openrouter-model-choice skill.
        """
        self.assertGreaterEqual(self.models["request_timeout_seconds"], 100.0)


if __name__ == "__main__":
    unittest.main()

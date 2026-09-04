"""Tests for Task 4.1a — `training_profile` (config/profile.json) and
`gym_programs`/`active_gym_program` (config/schedule.json), design-06 §2.

This is schema only: no workout is generated here, and none of the
preset-protection rule (Task 4.1b) or the Settings editors (Task 4.1c) exist
yet. What has to hold at this layer, per `dev/PROMPT-14.md`'s acceptance
list: a config predating these keys loads identically; a birth date alone
activates nothing; `movement_constraints` ids are unique; a `movement_pattern`
constraint names a known pattern; a `GymProgram`'s rep ranges and bounded
numbers are valid; and `active_gym_program`, if set, names a real catalog
entry. `unittest` and the `sys.path` insert match `test_diet_styles.py`.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import planner  # noqa: E402

BASE_CONFIG = {
    "weekly_schedule": {
        "Monday": {"calories": 1500, "protein_g": 120, "net_carbs_g": 130, "fat_g": 55},
    },
}

HIP_CONSTRAINT = {
    "id": "hip-impingement-squat-depth",
    "scope": "movement_pattern",
    "target": "squat",
    "action": "modify",
    "instruction": "Do not prescribe full-depth squats; use only my user-approved partial range.",
    "preferred_variations": [],
}

FUNCTIONAL_HYPERTROPHY = {
    "label": "Functional hypertrophy",
    "primary_goal": "hypertrophy_and_function",
    "architecture": "full_body",
    "working_sets": 3,
    "compound_rep_range": [8, 12],
    "accessory_rep_range": [10, 15],
    "target_rir": 2,
    "progression": "double_progression",
    "include_power": True,
    "movement_patterns": [
        "squat", "hinge", "horizontal_push", "horizontal_pull",
        "vertical_push", "vertical_pull", "carry", "core",
    ],
    "notes": "Use low-impact power variations.",
}


class TestBenignAbsence(unittest.TestCase):
    """A config predating this feature must load and behave identically —
    design-06 §10's compatibility list, first two bullets."""

    def test_config_with_none_of_the_new_keys_loads_cleanly(self):
        config = planner.load_app_config(dict(BASE_CONFIG))
        self.assertEqual(config["training_profile"], {
            "movement_constraints": [],
            "available_equipment": [],
            "notes": None,
        })
        self.assertEqual(config["gym_programs"], {})
        self.assertIsNone(config["active_gym_program"])

    def test_birth_date_alone_activates_nothing(self):
        """Merely having a birth date — specifically being 55 — must not
        create a constraint or select a program. Asserted directly, per
        design-06 §2.2/§10: "birth date never selects a gym program and
        never creates a constraint." """
        raw = dict(BASE_CONFIG, user_profile={"birth_date": "1971-01-10"})
        config = planner.load_app_config(raw)
        self.assertEqual(config["training_profile"]["movement_constraints"], [])
        self.assertEqual(config["gym_programs"], {})
        self.assertIsNone(config["active_gym_program"])


class TestMovementConstraints(unittest.TestCase):
    def test_the_hip_constraint_round_trips(self):
        raw = dict(BASE_CONFIG, training_profile={"movement_constraints": [HIP_CONSTRAINT]})
        config = planner.load_app_config(raw)
        self.assertEqual(config["training_profile"]["movement_constraints"], [HIP_CONSTRAINT])

    def test_duplicate_constraint_ids_fail_at_load(self):
        raw = dict(
            BASE_CONFIG,
            training_profile={"movement_constraints": [HIP_CONSTRAINT, dict(HIP_CONSTRAINT)]},
        )
        with self.assertRaises(ValueError) as caught:
            planner.load_app_config(raw)
        self.assertIn("hip-impingement-squat-depth", str(caught.exception))

    def test_movement_pattern_scope_requires_a_known_pattern(self):
        bad = dict(HIP_CONSTRAINT, target="bench_press")
        raw = dict(BASE_CONFIG, training_profile={"movement_constraints": [bad]})
        with self.assertRaises(ValueError) as caught:
            planner.load_app_config(raw)
        self.assertIn("bench_press", str(caught.exception))

    def test_exercise_scope_allows_free_text_target(self):
        """Exercise matching is normalized exact-title matching (design-06
        §2.1) — no controlled vocabulary, unlike a movement_pattern scope."""
        entry = {
            "id": "no-behind-neck-press",
            "scope": "exercise",
            "target": "Behind-the-Neck Overhead Press",
            "action": "exclude",
        }
        raw = dict(BASE_CONFIG, training_profile={"movement_constraints": [entry]})
        config = planner.load_app_config(raw)
        self.assertEqual(
            config["training_profile"]["movement_constraints"][0]["target"],
            "Behind-the-Neck Overhead Press",
        )

    def test_unknown_action_is_rejected(self):
        bad = dict(HIP_CONSTRAINT, action="ban")
        raw = dict(BASE_CONFIG, training_profile={"movement_constraints": [bad]})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_unknown_scope_is_rejected(self):
        bad = dict(HIP_CONSTRAINT, scope="body_part")
        raw = dict(BASE_CONFIG, training_profile={"movement_constraints": [bad]})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)


class TestGymProgramCatalog(unittest.TestCase):
    def test_a_well_formed_program_loads_cleanly(self):
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": FUNCTIONAL_HYPERTROPHY})
        config = planner.load_app_config(raw)
        self.assertEqual(
            config["gym_programs"]["functional_hypertrophy"]["label"],
            "Functional hypertrophy",
        )
        # Ship no active program even when a sample catalog entry exists —
        # design-06 §2.2: "leave active_gym_program null so the merged base
        # remains behaviourally identical."
        self.assertIsNone(config["active_gym_program"])

    def test_rejects_an_inverted_rep_range(self):
        bad = dict(FUNCTIONAL_HYPERTROPHY, compound_rep_range=[12, 8])
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": bad})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_rejects_a_zero_low_rep_range(self):
        bad = dict(FUNCTIONAL_HYPERTROPHY, accessory_rep_range=[0, 10])
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": bad})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_rejects_an_unbounded_rep_range(self):
        bad = dict(FUNCTIONAL_HYPERTROPHY, accessory_rep_range=[10, 500])
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": bad})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_rejects_working_sets_out_of_bounds(self):
        bad = dict(FUNCTIONAL_HYPERTROPHY, working_sets=0)
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": bad})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_rejects_target_rir_out_of_bounds(self):
        bad = dict(FUNCTIONAL_HYPERTROPHY, target_rir=9)
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": bad})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_rejects_an_unknown_primary_goal(self):
        bad = dict(FUNCTIONAL_HYPERTROPHY, primary_goal="get_swole")
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": bad})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_rejects_an_unknown_architecture(self):
        bad = dict(FUNCTIONAL_HYPERTROPHY, architecture="bro_split")
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": bad})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_rejects_an_unknown_progression(self):
        bad = dict(FUNCTIONAL_HYPERTROPHY, progression="linear")
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": bad})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)

    def test_rejects_an_unknown_movement_pattern(self):
        bad = dict(FUNCTIONAL_HYPERTROPHY, movement_patterns=["deadlift"])
        raw = dict(BASE_CONFIG, gym_programs={"functional_hypertrophy": bad})
        with self.assertRaises(ValueError):
            planner.load_app_config(raw)


class TestActiveGymProgramIsKnown(unittest.TestCase):
    def test_a_known_active_program_loads_cleanly(self):
        raw = dict(
            BASE_CONFIG,
            gym_programs={"functional_hypertrophy": FUNCTIONAL_HYPERTROPHY},
            active_gym_program="functional_hypertrophy",
        )
        config = planner.load_app_config(raw)
        self.assertEqual(config["active_gym_program"], "functional_hypertrophy")

    def test_an_unknown_active_program_fails_at_load(self):
        raw = dict(BASE_CONFIG, active_gym_program="functional_hypertrophy")
        with self.assertRaises(ValueError) as caught:
            planner.load_app_config(raw)
        self.assertIn("functional_hypertrophy", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

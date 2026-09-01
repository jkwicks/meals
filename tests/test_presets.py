"""The preset layer — the container, the layer, and the weekly pick.

`config/` held one implicit profile smeared across five files. A preset names
it, makes it switchable, and makes the choice weekly. Everything here is
about the three things that can go wrong with laying one dict over another:

- **The compatibility claim, first, because everything else is negotiable.**
  No `presets.json` plans exactly as `main` does, and so does a `presets.json`
  holding nothing but an empty `default`. The second is the stronger test and
  the one that proves `default` is *data* — it reproduces today's behaviour
  because its `overrides` are empty, not because the loader falls back to it.
- **The sibling-destruction case**, which is why the rule is leaf paths and
  not whole keys. It is asserted on values (17 banned ingredients, by name),
  not on shape: the refuted design *validated cleanly* and silently discarded
  them, so a test asserting only that `dietary_rules` is still a dict would
  have passed it.
- **The write path.** `save_config_keys` raises on every key in this file, so
  a test that only checked the file's contents afterwards could pass on a
  code path that happened to write it some other way.

`test_config_layout.py` covers the merged dict itself; this covers what is
laid over it.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from shutil import copytree

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import planner  # noqa: E402
import presets  # noqa: E402
import ui_state  # noqa: E402
from planner import WeekPlan, apply_preset_layer, load_app_config  # noqa: E402
from repository import CONFIG_KEY_OWNER, LocalJSONRepository, run_sync  # noqa: E402


def shipped_base() -> dict:
    """The five merged core files, before any layer. The baseline throughout —
    never the preset named `default`, which is an ordinary row that may be
    edited or deleted."""
    return run_sync(LocalJSONRepository().load_config())


def preset_file(active, **overrides_by_name) -> dict:
    """A `presets.json` document holding one preset per keyword argument."""
    return {
        "active": active,
        "presets": {
            name: {"label": name.title(), "overrides": overrides}
            for name, overrides in overrides_by_name.items()
        },
    }


class ConfigDirCase(unittest.TestCase):
    """A throwaway copy of the shipped `config/`, so a test may write to it."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_dir = Path(self.tmp.name) / "config"
        copytree(_ROOT / "config", self.config_dir)
        self.repo = LocalJSONRepository(config_dir=str(self.config_dir))

    def write_presets(self, document) -> None:
        (self.config_dir / "presets.json").write_text(json.dumps(document, indent=2))

    def remove_presets(self) -> None:
        (self.config_dir / "presets.json").unlink(missing_ok=True)

    def stored_presets(self) -> dict:
        return json.loads((self.config_dir / "presets.json").read_text())


class TestTheCompatibilityClaim(ConfigDirCase):
    """Assert it; do not assume it."""

    def test_no_presets_file_resolves_to_nothing(self):
        self.remove_presets()
        self.assertEqual(run_sync(self.repo.load_presets_config()), {})

    def test_no_presets_file_plans_byte_identically(self):
        self.remove_presets()
        base = run_sync(self.repo.load_config())
        self.assertEqual(apply_preset_layer(base, {}), load_app_config(base))

    def test_no_presets_file_stamps_no_preset_on_the_config(self):
        """`WeekPlan.preset` is None for a week nothing pinned, and the key is
        simply absent rather than present-and-null — a checkout with no file
        carries nothing new at all."""
        self.remove_presets()
        base = run_sync(self.repo.load_config())
        self.assertNotIn(presets.ACTIVE_PRESET_CONFIG_KEY, apply_preset_layer(base, {}))

    def test_an_empty_default_plans_byte_identically_too(self):
        """The stronger claim: `default` reproduces today's behaviour because
        of what it *contains*, not because the code treats it as special."""
        base = shipped_base()
        layered = apply_preset_layer(base, preset_file("default", default={}))
        # The one difference is the runtime stamp recording which preset ran,
        # which reaches no prompt and no target — `WeekPlan.preset` is its
        # only reader. Everything the week is actually planned against is
        # equal, key for key.
        self.assertEqual(layered.pop(presets.ACTIVE_PRESET_CONFIG_KEY), "default")
        self.assertEqual(layered, load_app_config(base))

    def test_the_shipped_presets_file_changes_nothing(self):
        """The file this repository actually ships, against the file it
        ships alongside."""
        base = shipped_base()
        shipped = run_sync(LocalJSONRepository().load_presets_config())
        layered = apply_preset_layer(base, shipped)
        layered.pop(presets.ACTIVE_PRESET_CONFIG_KEY, None)
        self.assertEqual(layered, load_app_config(base))

    def test_default_is_a_row_in_the_file_not_a_built_in(self):
        """Delete it and nothing dangles, provided `active` goes with it —
        which is what makes the editor's delete safe to build in PROMPT-9."""
        base = shipped_base()
        resolution = presets.resolve_config(base, {"active": None, "presets": {}})
        self.assertTrue(resolution.ok)
        self.assertIsNone(resolution.active)
        self.assertIs(resolution.config, base)


class TestSiblingsSurviveTheOverride(ConfigDirCase):
    """The case that refuted whole-key replacement.

    Under that rule `{"dietary_rules": {"allowed_nova_groups": [1,2,3,4]}}`
    *is* the week's `dietary_rules`, and `DietaryRules` has no required
    fields — so it validates cleanly and silently discards 17 banned
    ingredients and every active diet style. Asserted on values for exactly
    that reason.
    """

    def setUp(self) -> None:
        super().setUp()
        self.base = shipped_base()
        self.layered = apply_preset_layer(
            self.base,
            preset_file("comfort", comfort={"dietary_rules.allowed_nova_groups": [1, 2, 3, 4]}),
        )

    def test_the_override_landed(self):
        self.assertEqual(self.layered["dietary_rules"]["allowed_nova_groups"], [1, 2, 3, 4])

    def test_every_banned_ingredient_survived(self):
        banned = self.base["dietary_rules"]["banned_ingredients"]
        self.assertEqual(len(banned), 17, "fixture drifted: the shipped ban list moved")
        self.assertEqual(self.layered["dietary_rules"]["banned_ingredients"], banned)

    def test_the_sibling_keys_survived(self):
        self.assertEqual(
            self.layered["dietary_rules"]["active_diet_styles"],
            self.base["dietary_rules"]["active_diet_styles"],
        )

    def test_no_other_top_level_key_moved(self):
        moved = [
            key for key in self.base
            if key != "dietary_rules" and self.layered.get(key) != load_app_config(self.base)[key]
        ]
        self.assertEqual(moved, [])


class TestALeafIsReplacedWhole(unittest.TestCase):
    """No recursive merge anywhere. A merge cannot express deletion, and it
    makes "what does this preset plan against" unanswerable without replaying
    it."""

    def setUp(self) -> None:
        self.base = shipped_base()

    def test_overriding_the_key_itself_replaces_all_seven_days(self):
        three_days = {
            day: dict(self.base["weekly_schedule"][day])
            for day in list(self.base["weekly_schedule"])[:3]
        }
        layered = apply_preset_layer(
            self.base, preset_file("short", short={"weekly_schedule": three_days})
        )
        self.assertEqual(len(layered["weekly_schedule"]), 3)

    def test_overriding_one_day_leaves_the_other_six(self):
        day = "Thursday"
        replacement = {"calories": 1234, "protein_g": 144, "net_carbs_g": 50, "fat_g": 60}
        layered = apply_preset_layer(
            self.base, preset_file("thu", thu={f"weekly_schedule.{day}": replacement})
        )
        self.assertEqual(layered["weekly_schedule"][day]["calories"], 1234)
        for other in self.base["weekly_schedule"]:
            if other == day:
                continue
            with self.subTest(day=other):
                self.assertEqual(
                    layered["weekly_schedule"][other],
                    load_app_config(self.base)["weekly_schedule"][other],
                )

    def test_a_deep_leaf_leaves_its_siblings(self):
        layered = apply_preset_layer(
            self.base,
            preset_file("lazy", lazy={"planning_rules.favorite_dinner_slots": 4}),
        )
        self.assertEqual(layered["planning_rules"]["favorite_dinner_slots"], 4)
        self.assertEqual(
            layered["planning_rules"]["portion_trim_limits"],
            self.base["planning_rules"]["portion_trim_limits"],
        )

    def test_an_empty_list_is_an_explicit_value_not_an_absence(self):
        """`"dietary_rules.banned_ingredients": []` genuinely bans nothing,
        and says so where an omitted path would have said nothing at all."""
        layered = apply_preset_layer(
            self.base, preset_file("open", open={"dietary_rules.banned_ingredients": []})
        )
        self.assertEqual(layered["dietary_rules"]["banned_ingredients"], [])

    def test_an_empty_object_is_an_explicit_value_too(self):
        layered = apply_preset_layer(
            self.base, preset_file("noloc", noloc={"location_rules": {}})
        )
        self.assertEqual(layered["location_rules"], {})

    def test_the_base_config_is_never_mutated(self):
        before = json.dumps(self.base, sort_keys=True)
        apply_preset_layer(
            self.base, preset_file("x", x={"dietary_rules.banned_ingredients": []})
        )
        self.assertEqual(json.dumps(self.base, sort_keys=True), before)


class TestABadPathFailsAtLoad(unittest.TestCase):
    """A preset that appears applied and is not is strictly worse than one
    that refuses to load — the same argument `CONFIG_FILES` already makes
    about a key in the wrong file."""

    def setUp(self) -> None:
        self.base = shipped_base()

    def test_an_unknown_first_segment_raises(self):
        with self.assertRaises(ValueError) as caught:
            apply_preset_layer(
                self.base, preset_file("typo", typo={"dietry_rules.allowed_nova_groups": [4]})
            )
        message = str(caught.exception)
        self.assertIn("typo", message, "the failing preset must be named")
        self.assertIn("dietry_rules.allowed_nova_groups", message, "the path must be named")

    def test_only_the_first_segment_is_a_config_files_question(self):
        """Deeper segments are about the shape of a value, not about which
        file owns a key, so `CONFIG_KEY_OWNER` is asked once and only once."""
        self.assertIn("dietary_rules", CONFIG_KEY_OWNER)
        self.assertNotIn("allowed_nova_groups", CONFIG_KEY_OWNER)
        layered = apply_preset_layer(
            self.base, preset_file("ok", ok={"dietary_rules.allowed_nova_groups": [1]})
        )
        self.assertEqual(layered["dietary_rules"]["allowed_nova_groups"], [1])

    def test_a_missing_intermediate_segment_raises(self):
        """A path describing a branch that is not there is structurally
        wrong; creating it silently writes a value nothing reads."""
        with self.assertRaises(ValueError) as caught:
            apply_preset_layer(
                self.base, preset_file("x", x={"dietary_rules.nope.deeper": 1})
            )
        self.assertIn("dietary_rules.nope", str(caught.exception))

    def test_an_active_name_that_names_no_preset_raises(self):
        with self.assertRaises(ValueError) as caught:
            apply_preset_layer(self.base, preset_file("ghost", default={}))
        self.assertIn("ghost", str(caught.exception))

    def test_a_broken_preset_nobody_picked_still_fails(self):
        """Every preset is checked, not only the active one: a preset you
        might pick next Monday is worth knowing is broken now."""
        document = preset_file("default", default={}, broken={"nope.key": 1})
        with self.assertRaises(ValueError) as caught:
            apply_preset_layer(self.base, document)
        self.assertIn("broken", str(caught.exception))

    def test_validation_runs_after_the_layer(self):
        """The real change in this prompt. A preset overriding a key *after*
        validation could introduce a state `extra="forbid"` would have
        rejected, so validating last is the only ordering where it still
        means anything."""
        with self.assertRaises(ValueError) as caught:
            apply_preset_layer(
                self.base,
                preset_file("bad", bad={"serving_rules": {"servings_per_meal": "two"}}),
            )
        self.assertIn("schema validation", str(caught.exception))


class TestTheResolverIsOnePureFunction(unittest.TestCase):
    """One resolver, two presentations: the loader raises on its failures and
    PROMPT-9's editor renders the same ones and declines to write. A separate
    validator there would be a second interpretation of "valid", free to
    disagree about a file this accepted."""

    def test_it_reports_rather_than_raises(self):
        resolution = presets.resolve_config(
            shipped_base(), preset_file("x", x={"nope.key": 1})
        )
        self.assertFalse(resolution.ok)
        self.assertEqual(len(resolution.failures), 1)
        failure = resolution.failures[0]
        self.assertEqual(failure.preset, "x")
        self.assertEqual(failure.path, "nope.key")
        self.assertIn("nope", failure.message)

    def test_a_failed_resolution_returns_the_base_untouched(self):
        base = shipped_base()
        resolution = presets.resolve_config(base, preset_file("x", x={"nope.key": 1}))
        self.assertIs(resolution.config, base)
        self.assertIsNone(resolution.active)

    def test_it_needs_neither_nicegui_nor_plannerstate(self):
        """Asserted in a fresh interpreter, because this one has already
        imported `ui_state` and would answer for the whole test module rather
        than for the module under test."""
        probe = (
            "import sys; sys.path.insert(0, %r); import presets; "
            "assert 'nicegui' not in sys.modules, 'presets pulled in nicegui'; "
            "assert 'ui_state' not in sys.modules, 'presets pulled in PlannerState'; "
            "assert 'planner' not in sys.modules, 'presets pulled in planner'"
            % str(_ROOT / "src")
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class TestTheWritePath(ConfigDirCase):
    """`save_config_keys` looks every key up in `CONFIG_KEY_OWNER` and raises
    on a miss, and by design every key in this file misses. The pick is
    written through `save_presets_config` or it is not written at all."""

    def test_save_config_keys_refuses_this_file_s_keys(self):
        for key in (presets.ACTIVE_KEY, presets.PRESETS_KEY):
            with self.subTest(key=key):
                self.assertNotIn(key, CONFIG_KEY_OWNER)
                with self.assertRaises(ValueError):
                    run_sync(self.repo.save_config_keys({key: "anything"}))

    def test_the_pick_round_trips(self):
        self.write_presets(preset_file("default", default={}, comfort={}))
        run_sync(self.repo.save_presets_config({presets.ACTIVE_KEY: "comfort"}))
        self.assertEqual(run_sync(self.repo.load_presets_config())["active"], "comfort")

    def test_writing_the_pick_preserves_a_preset_the_code_never_parsed(self):
        """Read-modify-write on the whole file, the same property
        `save_config_keys` gives per file: an editor that has never heard of
        a hand-added field must not drop it."""
        document = preset_file("default", default={})
        document["presets"]["hand_written"] = {
            "label": "By hand",
            "overrides": {"planning_rules.favorite_dinner_slots": 1},
            "notes_the_app_has_never_heard_of": {"why": "because"},
        }
        self.write_presets(document)

        run_sync(self.repo.save_presets_config({presets.ACTIVE_KEY: "hand_written"}))

        stored = self.stored_presets()
        self.assertEqual(stored["active"], "hand_written")
        self.assertEqual(
            stored["presets"]["hand_written"]["notes_the_app_has_never_heard_of"],
            {"why": "because"},
        )
        self.assertEqual(stored["presets"]["default"], {"label": "Default", "overrides": {}})

    def test_a_missing_file_is_created_rather_than_refused(self):
        self.remove_presets()
        run_sync(self.repo.save_presets_config({presets.ACTIVE_KEY: None}))
        self.assertEqual(self.stored_presets(), {"active": None})


class RecordingRepository:
    """A repository that answers `save_presets_config` and explodes on the
    method that cannot serve this file — so a test asserting on the file's
    contents alone cannot pass on a code path that wrote it another way."""

    def __init__(self) -> None:
        self.saved = []

    async def save_presets_config(self, document) -> None:
        self.saved.append(document)

    async def save_config_keys(self, updates) -> None:
        raise AssertionError(f"the pick must not go through save_config_keys: {updates}")


class TestTheWeeklyPick(unittest.TestCase):
    """`PlannerState.set_preset` — the third writer to `config/`, after
    `set_target_mode` and `accept_training_proposal`, and it passes the same
    test both do: a standing choice, not an input to one run."""

    def setUp(self) -> None:
        self.base = shipped_base()
        self.document = preset_file(
            "default",
            default={},
            lean={
                "dietary_rules.allowed_nova_groups": [1, 2],
                "serving_rules": {"servings_per_meal": 4},
            },
        )
        self.repo = RecordingRepository()
        self.state = ui_state.PlannerState(
            config=apply_preset_layer(self.base, self.document),
            base_config=self.base,
            presets_config=self.document,
            week_start=self.base["week_start_day"],
            servings=self.base["serving_rules"]["servings_per_meal"],
            shop_days=list(self.base["shopping"]["shop_days"]),
        )

    def test_the_pick_is_saved_through_save_presets_config(self):
        run_sync(self.state.set_preset(self.repo, "lean"))
        self.assertEqual(self.repo.saved, [{"active": "lean"}])

    def test_the_config_is_relayered_not_layered_again(self):
        """Switching away from a preset must not leave its opinions behind on
        every leaf the incoming one is silent about."""
        run_sync(self.state.set_preset(self.repo, "lean"))
        self.assertEqual(self.state.config["dietary_rules"]["allowed_nova_groups"], [1, 2])
        run_sync(self.state.set_preset(self.repo, "default"))
        self.assertEqual(
            self.state.config["dietary_rules"]["allowed_nova_groups"],
            self.base["dietary_rules"]["allowed_nova_groups"],
        )

    def test_planning_config_sees_the_preset(self):
        """CLAUDE.md's standing rule: a number the UI displays and a number a
        run plans against must come from one call, not two."""
        run_sync(self.state.set_preset(self.repo, "lean"))
        self.assertEqual(
            self.state.planning_config()["dietary_rules"]["allowed_nova_groups"], [1, 2]
        )

    def test_a_seeded_field_the_preset_moved_is_reseeded(self):
        self.assertEqual(self.state.servings, self.base["serving_rules"]["servings_per_meal"])
        run_sync(self.state.set_preset(self.repo, "lean"))
        self.assertEqual(self.state.servings, 4)

    def test_a_staged_row_the_preset_says_nothing_about_survives(self):
        """The re-seed is keyed on the config value actually moving, which is
        what lets a pantry row typed a moment ago outlive a pick that has no
        opinion about the pantry."""
        self.state.pantry = [{"item": "half a bag of spinach", "quantity_g": None}]
        run_sync(self.state.set_preset(self.repo, "lean"))
        self.assertEqual(self.state.pantry, [{"item": "half a bag of spinach", "quantity_g": None}])

    def test_a_reseeded_training_schedule_reports_no_phantom_change(self):
        """`_original_training_schedule` moves with it, or the staged bar
        counts every session the preset just seeded as a user edit."""
        document = preset_file("default", default={}, rest={"training_schedule": []})
        state = ui_state.PlannerState(
            config=apply_preset_layer(self.base, document),
            base_config=self.base,
            presets_config=document,
        )
        state._original_training_schedule = [dict(s) for s in state.training_schedule]
        run_sync(state.set_preset(self.repo, "rest"))
        self.assertEqual(state.training_schedule, [])
        self.assertEqual(state._original_training_schedule, [])

    def test_an_unusable_pick_leaves_the_session_untouched(self):
        self.state.presets_config = preset_file("default", default={}, broken={"nope.k": 1})
        before = dict(self.state.config)
        with self.assertRaises(ValueError):
            run_sync(self.state.set_preset(self.repo, "broken"))
        self.assertEqual(self.state.config, before)
        self.assertEqual(self.repo.saved, [], "a rejected pick must not be written")

    def test_the_rest_of_the_dialog_is_still_session_only(self):
        """The pick persists; nothing beside it does. `saved` holding exactly
        one entry — the pick — is the assertion, since a target override or a
        pantry row riding along would show up here as a second write."""
        self.state.target_overrides = {"Monday": {"calories": 2200}}
        self.state.pantry = [{"item": "tinned tuna", "quantity_g": 95}]
        run_sync(self.state.set_preset(self.repo, "lean"))
        self.assertEqual(self.repo.saved, [{"active": "lean"}])
        # And they are still staged, because a preset with no opinion about
        # either must not quietly reset them.
        self.assertEqual(self.state.target_overrides, {"Monday": {"calories": 2200}})
        self.assertEqual(self.state.pantry, [{"item": "tinned tuna", "quantity_g": 95}])

    def test_the_view_says_what_the_pick_changed(self):
        run_sync(self.state.set_preset(self.repo, "lean"))
        view = self.state.preset_view()
        self.assertEqual(view.active, "lean")
        self.assertEqual(view.label, "Lean")
        self.assertIn("dietary_rules.allowed_nova_groups", view.summary)
        self.assertIn("1, 2", view.summary)

    def test_a_no_op_preset_says_so_rather_than_listing_nothing(self):
        run_sync(self.state.set_preset(self.repo, "default"))
        self.assertEqual(self.state.preset_view().summary, "No changes from the base config.")

    def test_the_diff_is_against_the_base_config_never_against_default(self):
        """A diff computed against another *row* goes blank the moment that
        row is edited or deleted."""
        document = preset_file(
            "lean",
            default={"dietary_rules.allowed_nova_groups": [1, 2]},
            lean={"dietary_rules.allowed_nova_groups": [1, 2]},
        )
        state = ui_state.PlannerState(
            config=apply_preset_layer(self.base, document),
            base_config=self.base,
            presets_config=document,
        )
        # Identical to `default`, and still reported, because `default` is not
        # the baseline — the base config is.
        self.assertIn("allowed_nova_groups", state.preset_view().summary)

    def test_an_override_restating_the_base_produces_no_line(self):
        document = preset_file(
            "same",
            same={"dietary_rules.allowed_nova_groups":
                  self.base["dietary_rules"]["allowed_nova_groups"]},
        )
        state = ui_state.PlannerState(
            config=apply_preset_layer(self.base, document),
            base_config=self.base,
            presets_config=document,
        )
        self.assertEqual(state.preset_view().summary, "No changes from the base config.")

    def test_no_presets_file_offers_no_control(self):
        state = ui_state.PlannerState(config=dict(self.base), base_config=self.base)
        self.assertFalse(state.preset_view().available)


class TestThePickSurvivesAReload(ConfigDirCase):
    """The pick persists where the rest of the review dialog does not — it is
    a standing choice, and one that reset on reload would reintroduce the
    decision this arm exists to remove."""

    def test_a_saved_pick_is_what_the_next_load_layers(self):
        self.write_presets(preset_file("default", default={}, lean={
            "planning_rules.favorite_dinner_slots": 4}))
        run_sync(self.repo.save_presets_config({presets.ACTIVE_KEY: "lean"}))

        reloaded = apply_preset_layer(
            run_sync(self.repo.load_config()), run_sync(self.repo.load_presets_config())
        )
        self.assertEqual(reloaded[presets.ACTIVE_PRESET_CONFIG_KEY], "lean")
        self.assertEqual(reloaded["planning_rules"]["favorite_dinner_slots"], 4)


class TestTheWeekRecordsItsPreset(unittest.TestCase):
    """Without it the feedback arm can compare weeks and never explain them —
    the mirror of the store-and-never-read trap this codebase has paid for
    three times."""

    def make_plan(self, preset):
        return WeekPlan(
            days=["Monday"],
            servings_per_meal=2,
            generated_at="2026-09-02T09:00:00",
            week_start_date="2026-09-02",
            cook_events=[],
            slots=[],
            targets={},
            preset=preset,
        )

    def test_a_plan_written_before_presets_existed_reads_as_none(self):
        plan = WeekPlan.model_validate({
            "days": ["Monday"], "servings_per_meal": 2,
            "generated_at": "2026-09-02T09:00:00", "cook_events": [], "slots": [],
            "targets": {},
        })
        self.assertIsNone(plan.preset)

    def test_the_history_entry_carries_the_plan_s_preset(self):
        recipe = planner.Recipe(
            name="Green Chicken Curry", meal_type="dinner",
            ingredients=[planner.Ingredient(
                name="Chicken breast", quantity_g=200.0, nova_group=1,
                calories=330.0, protein_g=62.0, net_carbs_g=0.0, fat_g=7.2)],
            instructions=["Cook it."], prep_time_minutes=30, servings=1,
        )
        plan = self.make_plan("lean")
        plan.cook_events = [planner.CookEvent(
            slot_id="Monday:dinner", day="Monday", meal_type="dinner",
            portions=1, eaten_by=["Monday:dinner"], recipe=recipe)]

        with tempfile.TemporaryDirectory() as tmp:
            repo = LocalJSONRepository(data_dir=tmp)
            run_sync(planner.record_week_history(plan, repo))
            history = run_sync(repo.load_history())
        self.assertEqual([entry["preset"] for entry in history], ["lean"])


# --------------------------------------------------------------------------
# The preset editor — PROMPT-9
# --------------------------------------------------------------------------
#
# The editor is a copy of `ui_review.training_editor`'s list-of-records
# pattern plus a save-time check that is the *same* one the loader runs
# (`planner.resolve_preset_layer`, tested against the loader in
# `test_preset_validation.py`). What is tested here is the `PlannerState`
# logic the widget module leans on — the escape hatch per preset, validate-
# before-save, the delete guard, and the re-layer when the *active* preset is
# the one edited.


def editor_state(document: dict) -> ui_state.PlannerState:
    """A `PlannerState` seeded from a preset document the way `.load()` does —
    the nine `PRESET_SEEDED_FIELDS` read from the *layered* config, so a field
    the active preset moved starts where the preset put it."""
    base = shipped_base()
    config = apply_preset_layer(base, document)
    state = ui_state.PlannerState(
        config=config,
        base_config=base,
        presets_config=document,
        **{name: read(config) for name, read in ui_state.PRESET_SEEDED_FIELDS},
    )
    state._original_training_schedule = [dict(s) for s in state.training_schedule]
    return state


class TestTheEmptyPresetIsTheIdentity(ConfigDirCase):
    """§4.1 made testable: a preset every field of which is left unset has
    empty `overrides` and plans byte-identically to the base config."""

    def test_creating_an_all_unset_preset_stores_empty_overrides(self):
        self.write_presets(preset_file("default", default={}))
        state = editor_state(self.stored_presets())
        failures = run_sync(state.save_preset(
            self.repo, name="plain", label="Plain", editor_overrides={}, is_new=True
        ))
        self.assertEqual(failures, [])
        stored = self.stored_presets()
        self.assertEqual(stored["presets"]["plain"], {"label": "Plain", "overrides": {}})

    def test_that_preset_plans_byte_identically_to_the_base(self):
        base = shipped_base()
        document = preset_file("plain", default={}, plain={})
        layered = apply_preset_layer(base, document)
        layered.pop(presets.ACTIVE_PRESET_CONFIG_KEY, None)
        self.assertEqual(layered, load_app_config(base))


class TestTheEditorPreservesWhatItDoesNotExpose(ConfigDirCase):
    """A preset naming an override path the editor does not draw survives an
    edit untouched — the `training_schedule` escape hatch, per preset."""

    def setUp(self) -> None:
        super().setUp()
        document = preset_file("default", default={})
        document["presets"]["rich"] = {
            "label": "Rich",
            "overrides": {
                # exposed — the editor manages this one
                "dietary_rules.allowed_nova_groups": [1, 2],
                # not exposed — the editor has never heard of it
                "meal_styles.breakfast": {"quick": "just a shake"},
            },
            "a_hand_added_note": {"why": "kept by hand"},
        }
        self.write_presets(document)
        self.state = editor_state(self.stored_presets())

    def test_the_unexposed_path_and_hand_key_survive_an_edit(self):
        failures = run_sync(self.state.save_preset(
            self.repo, name="rich", label="Rich",
            # the editor re-submits only the exposed field, now changed
            editor_overrides={"dietary_rules.allowed_nova_groups": [1, 2, 3, 4]},
            is_new=False,
        ))
        self.assertEqual(failures, [])
        entry = self.stored_presets()["presets"]["rich"]
        self.assertEqual(
            entry["overrides"]["dietary_rules.allowed_nova_groups"], [1, 2, 3, 4]
        )
        self.assertEqual(
            entry["overrides"]["meal_styles.breakfast"], {"quick": "just a shake"}
        )
        self.assertEqual(entry["a_hand_added_note"], {"why": "kept by hand"})

    def test_clearing_an_exposed_field_drops_only_that_path(self):
        failures = run_sync(self.state.save_preset(
            self.repo, name="rich", label="Rich",
            editor_overrides={},  # user cleared the NOVA field
            is_new=False,
        ))
        self.assertEqual(failures, [])
        overrides = self.stored_presets()["presets"]["rich"]["overrides"]
        self.assertNotIn("dietary_rules.allowed_nova_groups", overrides)
        self.assertIn("meal_styles.breakfast", overrides)

    def test_every_other_preset_round_trips_verbatim(self):
        document = preset_file(
            "default", default={}, other={"serving_rules.servings_per_meal": 6}
        )
        document["presets"]["rich"] = {"label": "Rich", "overrides": {}, "kept": 1}
        self.write_presets(document)
        state = editor_state(self.stored_presets())
        run_sync(state.save_preset(
            self.repo, name="rich", label="Rich (new label)",
            editor_overrides={"dietary_rules.allowed_nova_groups": [1]}, is_new=False,
        ))
        stored = self.stored_presets()["presets"]
        self.assertEqual(stored["other"]["overrides"],
                         {"serving_rules.servings_per_meal": 6})
        self.assertEqual(stored["rich"]["kept"], 1)
        self.assertEqual(stored["rich"]["label"], "Rich (new label)")


class TestTheEditorValidatesBeforeItSaves(ConfigDirCase):
    """An invalid preset is refused at save, the failure is named, and the
    file is not written."""

    def setUp(self) -> None:
        super().setUp()
        self.write_presets(preset_file("default", default={}, lean={}))
        self.before = self.config_dir.joinpath("presets.json").read_text()
        self.state = editor_state(self.stored_presets())

    def test_a_schema_violation_is_returned_and_nothing_is_written(self):
        failures = run_sync(self.state.save_preset(
            self.repo, name="lean", label="Lean",
            editor_overrides={"planning_rules.min_baseline_cuisine_share": 5},
            is_new=False,
        ))
        self.assertTrue(failures)
        self.assertIn("min_baseline_cuisine_share", failures[0])
        self.assertEqual(
            self.config_dir.joinpath("presets.json").read_text(), self.before,
            "a refused save must leave presets.json byte-identical",
        )

    def test_a_reuse_window_past_history_depth_is_refused(self):
        failures = run_sync(self.state.save_preset(
            self.repo, name="lean", label="Lean",
            editor_overrides={"planning_rules.favorite_reuse_days":
                              {"breakfast": 7, "lunch": 40, "dinner": 21}},
            is_new=False,
        ))
        self.assertTrue(failures)
        self.assertIn("favorite_reuse_days", failures[0])
        self.assertEqual(
            self.config_dir.joinpath("presets.json").read_text(), self.before
        )

    def test_a_hand_edited_invalid_file_still_fails_at_load(self):
        """Load-time validation is unchanged — the editor's check is a second
        presentation of it, not a replacement."""
        self.write_presets(preset_file(
            "bad", bad={"planning_rules.min_baseline_cuisine_share": 5}
        ))
        with self.assertRaises(ValueError):
            run_sync(planner.load_config_with_models(self.repo))


class TestDeletingAPreset(ConfigDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.write_presets(preset_file(
            "lean", default={}, lean={"dietary_rules.allowed_nova_groups": [1, 2]}
        ))
        self.state = editor_state(self.stored_presets())

    def test_a_non_active_preset_is_removed(self):
        failures = run_sync(self.state.delete_preset(self.repo, "default"))
        self.assertEqual(failures, [])
        self.assertNotIn("default", self.stored_presets()["presets"])
        self.assertEqual(self.stored_presets()["active"], "lean")

    def test_deleting_the_active_preset_is_refused(self):
        before = self.config_dir.joinpath("presets.json").read_text()
        failures = run_sync(self.state.delete_preset(self.repo, "lean"))
        self.assertTrue(failures)
        self.assertIn("active preset", failures[0])
        self.assertEqual(
            self.config_dir.joinpath("presets.json").read_text(), before
        )

    def test_deleting_default_leaves_every_other_diff_rendering(self):
        run_sync(self.state.delete_preset(self.repo, "default"))
        view = self.state.preset_catalog_view()
        self.assertEqual([row.name for row in view.rows], ["lean"])
        self.assertEqual(
            view.rows[0].changes, ["dietary_rules.allowed_nova_groups → 1, 2"]
        )


class TestEditingTheActivePresetRelayers(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RecordingRepository()
        self.document = preset_file(
            "lean", default={}, lean={"serving_rules.servings_per_meal": 4}
        )
        self.state = editor_state(self.document)

    def test_editing_the_active_preset_moves_the_session_config(self):
        self.assertEqual(self.state.config["serving_rules"]["servings_per_meal"], 4)
        self.assertEqual(self.state.servings, 4)
        run_sync(self.state.save_preset(
            self.repo, name="lean", label="Lean",
            editor_overrides={"serving_rules.servings_per_meal": 6}, is_new=False,
        ))
        self.assertEqual(self.state.config["serving_rules"]["servings_per_meal"], 6)
        # the re-seed follows the config value that moved
        self.assertEqual(self.state.servings, 6)

    def test_editing_a_non_active_preset_does_not_touch_the_config(self):
        before = dict(self.state.config)
        run_sync(self.state.save_preset(
            self.repo, name="default", label="Default",
            editor_overrides={"dietary_rules.allowed_nova_groups": [1]}, is_new=False,
        ))
        self.assertEqual(self.state.config, before)


class TestThePreview(unittest.TestCase):
    def setUp(self) -> None:
        self.state = editor_state(preset_file("default", default={}))

    def test_it_is_pure_and_reports_identical_for_an_empty_preset(self):
        preview = self.state.preview_preset(
            name="plain", label="Plain", editor_overrides={}, is_new=True
        )
        self.assertTrue(preview.ok)
        self.assertTrue(preview.identical)
        self.assertEqual(preview.changes, [])

    def test_it_shows_the_carb_column_moving_on_one_day_only(self):
        preview = self.state.preview_preset(
            name="lowcarb", label="Low carb",
            editor_overrides={"weekly_schedule.Monday.net_carbs_g": 60}, is_new=True,
        )
        self.assertTrue(preview.ok)
        by_day = {d.day: d for d in preview.day_targets}
        self.assertEqual(by_day["Monday"].preset["net_carbs_g"], 60)
        self.assertEqual(
            by_day["Tuesday"].preset["net_carbs_g"],
            by_day["Tuesday"].base["net_carbs_g"],
        )

    def test_an_invalid_preview_carries_the_failure_not_a_raise(self):
        preview = self.state.preview_preset(
            name="oops", label="Oops",
            editor_overrides={"planning_rules.min_baseline_cuisine_share": 9},
            is_new=True,
        )
        self.assertFalse(preview.ok)
        self.assertTrue(any("min_baseline_cuisine_share" in f for f in preview.failures))


if __name__ == "__main__":
    unittest.main()

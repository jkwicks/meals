"""The view model and per-client state for `ui_app.py`.

`SlotView` is one flattened cell of the canvas; `PlannerState` is everything
one browser tab is looking at. Split out of the monolithic page module so a
widget module (cards, canvas, drawer) can depend on the *shape* of state
without depending on the `ui.page` handler that builds the DOM around it.
Nothing in this module makes a `ui.*` call — every method here either
computes and returns a value or mutates `self`, and the caller (still in
`ui_app.py` today) is what repaints in response. That split already held in
the monolith; this file just draws the module boundary where it already was.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pydantic import ValidationError

from planner import (
    MACRO_KEYS,
    CookEvent,
    Recipe,
    WeekPlan,
    apply_training_adjustments,
    calculate_daily_targets,
    day_multiplicity,
    is_sunday_prepped,
    load_app_config,
    meal_overrides_for,
    resolve_planner_model,
    split_targets,
    weeknight_prep_minutes,
)
from repository import LocalJSONRepository
from ui_theme import (
    LINK_COLOURS,
    LINK_SOURCE_MEAL,
    LINK_TARGET_MEAL,
    STATUS_COOK,
    STATUS_LEFTOVER,
    STATUS_MISSING,
    STATUS_SKIP,
    TRAINING_TYPE_LABELS,
    TRAINING_TYPES,
)
from week import (
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    WeekSpec,
    default_week_spec,
    eaten_on,
    humanize,
    leftover_link_error,
    link_leftover,
    meal_types,
    next_day_slot_id,
    portions_for,
    slot_id,
    slot_label,
    span_days,
    week_days,
)


@dataclass
class SlotView:
    """One cell of the canvas, flattened for rendering.

    The grid renders from these rather than branching on `WeekPlan` vs.
    `WeekSpec` at every label: a generated week and an un-generated one differ
    only in whether `recipe` is set, so both paths build the same shape and the
    card widget stays single-purpose.
    """

    day: str
    meal_type: str
    status: str
    title: str
    mode: str = MODE_SKIP
    style: str = ""
    cuisine: str = ""
    portions: int = 0
    prep_minutes: Optional[int] = None
    macros: Optional[dict] = None
    source_label: str = ""
    prep_badge: str = ""  # "fridge" | "freezer" | "" — see PREP_BADGE_STYLES
    prep_origin: str = ""  # tooltip: where in the Sunday prep timeline this came from
    recipe: object = None  # planner.Recipe, kept loose to avoid a hard import cycle

    # --- leftover chain wiring ---
    # `chain` is the shared index of the cook-plus-its-leftovers group this
    # card belongs to; both ends of a link carry the same one, which is what
    # lets the card colour and the hover outline tie them together visually.
    chain: Optional[int] = None
    feeds: List[str] = field(default_factory=list)  # cook cards: who eats this
    link_target: str = ""  # slot id "Link to next lunch" would point at us
    link_error: str = ""  # why that link isn't available, if it isn't

    @property
    def id(self) -> str:
        return slot_id(self.day, self.meal_type)

    @property
    def chain_colour(self) -> str:
        return LINK_COLOURS[(self.chain or 0) % len(LINK_COLOURS)]


@dataclass
class PlannerState:
    """Everything one browser tab is looking at.

    `week_plan` is None until a week has been generated at least once —
    `load_week_plan()` returning None is the documented cold start, not a
    failure, so the canvas falls back to previewing the default grid shape.
    """

    config: dict
    # models.json, loaded alongside config — the drawer's model select offers
    # `selectable_models(models_config)` and `.load()` uses
    # `models_config["meal_generation_model"]` as `model`'s starting value
    # until the select changes it for this session.
    models_config: dict = field(default_factory=dict)
    week_plan: Optional[WeekPlan] = None
    # Which cached week is on screen — a key into WEEK_SELECTION_LABELS, and
    # the `week_identifier` threaded through every `load_week_plan`/
    # `save_week_plan` call below. Switching it is what `switch_week` does;
    # plain reloads/generation act on whichever value is already here.
    week_selection: str = "current"
    week_start: str = ""
    servings: int = 2
    shop_days: List[str] = field(default_factory=list)
    # Shopping-drawer display toggle only — never persisted and never fed to
    # `generate_week_plan`. True partitions the week into one window per cook
    # day (`shopping_windows(state.days, state.days)`) instead of the
    # configured `shop_days` trips; the underlying cook events and quantities
    # are identical either way, only the grouping changes.
    daily_shop_mode: bool = False
    # Real value is always set by `.load()` via `resolve_planner_model` —
    # this placeholder only exists because dataclasses require a default.
    model: str = ""
    focus: Optional[SlotView] = None
    edited: bool = False
    # Food already in the house, to be cooked through. Seeded from config's
    # `inventory_to_clear` and edited in the drawer; it reaches the model as a
    # priority, never a constraint (`planner.inventory_instruction`).
    pantry: List[str] = field(default_factory=list)
    # day -> partial macro dict, holding only what differs from config.json.
    # Storing the *difference* rather than a full copy is what lets the drawer
    # say which days are overridden and reset them one at a time, and means a
    # day nobody touched still follows the file if the file changes.
    target_overrides: Dict[str, dict] = field(default_factory=dict)
    # Workout sessions ({day, time, type, duration_minutes, estimated_burn_kcal}).
    # Seeded from config's `training_schedule` and edited in the drawer; like
    # the pantry it is an input to the *next* run, folded into
    # `planning_config()` by `planner.apply_training_adjustments` and never
    # written back to config.json.
    training_schedule: List[dict] = field(default_factory=list)
    # Which day's context-pipeline dialog is open, if any. Held the same way
    # as `focus` is for the recipe detail dialog: one dialog reused for all
    # seven days, refreshable off this key rather than seven pre-built dialogs.
    pipeline_day: Optional[str] = None
    # A run holds this client for 30s-3min per meal type. The loop stays free
    # (planner dispatches each call to a thread), so the browser is still live
    # and perfectly able to click Generate again — this is the flag that says
    # no. Per-client, like everything else here: two tabs generating at once
    # would race to overwrite the same week_plan.json.
    generating: bool = False
    # Which single day a "Regenerate day" click is mid-flight for, if any.
    # Same purpose as `generating` but scoped to one day — guards against a
    # second regenerate click (this day or another) racing the first, and
    # against overlapping with a whole-week run, without needing a canvas
    # repaint to show it: the clicked button's own `loading` prop covers that.
    regenerating_day: Optional[str] = None
    # Same purpose as `regenerating_day` but scoped to one meal (one slot_id),
    # for the per-card "Regenerate meal" refresh icon — guards against a
    # second regenerate click (this meal, another meal, a whole day, or a
    # whole-week run) racing this one.
    regenerating_meal: Optional[str] = None

    # The recipe catalog (recipes_master.json) — every recipe ever favorited
    # or imported, record shape {id, content_key, recipe, is_favorite,
    # source, added_at, updated_at}. It is the single place recipe content
    # outlives the week it was generated in; `week_plan.json` is overwritten
    # every run. Loaded once at startup and kept in sync with disk by every
    # handler that mutates it — there is no re-read on repaint, since the
    # whole point is that a browser tab's list doesn't jump around under a
    # card the user is mid-click on.
    recipe_catalog: List[dict] = field(default_factory=list)
    catalog_search: str = ""
    # Which card the swap modal is open for, and its in-progress filter/pick.
    # Held on state rather than as dialog-local variables so `swap_dialog_body`
    # can be a plain `@ui.refreshable` that reads state, the same pattern
    # `recipe_detail` uses for `state.focus`.
    swap_target: Optional[SlotView] = None
    swap_filter: Optional[str] = None
    swap_query: str = ""
    swap_selected_id: Optional[str] = None
    # Import dialog scratch text/toggle; cleared once a paste is successfully
    # turned into a catalog entry. `import_as_favorite` defaults off — an
    # import lands in the catalog but isn't favorited unless asked for.
    import_text: str = ""
    import_as_favorite: bool = False
    edit_catalog_id: Optional[str] = None
    edit_catalog_name: str = ""

    # The live week shape. Held rather than re-derived on every access because
    # it is now editable: rebuilding it per read would throw away the links the
    # user just made. `_spec_shape` records what it was derived from, so a
    # genuine change of week shape still rebuilds it.
    _spec: Optional[WeekSpec] = None
    _spec_shape: tuple = ()

    @classmethod
    async def load(cls, repository: LocalJSONRepository) -> "PlannerState":
        # `load_app_config` validates config.json against `AppConfig` here,
        # once, at startup — the same schema check the CLI gets from
        # `load_config_with_models`. Every field below is then guaranteed
        # present with a real value, so this reads them directly instead of
        # each picking its own `.get(key, DEFAULT)` fallback.
        config = load_app_config(await repository.load_config())
        models_config = await repository.load_models_config()
        state = cls(
            config=config,
            models_config=models_config,
            week_start=config["week_start_day"],
            servings=config["serving_rules"]["servings_per_meal"],
            shop_days=list(config["shopping"]["shop_days"]),
            model=resolve_planner_model(dict(config, models=models_config)),
            pantry=[str(item).strip() for item in config["inventory_to_clear"] if str(item).strip()],
            training_schedule=[dict(session) for session in config["training_schedule"]],
        )
        state.recipe_catalog = await repository.load_recipe_catalog()
        await state.reload_plan(repository)
        return state

    async def reload_plan(self, repository: LocalJSONRepository) -> None:
        """Pull the cached week off disk. Validation lives here, not in the
        repository, which deliberately deals in plain dicts.

        Acts on whichever week is already selected — `switch_week` is what
        changes that.
        """
        raw = await repository.load_week_plan(self.week_selection)
        self.adopt_plan(WeekPlan.model_validate(raw) if raw else None)

    async def switch_week(self, repository: LocalJSONRepository, target_week: str) -> None:
        """Change which cached week is on screen, loading it from disk.

        The load happens before `week_selection` is reassigned, same
        ordering as `reload_plan`: if it raises, the previous week is still
        the one showing rather than the state flipping to a selection whose
        plan never actually loaded. No generation call here — this only ever
        reads what's already on disk, per CLAUDE.md's "generating is the only
        thing that writes to disk."
        """
        raw = await repository.load_week_plan(target_week)
        self.week_selection = target_week
        self.adopt_plan(WeekPlan.model_validate(raw) if raw else None)

    def adopt_plan(self, plan: Optional[WeekPlan]) -> None:
        """Make `plan` the week on screen, discarding any unsaved edits.

        Both callers have just made disk the truth again — a reload read it, a
        generation wrote it — so the edited spec is stale by definition and the
        "edited — not saved" chip has to clear with it. Dropping `_spec` rather
        than rebuilding it here means the next `spec` read derives from the new
        plan, which is where that derivation already lives.
        """
        self.week_plan = plan
        self._spec = None
        self._spec_shape = ()
        self.edited = False

    @property
    def days(self) -> List[str]:
        return week_days(self.config, self.week_start)

    @property
    def meal_types(self) -> List[str]:
        return meal_types(self.config)

    def _shape(self) -> tuple:
        """What the spec is derived *from*; a change here invalidates edits.

        `servings` only counts for an un-generated week: a generated plan's
        portions come from `week_plan.servings_per_meal`, so nudging the
        drawer's people-per-meal must not silently discard links it can't
        affect.
        """
        plan = self.week_plan
        return (
            tuple(self.days),
            plan.generated_at if plan else None,
            None if plan else self.servings,
        )

    @property
    def spec(self) -> WeekSpec:
        """The week as a spec, whether or not it has been generated.

        A generated plan carries its own slots, so reusing them here means
        `portions_for` and friends work identically on both — the derived
        portion count in the preview is the same arithmetic that produced the
        real one.
        """
        shape = self._shape()
        if self._spec is None or self._spec_shape != shape:
            if self.week_plan:
                self._spec = WeekSpec(
                    days=self.days,
                    servings_per_meal=self.week_plan.servings_per_meal,
                    slots=self.week_plan.slots,
                )
            else:
                self._spec = default_week_spec(self.config, self.week_start, self.servings)
            self._spec_shape = shape
        return self._spec

    def apply_spec(self, spec: WeekSpec) -> None:
        """Adopt an edited spec as the week's live shape.

        The plan keeps its *own* copy of the slots and `day_slot_macros` walks
        those, so replacing only the spec would leave the telemetry header
        reporting the week as it was before the edit. Cook events are rescaled
        for the same reason: portions are derived from how many slots claim a
        cook (`week.portions_for`), so the batch — and its ingredient
        quantities — has to move with them.

        Nothing is written to disk. This is a local reshuffle of a week that
        has already been generated; `edited` is what tells the header to say
        so.
        """
        self._spec = spec
        self.edited = True

        plan = self.week_plan
        if plan is None:
            self._spec_shape = self._shape()
            return

        portions = portions_for(spec)
        claims = eaten_on(spec)

        def rescaled(event: CookEvent) -> CookEvent:
            if event.slot_id not in portions or event.portions <= 0:
                return event
            target = portions[event.slot_id]
            return event.model_copy(
                update={
                    "portions": target,
                    "eaten_by": list(claims.get(event.slot_id, [event.slot_id])),
                    # `self.config` threaded through so the storage note uses the
                    # configured `inventory_rules.fridge_safe_days`. Omitting it
                    # silently fell back to week.DEFAULT_INVENTORY_RULES, so an
                    # edited config would disagree with the note on the card.
                    "recipe": event.recipe.scale_to_servings(
                        target, span_days(spec, event.slot_id), self.config
                    ),
                }
            )

        self.week_plan = plan.model_copy(
            update={
                "slots": list(spec.slots),
                # A slot that stopped being a cook keeps its event in the list,
                # orphaned: nothing resolves to it any more, and holding it
                # means the recipe is still there if the week is re-pointed.
                "cook_events": [rescaled(event) for event in plan.cook_events],
            }
        )
        self._spec_shape = self._shape()

    def link_to_next_lunch(self, source_id: str) -> Optional[str]:
        """Point the following day's lunch at this cook. Returns why not, or None.

        The whole "Macro Action": one click turns tomorrow's lunch into
        leftovers of tonight's dinner, which — because portions are derived —
        is also what increases tonight's batch. There is no portion number to
        set anywhere in this flow, by design.
        """
        spec = self.spec
        source = spec.by_id().get(source_id)
        if source is None:
            return "That meal isn't part of this week."

        target_id = next_day_slot_id(spec, source.day, LINK_TARGET_MEAL)
        if target_id is None:
            return f"{source.day} is the last day of the week — there's no next lunch."

        error = leftover_link_error(spec, target_id, source_id)
        if error:
            return error

        self.apply_spec(link_leftover(spec, target_id, source_id))
        return None

    def shuffle_styles(self) -> None:
        """Blank the style/cuisine on every cook slot so the next generation
        re-rolls them from scratch.

        `resolve_auto_choices` only picks a fresh style/cuisine when a slot's
        is empty (planner.py) — once a week has been generated once, its
        slots carry the concrete values from that run and every later
        "Generate" re-requests the exact same style/cuisine per slot forever,
        varying only the dish the model composes inside it. This is the
        explicit escape hatch: it touches only style/cuisine, not mode or
        leftover links, so `apply_spec` can go through its normal rescale
        path unchanged.
        """
        spec = self.spec
        resolved = [
            slot.model_copy(update={"style": None, "cuisine": None})
            if slot.mode == MODE_COOK
            else slot
            for slot in spec.slots
        ]
        self.apply_spec(spec.model_copy(update={"slots": resolved}))

    def swap_slot_with_favorite(self, target_slot_id: str, favorite_recipe: dict) -> Optional[str]:
        """Replace a cooked slot's recipe with a saved favorite. Returns why not, or None.

        Same shape as `link_to_next_lunch`: a single sentence about the one
        slot clicked, state only mutates on success, and the caller is the one
        that repaints (`refresh_all`) — this class stays free of `ui.*` calls,
        same as everywhere else in `PlannerState`.

        Resolved through `source` for a leftover slot, same as `slot_views`,
        because the recipe belongs to the cook event, not the eating slot —
        swapping a leftover's favorite has to change the meal everyone in that
        chain eats, not fork a second copy only this slot sees.
        """
        if self.week_plan is None:
            return "Generate a week before swapping in a favorite."

        spec = self.spec
        slot = spec.by_id().get(target_slot_id)
        if slot is None:
            return "That meal isn't part of this week."
        if slot.mode == MODE_SKIP:
            return f"{slot_label(target_slot_id)} is skipped — nothing to swap."

        source_id = slot.id if slot.mode == MODE_COOK else slot.source
        event = self.week_plan.by_slot().get(source_id or "")
        if event is None:
            return f"{slot_label(target_slot_id)} hasn't been generated yet — nothing to swap."

        try:
            recipe = Recipe.model_validate(favorite_recipe)
        except ValidationError as exc:
            return f"That favorite isn't a usable recipe: {exc}"

        # Favorites are expected in the same shape generation produces: one
        # serving. A favorite saved off an already-scaled batch card would
        # carry servings > 1, so it's normalised back to one serving first —
        # `scale_to_servings` below is what re-expands it to this slot's batch.
        if recipe.servings != 1:
            recipe = recipe.resize_by_factor(1 / recipe.servings).model_copy(
                update={"servings": 1}
            )

        # portions/keeps_for_days mirror `apply_spec`'s rescale: the batch
        # size is still derived from how many slots claim this cook, a
        # favorite swap doesn't change that.
        scaled = recipe.scale_to_servings(
            event.portions,
            keeps_for_days=span_days(spec, source_id),
            config=self.config,
        )
        new_event = event.model_copy(update={"recipe": scaled})
        self.week_plan = self.week_plan.model_copy(
            update={
                "cook_events": [
                    new_event if e.slot_id == source_id else e
                    for e in self.week_plan.cook_events
                ]
            }
        )
        # No apply_spec here: the slot's mode/source didn't change, only the
        # cook event's recipe, which day_slot_macros and slot_views already
        # read live off week_plan.cook_events — no _spec rebuild needed.
        self.edited = True
        return None

    def planning_config(self) -> dict:
        """Config as the *next* generation will see it.

        The file on disk, plus everything the drawer can change: the model, the
        per-day target overrides, the pantry list and the training schedule.
        Assembled here rather than at the call site so one object carries all
        of it — `generate_week_plan`, `validate_week`, `split_targets` and
        `inventory_instruction` all read plain config, and each would
        otherwise need its own patch applied.

        `apply_training_adjustments` runs last, on top of the drawer's target
        overrides: a workout's burn stacks onto whatever the day target
        currently reads, edited or not, and its per-meal pin/notes have to be
        in place before `calculate_daily_targets`/`meal_overrides_for` read
        this config — that is what makes the telemetry header a live preview
        of a training edit rather than something only the next generation
        would show.

        Nothing here is written back to config.json. Overrides are meant to be
        "this week is different", and generating is still the only thing in
        this app that touches disk.
        """
        schedule = {
            day: dict(day_config, **self.target_overrides.get(day, {}))
            for day, day_config in self.config["weekly_schedule"].items()
        }
        return apply_training_adjustments(
            dict(
                self.config,
                weekly_schedule=schedule,
                inventory_to_clear=list(self.pantry),
                openrouter_model=self.model,
                training_schedule=[dict(session) for session in self.training_schedule],
                # generate_week_plan/build_client/fit_recipe_to_budget etc.
                # all read `config.get("models")` — see planner.py — so this
                # is what lets a run resolve request_timeout_seconds and the
                # base URL from models.json instead of the pre-models.json
                # literals.
                models=self.models_config,
            )
        )

    def planned_targets(self, day: str) -> dict:
        """What the next run will aim at for `day` — file numbers plus overrides.

        `fat_g` comes back derived, so the drawer shows the same figure the
        model will be told rather than one the UI computed its own way.
        """
        return calculate_daily_targets(day, self.planning_config())

    def set_target(self, day: str, key: str, value: float) -> None:
        """Record a drawer edit to one of a day's macro targets.

        A value equal to config.json clears that key instead of storing a no-op
        override, so "overridden" always means "differs from the file". That is
        also what makes the reset button able to undo itself: it writes the
        file's numbers back into the inputs, and the change events those fire
        land here and cancel out rather than re-creating the override.
        """
        base = self.config["weekly_schedule"].get(day, {})
        override = dict(self.target_overrides.get(day, {}))
        if float(base.get(key, 0)) == float(value):
            override.pop(key, None)
        else:
            override[key] = float(value)

        if override:
            self.target_overrides[day] = override
        else:
            self.target_overrides.pop(day, None)

    def clear_targets(self, day: Optional[str] = None) -> None:
        """Drop one day's overrides, or the whole week's."""
        if day is None:
            self.target_overrides.clear()
        else:
            self.target_overrides.pop(day, None)

    def add_training_session(self) -> None:
        """Append a new workout row with sane defaults, ready to edit in place."""
        self.training_schedule.append(
            {
                "day": self.days[0] if self.days else "Monday",
                "time": "07:00",
                "type": TRAINING_TYPES[0],
                "duration_minutes": 60,
                "estimated_burn_kcal": 300,
            }
        )

    def remove_training_session(self, index: int) -> None:
        if 0 <= index < len(self.training_schedule):
            self.training_schedule.pop(index)

    def has_training(self, day: str) -> bool:
        return any(session.get("day") == day for session in self.training_schedule)

    def targets_for(self, day: str) -> dict:
        """The denominator the telemetry header measures a day against.

        An override — or a workout scheduled that day — wins over the
        generated plan's own targets on purpose: the point of editing a
        target, or a training session, before a run is to see how far the
        current week sits from where you are about to aim it. Without this, a
        cached `week_plan.json` would keep showing yesterday's target and a
        training edit would silently do nothing until the next generation —
        exactly the "not live" failure this control exists to avoid. Otherwise
        a generated week is measured against what it was generated for, and an
        un-generated one against config, so the header always has something to
        divide by.
        """
        if day in self.target_overrides or self.has_training(day):
            return self.planned_targets(day)
        if self.week_plan and day in self.week_plan.targets:
            return self.week_plan.targets[day]
        return self.planned_targets(day)

    def totals_for(self, day: str) -> dict:
        if not self.week_plan:
            return {key: 0.0 for key in MACRO_KEYS}
        return self.week_plan.day_slot_macros(day)

    def slot_views(self) -> Dict[str, SlotView]:
        """slot_id -> SlotView for every slot in the week."""
        spec = self.spec
        events = self.week_plan.by_slot() if self.week_plan else {}
        views: Dict[str, SlotView] = {}

        # Hoisted: both are whole-week scans, and calling them per slot made
        # rendering 28 cards quadratic in the size of the week.
        portions = portions_for(spec)
        claims = eaten_on(spec)

        # One chain per cook that anything else eats — a cook nobody inherits
        # from is not a link, so it gets no colour and no marker.
        chains = {
            slot.id: index
            for index, slot in enumerate(
                cook for cook in spec.cook_slots() if len(claims.get(cook.id, [])) > 1
            )
        }

        for slot in spec.slots:
            if slot.mode == MODE_SKIP:
                views[slot.id] = SlotView(
                    day=slot.day,
                    meal_type=slot.meal_type,
                    status=STATUS_SKIP,
                    title="Skipped",
                    mode=slot.mode,
                )
                continue

            # A leftover shows the *source* recipe: it is the same food, so
            # resolving through `source` here is what makes the two cards
            # visibly the same dish.
            source_id = slot.id if slot.mode == MODE_COOK else slot.source
            event = events.get(source_id or "")
            source_label = slot_label(slot.source, short=True) if slot.source else ""

            link_target, link_error = "", ""
            if slot.mode == MODE_COOK and slot.meal_type == LINK_SOURCE_MEAL:
                link_target = next_day_slot_id(spec, slot.day, LINK_TARGET_MEAL) or ""
                link_error = (
                    leftover_link_error(spec, link_target, slot.id) or ""
                    if link_target
                    else f"{slot.day} is the last day of the week."
                )

            common = dict(
                day=slot.day,
                meal_type=slot.meal_type,
                mode=slot.mode,
                style=humanize(slot.style),
                cuisine=humanize(slot.cuisine),
                portions=portions.get(source_id or "", 0),
                source_label=source_label if slot.mode == MODE_LEFTOVER else "",
                chain=chains.get(source_id or ""),
                feeds=(
                    [slot_label(value, short=True) for value in claims.get(slot.id, [])[1:]]
                    if slot.mode == MODE_COOK
                    else []
                ),
                link_target=link_target,
                link_error=link_error,
            )

            if event is None:
                # Two different absences. With a plan loaded, a missing event
                # means this slot's cook (or the cook it points at) is in
                # WeekPlan.failures — that is the red "not generated" state,
                # and it must stay visible so the gap is obvious rather than
                # silently blank.
                # With no plan at all nothing has failed: the grid is a
                # *preview* of the shape about to be generated, so the slot
                # keeps its planned mode and only the recipe is missing.
                views[slot.id] = SlotView(
                    status=(
                        STATUS_MISSING
                        if self.week_plan
                        else (STATUS_COOK if slot.mode == MODE_COOK else STATUS_LEFTOVER)
                    ),
                    title="Not generated" if self.week_plan else "To be generated",
                    **common,
                )
                continue

            # A leftover eating a Sunday-prepped batch gets a badge and a
            # reheat/assemble estimate instead of the cook's from-scratch
            # prep time — see `planner.is_sunday_prepped`. "fridge" vs.
            # "freezer" mirrors the same span-vs-fridge-safe-days threshold
            # `storage_note` used to write the batch's own storage note.
            prep_badge, prep_origin = "", ""
            if slot.mode == MODE_LEFTOVER and is_sunday_prepped(event, self.week_plan):
                fridge_safe_days = self.config["inventory_rules"]["fridge_safe_days"]
                # Per-slot distance from its cook day, not `span_days`'s
                # whole-batch span to its *farthest* eater — a Tuesday
                # portion of a batch that runs to next Sunday is still
                # fridge-fresh even though the Sunday portion isn't.
                days_since_cook = spec.day_index(slot.day) - spec.day_index(event.day)
                frozen = days_since_cook >= fridge_safe_days
                prep_badge = "freezer" if frozen else "fridge"
                prep_origin = (
                    f"From the Sunday prep session: {event.recipe.name} "
                    f"({event.portions} portions, cooked {event.day})"
                    + (" — frozen, thaw ahead of eating" if frozen else " — kept refrigerated")
                )

            # Style and cuisine come off the event, which recorded whatever
            # `resolve_auto_choices` settled on — the slot may still say
            # "auto".
            views[slot.id] = SlotView(
                status=STATUS_COOK if slot.mode == MODE_COOK else STATUS_LEFTOVER,
                title=event.recipe.name,
                prep_minutes=(
                    weeknight_prep_minutes(event, self.week_plan)
                    if slot.mode == MODE_LEFTOVER
                    else event.recipe.prep_time_minutes
                ),
                macros=event.recipe.per_serving_macros,
                recipe=event.recipe,
                prep_badge=prep_badge,
                prep_origin=prep_origin,
                **{
                    **common,
                    "style": humanize(event.style),
                    "cuisine": humanize(event.cuisine),
                },
            )

        return views


def pipeline_value(state: PlannerState, day: str, key: str) -> Optional[str]:
    """What a connected pipeline stage has for `day`, or None if unset.

    Only "workout" is wired today — it reads the same `training_schedule`
    `has_training()`/the telemetry ⚡ marker already use, so this is a real
    signal, not a placeholder, from the day this pipeline ships. The other
    stages stay in `PIPELINE_STAGES` with `connected=False` and never reach
    here.
    """
    if key == "workout":
        session = next(
            (s for s in state.training_schedule if s.get("day") == day), None
        )
        if session is None:
            return None
        return TRAINING_TYPE_LABELS.get(session["type"], session["type"])
    return None


def slot_target_budget(state: PlannerState, view: SlotView) -> Optional[dict]:
    """The per-serving macro budget generation would aim this slot at right now.

    Mirrors `generate_day`'s own split rather than a rough per-day average:
    leftover slots eaten that day have their macros subtracted from the day
    target first, then the remainder is divided across the day's cook slots
    by `split_targets` — the same function and the same order of operations
    generation uses. So the swap modal's "target" column is the number a
    fresh generation would have handed the model for this slot, not an
    approximation of it.
    """
    if not view.day:
        return None
    spec = state.spec
    config = state.planning_config()
    day_slots = [slot for slot in spec.slots if slot.day == view.day]
    cook_slots = [slot for slot in day_slots if slot.mode == MODE_COOK]
    if not cook_slots:
        return None

    # `planner.day_multiplicity`, not a local count off `eaten_on`: the latter
    # counts every slot claiming a cook across the WHOLE WEEK, so a dinner
    # feeding tomorrow's lunch scored 2 here and 1 in generation. That inflated
    # `split_targets`'s total weight and understated every budget on the day by
    # ~30%. Sharing generation's own function is what keeps this honest.
    multiplicity = day_multiplicity(spec, view.day)

    events = state.week_plan.by_slot() if state.week_plan else {}
    carried = {key: 0.0 for key in MACRO_KEYS}
    for slot in day_slots:
        if slot.mode != MODE_LEFTOVER:
            continue
        event = events.get(slot.source or "")
        if event is None:
            continue
        per_serving = event.recipe.per_serving_macros
        for key in MACRO_KEYS:
            carried[key] += per_serving[key]

    targets = state.targets_for(view.day)
    remaining = {key: max(0.0, float(targets[key]) - carried[key]) for key in MACRO_KEYS}
    overrides = meal_overrides_for(view.day, config)
    budgets = split_targets(remaining, cook_slots, multiplicity, config, overrides)

    slot = spec.by_id().get(view.id)
    if slot is None:
        return None
    source_id = slot.id if slot.mode == MODE_COOK else slot.source
    return budgets.get(source_id or "")

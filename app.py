import copy

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from planner import (
    DEFAULT_MODEL,
    calculate_daily_targets,
    configure_logging,
    day_slot_macros,
    generate_week_plan,
    load_config,
    load_history,
    per_serving_totals,
    record_week_history,
    resolve_auto_choices,
)
from shopping import (
    aggregate_cook_events,
    cook_plan_lines,
    format_quantity,
    format_shopping_list_keep,
    format_shopping_list_markdown,
)
from week import (
    AUTO,
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    MODES,
    SlotSpec,
    WeekSpec,
    all_style_keys,
    autofill_leftovers,
    default_week_spec,
    eaten_on,
    humanize,
    meal_types,
    portions_for,
    shopping_windows,
    slot_id,
    validate_week,
    week_days,
    week_warnings,
)

load_dotenv()

CONFIG_PATH = "config.json"
MODEL_OPTIONS = [
    "anthropic/claude-sonnet-5",
    "deepseek/deepseek-v4-flash",
    # Free OpenRouter models below. Spot-checked directly (bypassing
    # instructor) with a trivial prompt before adding: finish_reason "stop"
    # and near-zero reasoning_tokens, per the diagnostic method in
    # CLAUDE.md. Several other free models were tried and rejected because
    # they burned most/all of their completion budget on hidden reasoning
    # tokens instead of the actual reply (nvidia/nemotron-nano-9b-v2:free,
    # nvidia/nemotron-3-super-120b-a12b:free, poolside/laguna-xs-2.1:free)
    # — the same failure mode documented for gpt-oss-20b / nemotron-3-nano
    # / ling-3.0 / north-mini-code.
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
    "poolside/laguna-s-2.1:free",
]

# Grid column headers. Kept as constants because they're referenced by
# st.data_editor's column_config, the DataFrame, and the row<->spec mapping.
COL_DAY = "Day"
COL_MEAL = "Meal"
COL_MODE = "Mode"
COL_STYLE = "Style"
COL_CUISINE = "Cuisine"
COL_SOURCE = "Leftover of"
COL_EXTRA = "Extra portions"

NO_SOURCE = "—"

configure_logging()
st.set_page_config(page_title="AI Weekly Meal Planner", layout="wide")


# --------------------------------------------------------------------------
# Grid <-> WeekSpec mapping
# --------------------------------------------------------------------------


def source_label(value: str) -> str:
    day, meal_type = value.split(":")
    return f"{day} · {meal_type}"


def source_value(label: str) -> str:
    if not label or label == NO_SOURCE:
        return ""
    day, meal_type = label.split(" · ")
    return slot_id(day, meal_type)


def spec_to_rows(spec: WeekSpec) -> list:
    return [
        {
            COL_DAY: slot.day,
            COL_MEAL: slot.meal_type,
            COL_MODE: slot.mode,
            COL_STYLE: slot.style or AUTO,
            COL_CUISINE: slot.cuisine or AUTO,
            COL_SOURCE: source_label(slot.source) if slot.source else NO_SOURCE,
            COL_EXTRA: int(slot.extra_portions),
        }
        for slot in spec.slots
    ]


def rows_to_spec(rows, days: list, servings_per_meal: int) -> WeekSpec:
    """Read the edited grid back into a WeekSpec.

    `auto` and the em-dash placeholder become None so the rest of the codebase
    only ever sees a concrete choice or an explicit absence.
    """
    slots = []
    for row in rows:
        mode = row[COL_MODE]
        style = row[COL_STYLE]
        cuisine = row[COL_CUISINE]
        slots.append(
            SlotSpec(
                day=row[COL_DAY],
                meal_type=row[COL_MEAL],
                mode=mode,
                style=None if (style in (AUTO, None) or mode != MODE_COOK) else style,
                cuisine=None if (cuisine in (AUTO, None) or mode != MODE_COOK) else cuisine,
                source=source_value(row[COL_SOURCE]) if mode == MODE_LEFTOVER else None,
                extra_portions=int(row[COL_EXTRA] or 0) if mode == MODE_COOK else 0,
            )
        )
    return WeekSpec(days=days, servings_per_meal=servings_per_meal, slots=slots)


def set_grid_rows(rows: list) -> None:
    """Replace the grid's data programmatically.

    A keyed st.data_editor keeps its own dict of edited cells in
    st.session_state["grid_editor"] and replays it over whatever data it's
    given. Without dropping that, a button that rewrites the rows appears to
    do nothing for any cell the user had already touched by hand.
    """
    st.session_state["grid_rows"] = rows
    st.session_state.pop("grid_editor", None)


def ensure_grid(config: dict, week_start: str, servings: int) -> None:
    """(Re)build the grid rows in session state when the week shape changes.

    Rebuilding on every rerun would throw away the user's edits; rebuilding
    never would leave stale days after changing the week start. Keying on the
    week shape is the middle ground.
    """
    shape = (week_start, tuple(meal_types(config)))
    if st.session_state.get("grid_shape") != shape:
        st.session_state["grid_shape"] = shape
        set_grid_rows(spec_to_rows(default_week_spec(config, week_start, servings)))


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------


def render_sidebar(config: dict) -> dict:
    st.sidebar.header("Week Setup")

    all_days = list(config["weekly_schedule"].keys())
    default_start = config.get("week_start_day", all_days[0])
    week_start = st.sidebar.selectbox(
        "Week starts on",
        all_days,
        index=all_days.index(default_start) if default_start in all_days else 0,
        help="Sets day 1. Leftovers may only be eaten on or after their cook day in this order.",
    )
    days = week_days(config, week_start)

    servings = st.sidebar.slider(
        "People per meal",
        min_value=1,
        max_value=8,
        value=int(config.get("serving_rules", {}).get("servings_per_meal", 2)),
        help="Batch sizes are derived from this times the number of meals each cook covers.",
    )

    st.sidebar.subheader("Shopping trips")
    default_shop_days = [
        day for day in config.get("shopping", {}).get("shop_days", []) if day in days
    ]
    shop_days = st.sidebar.multiselect(
        "Days you shop",
        days,
        default=default_shop_days,
        help="Each trip covers from that day until the next one.",
    )

    st.sidebar.subheader("Model")
    model_default = config.get("openrouter_model", DEFAULT_MODEL)
    model = st.sidebar.selectbox(
        "OpenRouter Model",
        MODEL_OPTIONS,
        index=MODEL_OPTIONS.index(model_default) if model_default in MODEL_OPTIONS else 0,
    )

    return {"week_start": week_start, "days": days, "servings": servings,
            "shop_days": shop_days, "model": model}


# --------------------------------------------------------------------------
# Plan setup tab
# --------------------------------------------------------------------------


def render_targets_editor(config: dict, days: list) -> dict:
    """Per-day macro targets as an editable table, returning an overridden config.

    Fat is never entered — calculate_daily_targets derives it from the other
    three, exactly as the CLI does, so the displayed Fat column is always the
    number the model will actually be given.
    """
    st.markdown("#### Daily targets")
    base = config["weekly_schedule"]
    rows = []
    for day in days:
        targets = calculate_daily_targets(day, config)
        rows.append(
            {
                "Day": day,
                "Calories": int(base[day]["calories"]),
                "Protein (g)": int(base[day]["protein_g"]),
                "Net carbs (g)": int(base[day]["net_carbs_g"]),
                "Fat (g)": targets["fat_g"],
            }
        )

    edited = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        disabled=["Day", "Fat (g)"],
        column_config={
            "Calories": st.column_config.NumberColumn(min_value=800, max_value=6000, step=50),
            "Protein (g)": st.column_config.NumberColumn(min_value=0, max_value=400, step=5),
            "Net carbs (g)": st.column_config.NumberColumn(min_value=0, max_value=500, step=5),
            "Fat (g)": st.column_config.NumberColumn(
                help="Computed in Python from the other three — not editable.", format="%.1f"
            ),
        },
        key="targets_editor",
    )

    day_config = copy.deepcopy(config)
    for row in edited.to_dict("records"):
        day_config["weekly_schedule"][row["Day"]].update(
            {
                "calories": int(row["Calories"]),
                "protein_g": int(row["Protein (g)"]),
                "net_carbs_g": int(row["Net carbs (g)"]),
            }
        )
    return day_config


def render_grid(config: dict, days: list, servings: int) -> WeekSpec:
    st.markdown("#### Meal grid")
    st.caption(
        "One row per meal. **Mode** decides whether it's cooked fresh, eaten as "
        "leftovers of an earlier cook, or skipped. Leave Style and Cuisine on "
        "`auto` to let the planner rotate them for you."
    )

    quick_cols = st.columns([1, 1, 1, 3])
    if quick_cols[0].button("Lunches = last night's dinner", use_container_width=True):
        spec = rows_to_spec(st.session_state["grid_rows"], days, servings)
        set_grid_rows(spec_to_rows(autofill_leftovers(spec, "lunch", "dinner")))
        st.rerun()
    if quick_cols[1].button("Reset grid", use_container_width=True):
        set_grid_rows(spec_to_rows(default_week_spec(config, days[0], servings)))
        st.rerun()

    current = rows_to_spec(st.session_state["grid_rows"], days, servings)
    # Only slots currently set to cook can be a leftover source — recomputed
    # each rerun so the dropdown tracks edits made on the previous pass. Any
    # source already in use is kept in the list even if it stopped being a
    # cook slot: a SelectboxColumn value outside its options is a render
    # error, and a stale pointer should surface as a validation message
    # instead.
    source_options = [NO_SOURCE] + [source_label(slot.id) for slot in current.cook_slots()]
    for row in st.session_state["grid_rows"]:
        if row[COL_SOURCE] not in source_options:
            source_options.append(row[COL_SOURCE])

    edited = st.data_editor(
        pd.DataFrame(st.session_state["grid_rows"]),
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        height=min(1000, 60 + 35 * len(st.session_state["grid_rows"])),
        disabled=[COL_DAY, COL_MEAL],
        column_config={
            COL_MODE: st.column_config.SelectboxColumn(options=MODES, required=True),
            COL_STYLE: st.column_config.SelectboxColumn(
                options=[AUTO] + all_style_keys(config),
                help="Styles are per meal type — a breakfast style on a dinner row is flagged below.",
            ),
            COL_CUISINE: st.column_config.SelectboxColumn(
                options=[AUTO] + config.get("cuisines", []),
                help="Applies to "
                + ", ".join(config.get("cuisine_meal_types") or meal_types(config)),
            ),
            COL_SOURCE: st.column_config.SelectboxColumn(
                options=source_options,
                help="Which cooked meal this eats the leftovers of.",
            ),
            COL_EXTRA: st.column_config.NumberColumn(
                min_value=0, max_value=12, step=1,
                help="Spare portions to freeze, on top of the meals this cook covers.",
            ),
        },
        key="grid_editor",
    )

    rows = edited.to_dict("records")
    st.session_state["grid_rows"] = rows
    return rows_to_spec(rows, days, servings)


def render_cook_summary(spec: WeekSpec) -> None:
    portions = portions_for(spec)
    claims = eaten_on(spec)
    if not portions:
        return

    rows = []
    for slot in spec.cook_slots():
        claim_ids = claims.get(slot.id, [slot.id])
        last_day = max(claim_ids, key=lambda value: spec.day_index(value.split(":")[0]))
        span = spec.day_index(last_day.split(":")[0]) - spec.day_index(slot.day)
        rows.append(
            {
                "Cook": f"{slot.day} · {slot.meal_type}",
                "Style": humanize(slot.style) or "auto",
                "Cuisine": humanize(slot.cuisine) or "—",
                "Meals covered": len(claim_ids),
                "Portions": portions[slot.id],
                "Eaten over": f"{span + 1} day(s)",
            }
        )

    st.markdown("#### Cooking sessions")
    st.caption(
        f"{len(rows)} cook(s) · {sum(row['Portions'] for row in rows)} portions total. "
        "Portions are derived: meals covered × people per meal, plus any extras."
    )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_window_preview(spec: WeekSpec, shop_days: list) -> list:
    windows = shopping_windows(spec.days, shop_days)
    st.markdown("#### Shopping windows")
    if not shop_days:
        st.caption("No shopping days selected — the whole week becomes one trip.")
    rows = []
    for window in windows:
        cooks = len(
            [slot for slot in spec.cook_slots() if slot.day in window.days]
        )
        rows.append(
            {
                "Trip": window.label,
                "Days": len(window.days),
                "Cooks bought for": cooks,
                "Buy ahead": "yes" if window.shop_ahead else "",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    return windows


# --------------------------------------------------------------------------
# Results tabs
# --------------------------------------------------------------------------


def render_week(week_plan) -> None:
    by_slot = week_plan.by_slot()
    slots_by_day = {}
    for slot in week_plan.slots:
        slots_by_day.setdefault(slot.day, []).append(slot)

    for day in week_plan.days:
        target = week_plan.targets[day]
        totals = day_slot_macros(week_plan, day)

        with st.expander(
            f"**{day}** — {totals['calories']:.0f} / {target['calories']:.0f} kcal",
            expanded=True,
        ):
            macro_cols = st.columns(4)
            for column, (label, key, unit) in zip(
                macro_cols,
                [
                    ("Calories", "calories", "kcal"),
                    ("Protein", "protein_g", "g"),
                    ("Net Carbs", "net_carbs_g", "g"),
                    ("Fat", "fat_g", "g"),
                ],
            ):
                column.metric(
                    f"{label} ({unit})",
                    f"{totals[key]:.0f}",
                    delta=f"{totals[key] - target[key]:+.0f} vs target",
                )

            for slot in slots_by_day.get(day, []):
                if slot.mode == MODE_SKIP:
                    st.markdown(f"**{slot.meal_type.title()}** — _skipped_")
                    continue

                source_id = slot.id if slot.mode == MODE_COOK else slot.source
                event = by_slot.get(source_id)
                if event is None:
                    st.markdown(f"**{slot.meal_type.title()}** — _not generated_")
                    continue

                render_meal(slot, event, is_leftover=slot.mode == MODE_LEFTOVER)


def render_meal(slot, event, is_leftover: bool) -> None:
    with st.container(border=True):
        origin = (
            f" · leftovers from {event.day} {event.meal_type}"
            if is_leftover
            else (f" · cooks {event.portions} portions" if event.portions > 1 else "")
        )
        st.markdown(f"**{slot.meal_type.title()}: {event.recipe.name}**{origin}")

        tags = [humanize(event.style), humanize(event.cuisine)]
        caption = " · ".join([tag for tag in tags if tag])
        st.caption(
            f"{caption + ' · ' if caption else ''}{event.recipe.prep_time_minutes} min prep"
        )

        serving = per_serving_totals(event.recipe)
        nutrition_cols = st.columns(4)
        nutrition_cols[0].metric("Calories", f"{serving['calories']:.0f} kcal")
        nutrition_cols[1].metric("Protein", f"{serving['protein_g']:.0f} g")
        nutrition_cols[2].metric("Net Carbs", f"{serving['net_carbs_g']:.0f} g")
        nutrition_cols[3].metric("Fat", f"{serving['fat_g']:.0f} g")

        if is_leftover:
            st.caption("Reheat only — no shopping or cooking needed today.")
            return

        with st.expander(f"Ingredients (for all {event.portions} portions)"):
            for ingredient in event.recipe.ingredients:
                st.write(
                    f"- {ingredient.name}: "
                    f"{format_quantity(ingredient.name, ingredient.quantity_g)} "
                    f"(NOVA {ingredient.nova_group})"
                )

        with st.expander("Instructions"):
            for step_number, step in enumerate(event.recipe.instructions, start=1):
                st.write(f"{step_number}. {step}")

        if event.recipe.prep_notes:
            st.info(event.recipe.prep_notes)


def render_shopping(week_plan, windows) -> None:
    if not windows:
        st.info("No shopping windows configured.")
        return

    tabs = st.tabs([window.label for window in windows])
    for tab, window in zip(tabs, windows):
        with tab:
            events = week_plan.events_on_days(window.days)
            if not events:
                st.info("Nothing is cooked in this window — no shopping needed.")
                continue

            if window.shop_ahead:
                st.warning(
                    f"You didn't mark {window.days[0]} as a shopping day, so this "
                    "food has to be bought before the week starts."
                )

            with st.expander(f"Cooking this window ({len(events)} sessions)", expanded=False):
                for line in cook_plan_lines(events):
                    st.write(f"- {line}")
                st.caption("Quantities below already include every portion.")

            shopping_list = aggregate_cook_events(events, window.days)

            late_items = [item for item in shopping_list.items() if item.buy_late]
            if late_items:
                st.caption(
                    "⚠️ marks perishables not cooked until later in the window — "
                    "worth a top-up trip closer to the day."
                )

            for department in sorted(shopping_list.categories):
                st.markdown(f"**{department}**")
                for item in shopping_list.categories[department]:
                    flag = " ⚠️" if item.buy_late else ""
                    st.checkbox(
                        f"{item.name} — {format_quantity(item.name, item.total_amount_g)}{flag}",
                        key=f"shop_{window.shop_day}_{department}_{item.name}",
                    )

            st.download_button(
                "Download this list",
                data=format_shopping_list_markdown(
                    shopping_list, cook_events=events, title=window.label
                ),
                file_name=f"shopping_{window.shop_day.lower()}.md",
                mime="text/markdown",
            )

            with st.expander("Copy to Google Keep"):
                st.caption(
                    "Click the copy icon, then paste into a new Google Keep list "
                    "note — each line becomes its own checkbox item."
                )
                st.code(format_shopping_list_keep(shopping_list), language=None)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def run_generation(spec: WeekSpec, config: dict) -> None:
    history = load_history()
    resolved = resolve_auto_choices(spec, config, history)
    cook_days = sorted({slot.day for slot in resolved.cook_slots()}, key=resolved.days.index)

    progress = st.progress(0.0, text="Starting…")
    completed = {"count": 0}
    notes = []

    def report(day: str, cooks: int) -> None:
        if day in cook_days:
            completed["count"] += 1
        fraction = completed["count"] / max(1, len(cook_days))
        progress.progress(
            min(1.0, fraction),
            text=f"{day}: generating {cooks} recipe(s)…" if cooks else f"{day}: leftovers only",
        )

    try:
        week_plan = generate_week_plan(
            resolved, config, history, progress_callback=report, note_callback=notes.append
        )
    except Exception as exc:
        progress.empty()
        st.error(f"Failed to generate the week: {exc}")
        return

    progress.empty()
    record_week_history(week_plan)
    st.session_state["week_plan"] = week_plan
    st.session_state["generation_notes"] = notes


def main():
    config = load_config(CONFIG_PATH)
    sidebar = render_sidebar(config)
    days = sidebar["days"]

    st.title("AI Weekly Meal Planner")

    ensure_grid(config, sidebar["week_start"], sidebar["servings"])

    setup_tab, week_tab, shopping_tab = st.tabs(["Plan Setup", "The Week", "Shopping"])

    with setup_tab:
        day_config = render_targets_editor(config, days)
        day_config["openrouter_model"] = sidebar["model"]

        spec = render_grid(day_config, days, sidebar["servings"])
        render_cook_summary(spec)
        windows = render_window_preview(spec, sidebar["shop_days"])

        errors = validate_week(spec, day_config)
        for error in errors:
            st.error(error)
        for warning in week_warnings(spec):
            st.warning(warning)

        cook_count = len(spec.cook_slots())
        cook_days = len({slot.day for slot in spec.cook_slots()})
        st.caption(
            f"Generating will make {cook_days} API call(s) — one per day with "
            f"cooking to do — for {cook_count} recipes."
        )
        if st.button(
            "Generate Week",
            type="primary",
            use_container_width=True,
            disabled=bool(errors),
        ):
            run_generation(spec, day_config)

        st.session_state["windows"] = windows

    with week_tab:
        if "week_plan" not in st.session_state:
            st.info("Set up your week in **Plan Setup**, then click Generate Week.")
        else:
            failures = st.session_state["week_plan"].failures
            if failures:
                st.warning(
                    "These days failed to generate and are missing from the plan "
                    "and shopping lists. Click Generate Week again to retry — the "
                    "rest of the week is kept in the meantime.\n\n"
                    + "\n".join(f"- **{day}** — {error}" for day, error in failures.items())
                )
            notes = st.session_state.get("generation_notes") or []
            if notes:
                with st.expander(f"Portion adjustments ({len(notes)})"):
                    st.caption(
                        "The model sizes portions poorly, so Python rescaled these "
                        "recipes to land on their macro budget."
                    )
                    for note in notes:
                        st.write(f"- {note}")
            render_week(st.session_state["week_plan"])

    with shopping_tab:
        if "week_plan" not in st.session_state:
            st.info("Generate a week first — shopping lists are built from it.")
        else:
            render_shopping(st.session_state["week_plan"], st.session_state.get("windows", []))


if __name__ == "__main__":
    main()

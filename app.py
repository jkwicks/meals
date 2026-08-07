import copy
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from planner import (
    calculate_daily_targets,
    compute_macro_totals,
    compute_recipe_totals,
    generate_meal_plan,
    load_config,
    load_history,
    pick_cuisine,
    record_history_entry,
    recent_main_proteins,
    resolve_serving_rules,
    scale_recipe,
)
from shopping import (
    aggregate_meal_plan,
    batch_prep_lines,
    format_grams,
    format_shopping_list_keep,
    format_shopping_list_markdown,
)

MIN_NET_CARBS_SLIDER_MAX = 150

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

st.set_page_config(page_title="AI Meal Planner", layout="wide")


def slider_bounds(default_value: float, low_ratio: float, high_ratio: float, min_span: int) -> tuple:
    """Center a slider range on a config default so the current day's value
    always falls inside it, instead of using one fixed range for every day
    (defaults range from a 30g keto-carb day to a 200g high-carb day)."""
    low = max(0, int(default_value * low_ratio))
    high = max(int(default_value * high_ratio), low + min_span)
    return low, high


def render_sidebar(config: dict) -> dict:
    st.sidebar.header("Plan Configuration")

    weekly_schedule = config["weekly_schedule"]
    days = list(weekly_schedule.keys())
    today_name = datetime.now().strftime("%A")
    default_day_index = days.index(today_name) if today_name in days else 0
    day = st.sidebar.selectbox("Day", days, index=default_day_index)
    day_defaults = weekly_schedule[day]

    serving_rules = config.get("serving_rules", {})
    servings = st.sidebar.slider(
        "Servings per meal",
        min_value=1,
        max_value=8,
        value=int(serving_rules.get("servings_per_meal", 2)),
    )

    default_serving_info = resolve_serving_rules(day, config)
    bulk = st.sidebar.checkbox(
        "Batch cook this day (bulk prep)",
        value=default_serving_info["is_batch_day"],
    )
    batch_multiplier_default = min(max(int(serving_rules.get("batch_multiplier", 2)), 2), 4)
    batch_multiplier = st.sidebar.select_slider(
        "Batch multiplier",
        options=[2, 3, 4],
        value=batch_multiplier_default,
        disabled=not bulk,
    )

    st.sidebar.subheader("Macro Targets")
    protein_low, protein_high = slider_bounds(day_defaults["protein_g"], 0.6, 1.4, min_span=40)
    protein_g = st.sidebar.slider(
        "Protein (g)",
        min_value=protein_low,
        max_value=protein_high,
        value=int(day_defaults["protein_g"]),
    )
    carbs_low, carbs_high = slider_bounds(day_defaults["net_carbs_g"], 0.0, 1.6, min_span=40)
    carbs_high = max(carbs_high, MIN_NET_CARBS_SLIDER_MAX)
    net_carbs_g = st.sidebar.slider(
        "Net Carbs (g)",
        min_value=carbs_low,
        max_value=carbs_high,
        value=int(day_defaults["net_carbs_g"]),
    )

    # calculate_daily_targets does the deterministic fat/calorie math — reuse
    # it here instead of re-deriving the formula for the sidebar preview.
    day_config = copy.deepcopy(config)
    day_config["weekly_schedule"][day]["protein_g"] = protein_g
    day_config["weekly_schedule"][day]["net_carbs_g"] = net_carbs_g
    targets = calculate_daily_targets(day, day_config)

    st.sidebar.metric("Fat (g) — computed", f"{targets['fat_g']:.1f}")
    st.sidebar.metric("Total Calories", f"{targets['calories']:.0f} kcal")

    st.sidebar.subheader("Model")
    model = st.sidebar.selectbox("OpenRouter Model", MODEL_OPTIONS, index=0)
    day_config["openrouter_model"] = model

    serving_info = resolve_serving_rules(
        day,
        day_config,
        servings_override=servings,
        bulk_override=bulk,
        batch_multiplier_override=batch_multiplier if bulk else None,
    )

    st.sidebar.subheader("Cuisine")
    history = load_history()
    auto_cuisine = pick_cuisine(day_config, history)
    cuisine_options = ["Auto (rotate)"] + day_config.get("cuisines", [])
    cuisine_choice = st.sidebar.selectbox("Cuisine theme", cuisine_options, index=0)
    cuisine = auto_cuisine if cuisine_choice == "Auto (rotate)" else cuisine_choice
    if auto_cuisine:
        st.sidebar.caption(f"Auto-picked: {auto_cuisine.replace('_', ' ')}")
    avoid_proteins = recent_main_proteins(history)

    return {
        "day": day,
        "config": day_config,
        "targets": targets,
        "serving_info": serving_info,
        "cuisine": cuisine,
        "avoid_proteins": avoid_proteins,
    }


def render_nutrition_summary(targets: dict, per_serving_totals: dict, batch_totals: dict, serving_info: dict) -> None:
    st.subheader("Nutrition Summary — Per Serving")
    macro_fields = [
        ("Calories", "calories", "kcal"),
        ("Protein", "protein_g", "g"),
        ("Net Carbs", "net_carbs_g", "g"),
        ("Fat", "fat_g", "g"),
    ]
    columns = st.columns(4)
    for column, (label, key, unit) in zip(columns, macro_fields):
        generated = per_serving_totals[key]
        target = targets[key]
        column.metric(
            f"{label} ({unit})",
            f"{generated:.1f}",
            delta=f"{generated - target:+.1f} vs target",
        )

    if serving_info["is_batch_day"]:
        st.caption(
            f"Total batch yield ({serving_info['servings_per_meal']} servings/meal "
            f"x {serving_info['batch_multiplier']} batch multiplier): "
            f"{batch_totals['calories']:.0f} kcal, {batch_totals['protein_g']:.0f}g protein, "
            f"{batch_totals['net_carbs_g']:.0f}g net carbs, {batch_totals['fat_g']:.0f}g fat."
        )


def render_recipe_cards(meal_plan) -> None:
    st.subheader("Recipes")
    for recipe in meal_plan.recipes:
        with st.container(border=True):
            batch_badge = " · Batch Prep" if recipe.is_batch_prep else ""
            st.markdown(f"**{recipe.name}** — {recipe.meal_type.title()}{batch_badge}")
            st.caption(f"{recipe.servings} servings · {recipe.prep_time_minutes} min prep")

            # Recipe.ingredients hold the scaled batch total, so divide back
            # down by servings to show what one serving of this meal has.
            recipe_totals = compute_recipe_totals(recipe)
            per_serving = {key: value / recipe.servings for key, value in recipe_totals.items()}
            st.caption("Nutrition per serving")
            nutrition_cols = st.columns(4)
            nutrition_cols[0].metric("Calories", f"{per_serving['calories']:.0f} kcal")
            nutrition_cols[1].metric("Protein", f"{per_serving['protein_g']:.0f} g")
            nutrition_cols[2].metric("Net Carbs", f"{per_serving['net_carbs_g']:.0f} g")
            nutrition_cols[3].metric("Fat", f"{per_serving['fat_g']:.0f} g")

            with st.expander("Ingredients"):
                for ingredient in recipe.ingredients:
                    st.write(f"- {ingredient.name}: {ingredient.quantity_g:g}g (NOVA {ingredient.nova_group})")

            with st.expander("Instructions"):
                for step_number, step in enumerate(recipe.instructions, start=1):
                    st.write(f"{step_number}. {step}")

            if recipe.prep_notes:
                st.info(recipe.prep_notes)


def render_shopping_list(meal_plan) -> None:
    shopping_list = aggregate_meal_plan(meal_plan)

    batch_lines = batch_prep_lines(meal_plan)
    if batch_lines:
        st.info("Batch prep included (already counted below):\n" + "\n".join(f"- {line}" for line in batch_lines))

    for department in sorted(shopping_list.categories):
        st.markdown(f"**{department}**")
        for item in shopping_list.categories[department]:
            st.checkbox(
                f"{item.name} — {format_grams(item.total_amount_g)}",
                key=f"shop_{department}_{item.name}",
            )

    st.download_button(
        "Download shopping_list.md",
        data=format_shopping_list_markdown(shopping_list, meal_plan=meal_plan),
        file_name="shopping_list.md",
        mime="text/markdown",
    )

    st.subheader("Copy to Google Keep")
    st.caption(
        "Click the copy icon, then paste into a new Google Keep list note — "
        "each line becomes its own checkbox item."
    )
    st.code(format_shopping_list_keep(shopping_list), language=None)


def main():
    config = load_config(CONFIG_PATH)
    sidebar_state = render_sidebar(config)

    st.title("AI Meal Planner")

    generate_clicked = st.button("Generate Meal Plan", type="primary", use_container_width=True)

    if generate_clicked:
        model = sidebar_state["config"]["openrouter_model"]
        with st.spinner(f"Generating meal plan for {sidebar_state['day']} using {model}..."):
            try:
                meal_plan = generate_meal_plan(
                    sidebar_state["targets"],
                    sidebar_state["config"],
                    sidebar_state["serving_info"],
                    sidebar_state["cuisine"],
                    sidebar_state["avoid_proteins"],
                )
            except Exception as exc:
                st.error(f"Failed to generate meal plan: {exc}")
                meal_plan = None

        if meal_plan is not None:
            record_history_entry(meal_plan, sidebar_state["cuisine"])
            per_serving_totals = compute_macro_totals(meal_plan)
            meal_plan.recipes = [
                scale_recipe(
                    recipe,
                    sidebar_state["serving_info"]["servings_per_meal"],
                    sidebar_state["serving_info"]["batch_multiplier"],
                    sidebar_state["serving_info"]["is_batch_day"],
                )
                for recipe in meal_plan.recipes
            ]
            st.session_state["meal_plan"] = meal_plan
            st.session_state["targets"] = sidebar_state["targets"]
            st.session_state["per_serving_totals"] = per_serving_totals
            st.session_state["batch_totals"] = compute_macro_totals(meal_plan)
            st.session_state["serving_info"] = sidebar_state["serving_info"]

    if "meal_plan" not in st.session_state:
        st.info("Configure your plan in the sidebar, then click Generate Meal Plan.")
        return

    plan_tab, shopping_tab = st.tabs(["Meal Plan", "Shopping List"])

    with plan_tab:
        render_nutrition_summary(
            st.session_state["targets"],
            st.session_state["per_serving_totals"],
            st.session_state["batch_totals"],
            st.session_state["serving_info"],
        )
        render_recipe_cards(st.session_state["meal_plan"])

    with shopping_tab:
        render_shopping_list(st.session_state["meal_plan"])


if __name__ == "__main__":
    main()

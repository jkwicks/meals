"""The full-screen catalog browser — a maximized dialog for reviewing every
recipe ever favorited or imported (`data/recipes_master.json`), not just the
7-row search box the drawer's "Recipe Catalog" section has room for.

`build_catalog_browser(ctx, cards, rename_dialog)` needs `cards` (see
`ui_cards`) so a clicked recipe opens the same read-only detail dialog every
other card in the app shares — this module never builds its own — and
`rename_dialog` (see `ui_catalog`) so its edit icon opens the one dialog every
catalog row anywhere in the app shares. `favorite`/`delete` are plain
awaitable helpers from `ui_catalog`, needing no dialog of their own.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from nicegui import ui

from planner import Recipe
from ui_catalog import RenameDialogHandles, delete_recipe, toggle_favorite
from ui_cards import CardHandles
from ui_context import UIContext
from ui_state import SlotView
from ui_theme import (
    MACRO_LABELS,
    MACRO_TINTS,
    RADIUS_PANEL,
    RADIUS_PILL,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    STATUS_COOK,
    TEXT_BODY,
    TEXT_DISPLAY,
    TEXT_HEAD,
    TEXT_MICRO,
)
from week import MODE_COOK


@dataclass
class CatalogBrowserHandles:
    open: Callable
    catalog_grid: Callable


def _detail_view(entry: dict) -> Optional[SlotView]:
    """A catalog entry, reshaped into the same `SlotView` the grid's own
    cards render from — `ui_cards.recipe_detail` reads a `SlotView`, not a
    raw recipe dict, and building a second detail renderer here would be a
    second place for the two to disagree about how a recipe reads. There is
    no day/meal-type slot behind a catalog entry, so those fields are left at
    their defaults; `status=STATUS_COOK` is the closest of the four statuses
    to "a real, cookable recipe", which is all a catalog entry ever claims to
    be. Returns None for a stored recipe that no longer validates (a manual
    edit to the JSON, say), so a bad entry can still be deleted from the
    browser without crashing it on click.
    """
    try:
        recipe = Recipe.model_validate(entry["recipe"])
    except Exception:
        return None
    return SlotView(
        day="",
        meal_type=recipe.meal_type,
        status=STATUS_COOK,
        title=recipe.name,
        mode=MODE_COOK,
        portions=recipe.servings,
        prep_minutes=recipe.prep_time_minutes,
        macros=recipe.per_serving_macros,
        recipe=recipe,
    )


def _matches(entry: dict, search: str, meal_type: str, favorites_only: bool) -> bool:
    if favorites_only and not entry.get("is_favorite"):
        return False
    recipe = entry["recipe"]
    if meal_type != "All" and recipe.get("meal_type") != meal_type:
        return False
    if search and search not in recipe.get("name", "").lower():
        return False
    return True


def build_catalog_browser(
    ctx: UIContext, cards: CardHandles, rename_dialog: RenameDialogHandles
) -> CatalogBrowserHandles:
    state = ctx.state
    refreshables = ctx.refreshables

    def on_search(event) -> None:
        state.catalog_browser_search = (event.value or "").strip()
        refreshables.refresh("catalog_browser")

    def on_meal_type(event) -> None:
        state.catalog_browser_meal_type = event.value or "All"
        refreshables.refresh("catalog_browser")

    def on_favorites_only(event) -> None:
        state.catalog_browser_favorites_only = bool(event.value)
        refreshables.refresh("catalog_browser")

    def open_detail(entry: dict) -> None:
        view = _detail_view(entry)
        if view is None:
            ui.notify("This recipe can't be displayed — try deleting it.", type="warning")
            return
        cards.open_detail(view)

    def catalog_card(entry: dict) -> None:
        recipe = entry["recipe"]
        favorited = bool(entry.get("is_favorite"))
        detail_view = _detail_view(entry)
        macros = detail_view.macros if detail_view else None

        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_BASE} {RADIUS_PANEL} border border-slate-800 bg-slate-900/60 "
            "hover:border-slate-600 transition-colors min-w-0"
        ):
            with ui.element("div").classes(f"flex flex-row items-start justify-between gap-{SPACE_TIGHT}"):
                title = ui.label(recipe.get("name", "")).classes(
                    f"{TEXT_BODY} font-semibold leading-tight line-clamp-2 min-w-0 "
                    + ("cursor-pointer hover:text-sky-300" if detail_view else "text-slate-500")
                )
                if detail_view:
                    title.on("click", lambda e=entry: open_detail(e))
                with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_HAIR} shrink-0"):
                    fav_button = ui.button(
                        icon="bookmark" if favorited else "bookmark_border",
                        on_click=lambda r=recipe: toggle_favorite(ctx, r),
                    ).props("dense flat round size=xs")
                    fav_button.classes(
                        f"min-h-0 p-{SPACE_HAIR} "
                        + ("text-amber-300" if favorited else "text-slate-500 hover:text-amber-300")
                    )
                    ui.button(
                        icon="edit", on_click=lambda e=entry: rename_dialog.open(e)
                    ).props("dense flat round size=xs").classes(
                        f"min-h-0 p-{SPACE_HAIR} text-slate-500 hover:text-sky-300"
                    )
                    ui.button(
                        icon="delete", on_click=lambda rid=entry["id"]: delete_recipe(ctx, rid)
                    ).props("dense flat round size=xs").classes(
                        f"min-h-0 p-{SPACE_HAIR} text-slate-500 hover:text-rose-300"
                    )

            tags = " · ".join(
                part
                for part in [
                    recipe.get("meal_type", "").title(),
                    "Long cook" if recipe.get("long_oven_cook") else "",
                    "Bulk prep" if recipe.get("bulk_prep_friendly") else "",
                ]
                if part
            )
            if tags:
                ui.label(tags).classes(f"{TEXT_MICRO} text-slate-500")

            if macros:
                with ui.element("div").classes(
                    f"flex flex-row flex-wrap items-center gap-x-1 mt-0.5 px-{SPACE_TIGHT} py-{SPACE_HAIR} "
                    f"{RADIUS_PILL} bg-slate-950/40 w-fit max-w-full"
                ):
                    ui.label(f"{macros['calories']:.0f} kcal").classes(
                        f"{TEXT_MICRO} font-mono text-slate-300"
                    )
                    for key, short, unit in MACRO_LABELS[1:]:
                        ui.label("·").classes(f"{TEXT_MICRO} text-slate-600")
                        ui.label(f"{macros[key]:.0f}{unit} {short}").classes(
                            f"{TEXT_MICRO} font-mono {MACRO_TINTS[key]}"
                        )

    @ui.refreshable
    def catalog_grid() -> None:
        matches = [
            entry
            for entry in state.recipe_catalog
            if _matches(
                entry,
                state.catalog_browser_search.lower(),
                state.catalog_browser_meal_type,
                state.catalog_browser_favorites_only,
            )
        ]
        # Favorites first, then grouped by meal type — the shape a "make sure
        # favorites are right" pass actually wants: every favorite together,
        # rather than interleaved alphabetically with everything else.
        matches.sort(
            key=lambda e: (
                not e.get("is_favorite"),
                e["recipe"].get("meal_type", ""),
                e["recipe"].get("name", "").lower(),
            )
        )

        ui.label(f"{len(matches)} of {len(state.recipe_catalog)} recipes").classes(
            f"{TEXT_BODY} text-slate-500 mb-2"
        )

        if not state.recipe_catalog:
            ui.label("Catalog is empty — bookmark a cooked meal or import one.").classes(
                f"{TEXT_HEAD} text-slate-500 italic"
            )
            return
        if not matches:
            ui.label("No recipes match these filters.").classes(
                f"{TEXT_HEAD} text-slate-500 italic"
            )
            return

        with ui.element("div").classes(
            f"grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-{SPACE_BASE}"
        ):
            for entry in matches:
                catalog_card(entry)

    # `maximized` is Quasar's own full-screen dialog mode — this is a browse
    # surface, not a modal form, so it gets the whole viewport rather than a
    # fixed-width card.
    with ui.dialog().props("maximized") as dialog:
        with ui.element("div").classes(
            f"bg-slate-950 h-full w-full flex flex-col p-{SPACE_PAGE} gap-{SPACE_SECTION}"
        ):
            with ui.element("div").classes(f"flex flex-row items-center justify-between gap-{SPACE_SECTION}"):
                with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_BASE}"):
                    ui.icon("menu_book").classes("text-slate-300")
                    ui.label("Recipe Catalog").classes(
                        f"{TEXT_DISPLAY} font-semibold text-slate-100"
                    )
                ui.button(icon="close", on_click=dialog.close).props("dense flat round")

            with ui.row().classes(f"w-full items-center flex-nowrap gap-{SPACE_BASE}"):
                ui.input(placeholder="Search recipes…", on_change=on_search).props(
                    "dense outlined clearable"
                ).classes(f"flex-1 {TEXT_HEAD}")
                ui.select(
                    ["All"] + state.meal_types, value="All", on_change=on_meal_type
                ).props("dense outlined").classes(f"w-40 {TEXT_HEAD}")
                ui.checkbox("Favorites only", on_change=on_favorites_only).classes(
                    f"{TEXT_HEAD} text-slate-300"
                )

            with ui.element("div").classes("flex-1 overflow-y-auto"):
                catalog_grid()

    return CatalogBrowserHandles(open=dialog.open, catalog_grid=catalog_grid)

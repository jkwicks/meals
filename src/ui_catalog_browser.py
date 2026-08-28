"""The Library destination: the recipe catalog (`data/recipes_master.json`),
its filters, and recipe import. Used to be a `ui.dialog().props("maximized")`
opened from the drawer's "Browse all" button — phase 3 of `ui-redesign.md`
promotes it to a rail destination, so the wrapper dialog and its close button
are gone; the content below is otherwise the surface that already existed.

`build_catalog_browser(ctx, cards, rename_dialog)` needs `cards` (see
`ui_cards`) so a clicked recipe opens the same read-only detail dialog every
other card in the app shares — this module never builds its own — and
`rename_dialog` (see `ui_catalog`) so its edit icon opens the one dialog every
catalog row anywhere in the app shares. `favorite`/`delete` are plain
awaitable helpers from `ui_catalog`, needing no dialog of their own.

Import is the one recipe-intake path this app's UI currently offers — paste
raw text, an ingredient list, or a URL into one dialog, parsed through
`import_external_recipe` under the same dietary rules generation uses. It
lives here now rather than in the deleted drawer, since Library is where
`ui-redesign.md`'s target shape puts "all import paths."
"""

from dataclasses import dataclass
from typing import Callable, Optional

from nicegui import ui

from planner import Recipe, api_key_error, import_external_recipe, short_error
from repository import catalog_matches
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
    panel: Callable
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


def build_catalog_browser(
    ctx: UIContext, cards: CardHandles, rename_dialog: RenameDialogHandles
) -> CatalogBrowserHandles:
    state = ctx.state
    REPOSITORY = ctx.repository
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
            # The icon row is a *sibling* of the clickable body below, never a
            # parent of it — the same split `ui_cards.meal_card` and
            # `prep_candidate_card` make, and for the same reason: a click on
            # favorite/edit/delete would otherwise bubble into the body's
            # handler and open the recipe dialog on its way past. Phase 6d of
            # `ui-redesign.md`; before it, only the title carried the handler,
            # so this was the one card in the app whose body did nothing.
            #
            # The title moved *out* of this row and down into that body, which
            # is what makes the split possible at all — and it fixes a second
            # thing measured on the running page at 1440px rather than read
            # out of the source: this row is subject to the standing Quasar
            # `.flex` wrap trap, and with the title still in it, any name whose
            # max-content ran past roughly 200px pushed all three icons onto a
            # line of their own. Wrapping is decided from the items'
            # *unshrunk* widths, so the title's `min-w-0` never got a chance to
            # prevent it. Half the catalog rendered icons-beside-title and half
            # icons-under-title, on nothing more principled than name length.
            # `flex-nowrap` keeps it that way now that the row is icons only.
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center justify-end gap-{SPACE_HAIR}"
            ):
                fav_button = ui.button(
                    icon="bookmark" if favorited else "bookmark_border",
                    on_click=lambda r=recipe: toggle_favorite(ctx, r),
                ).props("dense flat round size=xs")
                fav_button.classes(
                    f"min-h-0 p-{SPACE_HAIR} "
                    + ("text-slate-200" if favorited else "text-slate-500 hover:text-slate-300")
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

            # `hover:text-sky-300` sits on the body rather than on the title,
            # so hovering anywhere in the clickable region tints the one thing
            # a click acts on. It reaches the title by inheritance — that label
            # is the only descendant here with no colour of its own; the tag
            # line and every macro figure set theirs and are left alone.
            body = ui.element("div").classes(
                f"flex flex-col gap-{SPACE_TIGHT} min-w-0 "
                + ("cursor-pointer hover:text-sky-300" if detail_view else "")
            )
            if detail_view:
                body.on("click", lambda e=entry: open_detail(e))

            with body:
                ui.label(recipe.get("name", "")).classes(
                    f"{TEXT_BODY} font-semibold leading-tight line-clamp-2 min-w-0 "
                    + ("" if detail_view else "text-slate-500")
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
            # `repository.catalog_matches` — the same function `/api/recipes`
            # filters with. This was a private `_matches` here and four
            # inline lines there, already disagreeing about whether "no meal
            # type" is spelled "All" or None; the shared helper owns both
            # spellings. Closed in v0.31.0 — see CHANGE-QUEUE.md's
            # "Verified closed" table.
            if catalog_matches(
                entry,
                favorites_only=state.catalog_browser_favorites_only,
                meal_type=state.catalog_browser_meal_type,
                search=state.catalog_browser_search,
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

    # ---- import ---------------------------------------------------------

    async def on_import() -> None:
        text = (state.import_text or "").strip()
        if not text:
            ui.notify("Paste some recipe text first.", type="warning")
            return
        key_error = api_key_error()
        if key_error:
            ui.notify(key_error, type="negative", close_button=True, timeout=0)
            return

        import_button.props("loading")
        try:
            recipe = await import_external_recipe(
                text, config=state.planning_config(), repository=REPOSITORY
            )
        except Exception as exc:
            ui.notify(
                f"Import failed: {short_error(exc)}",
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return
        finally:
            import_button.props(remove="loading")

        favorite = state.import_as_favorite
        await REPOSITORY.import_recipe(recipe.model_dump(), favorite=favorite)
        state.recipe_catalog = await REPOSITORY.load_recipe_catalog()
        refreshables.refresh("favorites")
        state.import_text = ""
        state.import_as_favorite = False
        import_dialog.close()
        ui.notify(
            f"Imported \"{recipe.name}\"" + (" and favorited it." if favorite else "."),
            type="positive",
        )

    with ui.dialog() as import_dialog:
        with ui.element("div").classes(
            f"bg-slate-900 {RADIUS_PANEL} p-{SPACE_PAGE} w-[32rem] max-w-full flex flex-col gap-{SPACE_BASE}"
        ):
            ui.label("Import a recipe").classes(f"{TEXT_HEAD} font-semibold")
            ui.label(
                "Paste raw recipe text, an ingredient list, or a URL — it's turned "
                "into grams, macros and NOVA groups under the same dietary rules "
                "generation uses."
            ).classes(f"{TEXT_MICRO} text-slate-500")
            ui.textarea(placeholder="Paste recipe text or a URL…").bind_value(
                state, "import_text"
            ).props("dense outlined").classes(f"w-full {TEXT_BODY}").style(
                "min-height: 8rem"
            )
            ui.checkbox("Mark as favorite").bind_value(state, "import_as_favorite").classes(
                TEXT_BODY
            )
            with ui.row().classes(f"justify-end gap-{SPACE_BASE} mt-2"):
                ui.button("Cancel", on_click=import_dialog.close).props(
                    "dense flat no-caps"
                )
                import_button = ui.button(
                    "Analyze & Import", icon="auto_awesome", on_click=on_import
                ).props("dense no-caps")

    def panel() -> None:
        with ui.element("div").classes(f"flex flex-col p-{SPACE_PAGE} gap-{SPACE_SECTION}"):
            with ui.element("div").classes(f"flex flex-row items-center justify-between gap-{SPACE_SECTION}"):
                with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_BASE}"):
                    ui.icon("menu_book").classes("text-slate-300")
                    ui.label("Library").classes(f"{TEXT_DISPLAY} font-semibold text-slate-100")
                ui.button(
                    "Import recipe", icon="upload_file", on_click=import_dialog.open
                ).props("dense flat no-caps").classes("text-slate-300")

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

            catalog_grid()

    return CatalogBrowserHandles(panel=panel, catalog_grid=catalog_grid)

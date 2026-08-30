"""The Library destination: the recipe catalog (`data/recipes_master.json`),
its filters, and recipe import. Used to be a `ui.dialog().props("maximized")`
opened from the drawer's "Browse all" button — phase 3 of `ui-redesign.md`
promotes it to a rail destination, so the wrapper dialog and its close button
are gone.

**It is a table, not a card grid.** The grid showed a name, a tag line and a
macro pill per card, four across, and answered "what is in my catalog" while
being unable to answer any question that compares two recipes — which of these
is the cheap one, which yields six servings rather than one, which have I not
cooked in a month. Those are column questions: a figure is only comparable when
it sits in the same place on every row, and a 4-across grid puts every figure
somewhere different. The table adds servings and a last-cooked date (neither
was visible anywhere in the app before) and makes every column sortable, which
is the affordance that turns "what is in my catalog" into "what should I cook".

`build_catalog_browser(ctx, cards, rename_dialog)` needs `cards` (see
`ui_cards`) so a clicked recipe opens the same read-only detail dialog every
other card in the app shares — this module never builds its own — and
`rename_dialog` (see `ui_catalog`) so its edit icon opens the one dialog every
catalog row anywhere in the app shares. `favorite`/`delete` are plain
awaitable helpers from `ui_catalog`, needing no dialog of their own.

Every column's *value* is computed in `ui_state` (`build_catalog_rows`,
`sort_catalog_rows`, `PlannerState.catalog_rows`) and this module only places
it — the standing rule that logic worth testing lives in the one UI module
with tests. A widget here that derived a figure of its own would be a column
nothing could check.

Import is the one recipe-intake path this app's UI currently offers — paste
raw text, an ingredient list, or a URL into one dialog, parsed through
`import_external_recipe` under the same dietary rules generation uses. It
lives here now rather than in the deleted drawer, since Library is where
`ui-redesign.md`'s target shape puts "all import paths."
"""

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from nicegui import ui

from planner import api_key_error, import_external_recipe, short_error
from ui_catalog import RenameDialogHandles, delete_recipe, toggle_favorite
from ui_cards import CardHandles
from ui_context import UIContext
from ui_state import CatalogRow, catalog_history_window
from ui_theme import (
    CATALOG_ROW_LEAD,
    CATALOG_ROW_TAIL,
    CATALOG_TABLE_COLS,
    CATALOG_TABLE_MIN_W,
    MACRO_DETAIL_LABELS,
    MACRO_TINTS,
    RADIUS_CARD,
    RADIUS_PANEL,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    SURFACE_INSET,
    TEXT_BODY,
    TEXT_DISPLAY,
    TEXT_HEAD,
    TEXT_MICRO,
)


@dataclass
class CatalogBrowserHandles:
    panel: Callable
    catalog_grid: Callable


# The recipe flags that survived the move from card to row. They were a
# comma-joined tag line under the card title; a table row is one line high, so
# they are glyphs with tooltips instead. Both are slate — the palette contract
# has no hue left that wouldn't collide, and `TRAINING_TYPE_ICONS` is the
# precedent for letting the glyph carry the distinction rather than a colour.
RECIPE_FLAG_ICONS = [
    ("long_oven_cook", "hourglass_bottom", "Long oven cook"),
    ("bulk_prep_friendly", "inventory_2", "Bulk-prep friendly"),
]


def _window_note(history_start: Optional[str]) -> str:
    """What a dash in the Last eaten column means, in words.

    `meal_history.json` retains `history_max_entries` (28) cooked days, so a
    recipe missing from it was either never cooked or last cooked before the
    window opened — nothing stored can tell those apart, and an em dash left
    unexplained reads as the first. Naming the window's own first date is the
    honest form: the column is answering "since <date>", not "ever".
    """
    if not history_start:
        return "No cooked history recorded yet — Last eaten is blank for every recipe."
    try:
        opened = date.fromisoformat(history_start)
    except ValueError:
        return "Last eaten reads the retained cooking history; a dash means not cooked in it."
    return (
        f"Last eaten reads the retained cooking history, which starts "
        f"{opened.day} {opened:%b %Y} — a dash means not cooked since then."
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

    def on_sort(column: str) -> None:
        state.sort_catalog_by(column)
        refreshables.refresh("catalog_browser")

    def open_detail(row: CatalogRow) -> None:
        if row.view is None:
            ui.notify("This recipe can't be displayed — try deleting it.", type="warning")
            return
        cards.open_detail(row.view)

    # ---- the table ------------------------------------------------------

    def column_header(
        label: str, column: str, right: bool = False, tint: str = "", icon: str = ""
    ) -> None:
        """One sortable header cell.

        Sort state is carried by the caret alone, never by colour: a macro
        header keeps its `MACRO_TINTS` hue whether or not the table is sorted
        by it, because that hue says *which macro* and would mean two things
        at once if it also said "sorted". Same rule the palette contract
        draws everywhere else — icon distinguishes, colour identifies.

        `icon` is the favourite column, whose header is the same `bookmark`
        glyph its rows toggle rather than a star: the filled/outline bookmark
        pair is how this app has always said "favourite" (a star was one of
        the amber meanings that pass removed), and a header drawn in a
        different vocabulary from the column under it is a second name for
        one thing.
        """
        active = state.catalog_browser_sort == column
        cell = ui.element("div").classes(
            f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR} min-w-0 cursor-pointer "
            f"select-none {'justify-end' if right else ''} "
            + (tint or ("text-slate-300" if active else "text-slate-400 hover:text-slate-300"))
        )
        cell.on("click", lambda c=column: on_sort(c))
        with cell:
            if icon:
                ui.icon(icon).classes(f"{TEXT_MICRO} shrink-0").tooltip(label)
            else:
                ui.label(label).classes(
                    f"{TEXT_MICRO} uppercase tracking-wide truncate "
                    + ("font-semibold" if active else "font-medium")
                )
            if active:
                ui.icon(
                    "arrow_downward" if state.catalog_browser_sort_desc else "arrow_upward"
                ).classes(f"{TEXT_MICRO} shrink-0")

    def header_row() -> None:
        # Deliberately **not** `sticky top-0`. The page's only vertical scroll
        # container is the document itself, and `ui.header()` is
        # `position: fixed` over the top of it — so a sticky header row would
        # pin itself to the viewport's top edge and sit *underneath* the seven
        # macro bars, which is worse than no sticky header at all. The one
        # `sticky` this app does use (`ui_cards`' meal-type gutter) is
        # horizontal, inside a scroll region it can actually position against.
        # Making this one stick would mean giving the table its own bounded
        # scroll box, which is a real change to how the destination is laid
        # out rather than a class on this row.
        with ui.element("div").classes(
            f"flex flex-row flex-nowrap items-center gap-{SPACE_BASE} px-{SPACE_BASE} pb-{SPACE_TIGHT} "
            "border-b border-slate-700"
        ):
            with ui.element("div").classes(
                f"{CATALOG_ROW_LEAD} shrink-0 flex flex-row justify-center"
            ):
                column_header("Favourites", "favorite", icon="bookmark")
            with ui.element("div").classes(
                f"grid {CATALOG_TABLE_COLS} items-center gap-{SPACE_BASE} flex-1 min-w-0"
            ):
                column_header("Recipe", "name")
                column_header("Meal", "meal_type")
                column_header("Serves", "servings", right=True)
                column_header("kcal", "calories", right=True)
                for key, label, _unit in MACRO_DETAIL_LABELS[1:4]:
                    column_header(label, key, right=True, tint=MACRO_TINTS[key])
                column_header("Last eaten", "last_eaten", right=True)
            ui.element("div").classes(f"{CATALOG_ROW_TAIL} shrink-0")

    def catalog_row(row: CatalogRow) -> None:
        with ui.element("div").classes(
            f"flex flex-row flex-nowrap items-center gap-{SPACE_BASE} px-{SPACE_BASE} py-{SPACE_TIGHT} "
            f"{RADIUS_CARD} border-b border-slate-800/70 hover:{SURFACE_INSET} transition-colors"
        ):
            # The favourite toggle and the edit/delete pair are siblings of
            # the clickable body between them, never inside it — the same
            # split the card this row replaced had to make, and for the same
            # reason: nested, a click on any of the three would bubble into
            # the body's handler and open the recipe dialog on its way past.
            with ui.element("div").classes(
                f"{CATALOG_ROW_LEAD} shrink-0 flex flex-row justify-center"
            ):
                fav_button = ui.button(
                    icon="bookmark" if row.is_favorite else "bookmark_border",
                    on_click=lambda r=row.entry["recipe"]: toggle_favorite(ctx, r),
                ).props("dense flat round size=xs")
                fav_button.classes(
                    f"min-h-0 p-{SPACE_HAIR} "
                    + ("text-slate-200" if row.is_favorite else "text-slate-400 hover:text-slate-300")
                )

            body = ui.element("div").classes(
                f"grid {CATALOG_TABLE_COLS} items-center gap-{SPACE_BASE} flex-1 min-w-0 "
                + ("cursor-pointer hover:text-sky-300" if row.readable else "")
            )
            if row.readable:
                body.on("click", lambda r=row: open_detail(r))

            with body:
                # `hover:text-sky-300` sits on the body rather than on the
                # name, so hovering anywhere in the clickable region tints the
                # one thing a click acts on. It reaches the name by
                # inheritance — that label is the only cell here with no
                # colour of its own; every figure sets its own and is left
                # alone.
                with ui.element("div").classes(
                    f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR} min-w-0"
                ):
                    ui.label(row.name).classes(
                        f"{TEXT_BODY} font-medium truncate min-w-0 "
                        + ("" if row.readable else "text-slate-400 italic")
                    )
                    for flag, icon, tooltip in RECIPE_FLAG_ICONS:
                        if row.entry.get("recipe", {}).get(flag):
                            ui.icon(icon).classes(
                                f"{TEXT_MICRO} shrink-0 text-slate-400"
                            ).tooltip(tooltip)

                ui.label(row.meal_type.title()).classes(f"{TEXT_MICRO} text-slate-400 truncate")
                ui.label(str(row.servings)).classes(
                    f"{TEXT_MICRO} font-mono text-slate-400 text-right"
                )

                # Figures stay one colour down the whole column — the digits
                # are what a reader compares between rows, so the tint that
                # says *which macro* sits on the header letter instead. Same
                # rule `MACRO_TINTS`' own comment states for the card strip.
                if row.macros:
                    ui.label(f"{row.macros['calories']:.0f}").classes(
                        f"{TEXT_MICRO} font-mono text-slate-200 text-right"
                    )
                    for key, _label, _unit in MACRO_DETAIL_LABELS[1:4]:
                        ui.label(f"{row.macros[key]:.0f}").classes(
                            f"{TEXT_MICRO} font-mono text-slate-300 text-right"
                        )
                else:
                    for _ in range(4):
                        ui.label("—").classes(f"{TEXT_MICRO} font-mono text-slate-400 text-right")

                ui.label(row.last_eaten_label).classes(
                    f"{TEXT_MICRO} font-mono text-right "
                    + ("text-slate-400" if row.last_eaten else "text-slate-400")
                )

            with ui.element("div").classes(
                f"{CATALOG_ROW_TAIL} shrink-0 flex flex-row flex-nowrap justify-end gap-{SPACE_HAIR}"
            ):
                ui.button(
                    icon="edit", on_click=lambda e=row.entry: rename_dialog.open(e)
                ).props("dense flat round size=xs").classes(
                    f"min-h-0 p-{SPACE_HAIR} text-slate-400 hover:text-sky-300"
                )
                ui.button(
                    icon="delete", on_click=lambda rid=row.id: delete_recipe(ctx, rid)
                ).props("dense flat round size=xs").classes(
                    f"min-h-0 p-{SPACE_HAIR} text-slate-400 hover:text-rose-300"
                )

    @ui.refreshable
    def catalog_grid() -> None:
        rows = state.catalog_rows()

        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_HAIR} mb-{SPACE_BASE}"
        ):
            ui.label(f"{len(rows)} of {len(state.recipe_catalog)} recipes").classes(
                f"{TEXT_BODY} text-slate-400"
            )
            ui.label(_window_note(catalog_history_window(state.history))).classes(
                f"{TEXT_MICRO} text-slate-400"
            )

        if not state.recipe_catalog:
            ui.label("Catalog is empty — bookmark a cooked meal or import one.").classes(
                f"{TEXT_HEAD} text-slate-400 italic"
            )
            return
        if not rows:
            ui.label("No recipes match these filters.").classes(
                f"{TEXT_HEAD} text-slate-400 italic"
            )
            return

        # `min-w-0` on the scroll wrapper, or it grows to the table's own
        # min-content width regardless of viewport and a Quasar container
        # further up scrolls the whole panel instead — the trap
        # `ui_plan.panel()` hit, documented in the `ui-work` skill.
        with ui.element("div").classes("w-full min-w-0 overflow-x-auto"):
            with ui.element("div").classes(
                f"flex flex-col flex-nowrap {CATALOG_TABLE_MIN_W}"
            ):
                header_row()
                for row in rows:
                    catalog_row(row)

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
            ).classes(f"{TEXT_MICRO} text-slate-400")
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

"""Favorites/catalog helpers shared by `ui_cards` (the bookmark button on a
cooked card), `ui_drawer` (the catalog list's bookmark/edit/delete row) and
`ui_catalog_browser` (the full-screen catalog dialog, which offers the same
three actions per recipe).

These read `state.recipe_catalog` (the in-memory copy loaded at startup)
rather than awaiting the repository, deliberately: `ui_cards.canvas()` calls
`is_favorited` once per cooked card on every repaint, and turning that into
a disk read per card would make a repaint O(cards) file opens. Every handler
that mutates the catalog refreshes the "catalog"/"favorites" topics, so it
stays in sync — do not "fix" these into async repository calls.

`build_rename_dialog` lives here rather than in `ui_drawer` for the same
reason `toggle_favorite` does: two surfaces now need to rename a catalog
entry, and one dialog keyed off `state.edit_catalog_id` (the same pattern
`ui_cards.recipe_detail` uses for `state.focus`) is what lets both open it
without either owning it.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from nicegui import ui

from repository import recipe_content_key
from ui_context import UIContext


def catalog_entry_for(ctx: UIContext, recipe: dict) -> Optional[dict]:
    key = recipe_content_key(recipe)
    return next(
        (r for r in ctx.state.recipe_catalog if r.get("content_key") == key), None
    )


def is_favorited(ctx: UIContext, recipe: dict) -> bool:
    entry = catalog_entry_for(ctx, recipe)
    return bool(entry and entry.get("is_favorite"))


def favorited_catalog(ctx: UIContext) -> list:
    return [r for r in ctx.state.recipe_catalog if r.get("is_favorite")]


async def toggle_favorite(ctx: UIContext, recipe: dict) -> None:
    new_state = await ctx.repository.toggle_favorite(recipe)
    ctx.state.recipe_catalog = await ctx.repository.load_recipe_catalog()
    ctx.refreshables.refresh("catalog")
    ui.notify(
        "Saved to favorites" if new_state else "Removed from favorites",
        type="positive" if new_state else "info",
    )


async def delete_recipe(ctx: UIContext, recipe_id: str) -> None:
    """Remove a catalog entry outright — for discarding a bad import or a
    mistaken save, distinct from un-favoriting (`toggle_favorite`), which
    keeps the entry. Shared by the drawer's catalog list and the full-screen
    browser, both of which offer a delete icon per row."""
    await ctx.repository.delete_catalog_recipe(recipe_id)
    ctx.state.recipe_catalog = [
        r for r in ctx.state.recipe_catalog if r["id"] != recipe_id
    ]
    ctx.refreshables.refresh("catalog")
    ui.notify("Removed from catalog", type="positive")


@dataclass
class RenameDialogHandles:
    open: Callable[[dict], None]


def build_rename_dialog(ctx: UIContext) -> RenameDialogHandles:
    """One rename dialog, reused by every catalog row anywhere in the app —
    same "one dialog, keyed off state" shape as `ui_cards.recipe_detail`,
    rather than one dialog built per row.
    """
    state = ctx.state
    REPOSITORY = ctx.repository
    refreshables = ctx.refreshables

    async def save() -> None:
        entry = next(
            (r for r in state.recipe_catalog if r["id"] == state.edit_catalog_id), None
        )
        if entry is None:
            return
        new_name = (state.edit_catalog_name or "").strip()
        if not new_name:
            ui.notify("Name can't be empty.", type="warning")
            return
        record = await REPOSITORY.rename_catalog_recipe(entry["id"], new_name)
        if record:
            entry["recipe"] = record["recipe"]
        refreshables.refresh("catalog")
        dialog.close()
        ui.notify("Recipe renamed", type="positive")

    with ui.dialog() as dialog:
        with ui.element("div").classes(
            "bg-slate-900 rounded-lg p-4 w-96 max-w-full flex flex-col gap-2"
        ):
            ui.label("Rename recipe").classes("text-sm font-semibold")
            ui.input(label="Name").bind_value(state, "edit_catalog_name").props(
                "dense outlined"
            ).classes("w-full text-xs")
            with ui.row().classes("justify-end gap-2 mt-2"):
                ui.button("Cancel", on_click=dialog.close).props("dense flat no-caps")
                ui.button("Save", on_click=save).props("dense no-caps")

    def open_for(entry: dict) -> None:
        state.edit_catalog_id = entry["id"]
        state.edit_catalog_name = entry["recipe"]["name"]
        dialog.open()

    return RenameDialogHandles(open=open_for)

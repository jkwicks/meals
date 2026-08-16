"""Favorites/catalog helpers shared by `ui_cards` (the bookmark button on a
cooked card) and `ui_drawer` (the catalog list's bookmark toggle).

These read `state.recipe_catalog` (the in-memory copy loaded at startup)
rather than awaiting the repository, deliberately: `ui_cards.canvas()` calls
`is_favorited` once per cooked card on every repaint, and turning that into
a disk read per card would make a repaint O(cards) file opens. Every handler
that mutates the catalog refreshes the "catalog"/"favorites" topics, so it
stays in sync — do not "fix" these into async repository calls.
"""

from typing import Optional

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

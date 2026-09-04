"""Formats a generated week into a printable menu — Markdown text, a
magazine-style PDF (CSIRO Total Wellbeing Diet inspired: restrained dark-ink
typography, a teal-header day-by-day grid, a tickable prep checklist, a
single hairline-ruled recipe page per meal grouped by meal type, and a
catalog-style shopping list), and a self-contained mobile HTML page.

All three walk `WeekPlan.slots` (one `SlotSpec` per eating slot) resolved
against `WeekPlan.by_slot()` (cook events), the same source
`WeekPlan.day_slot_macros` reads — not `PlannerState`/`SlotView`, so this
module has no UI dependency and works the same from the NiceGUI drawer today
or a future CLI flag.

`build_week_menu_pdf` needs `reportlab` (pure Python, no system libraries —
unlike `weasyprint`, which needs Cairo/Pango, it installs cleanly into this
project's venv with a plain `pip install`); it's a hard requirement (see
`requirements.txt`), and `ui_app.py` already needs it for the "Download PDF
Menu" button to exist, so importing it at module level costs nothing the app
doesn't already pay.

`build_week_menu_html` needs nothing beyond the standard library — the PDF
paginates for a printer, which is exactly wrong for a phone: no page to turn,
pinch-zoom fighting a fixed layout, and the printed page's checkboxes are
just ink. The HTML export is a single scrolling page sized for a phone, and
its "tap a step when done" behaviour (a step's `onclick` toggles one CSS
class) is real state, not a static mark — the same `line-through` treatment
`ui_cards.recipe_detail`'s step rows already use in the live UI, so a cook
who has seen the app once recognises it instantly. Deliberately unpersisted
(no `localStorage`): reopening the file starts every step unticked, the same
scratch-state choice `recipe_detail` and the shopping drawer already make, so
a re-downloaded plan can't disagree with stale ticks from a previous week.
"""

import io
import json
import os
import re
from html import escape as html_escape
from typing import Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from planner import CookEvent, Recipe, SundayPrepSession, WeekPlan
from repository import DATA_DIR
from shopping import (
    NON_SHOPPING_INGREDIENTS,
    ShoppingItem,
    aggregate_cook_events,
    apply_pantry,
    format_quantity,
    format_shopping_list_text,
    matcher_name,
    normalize_name,
    ordered_departments,
    pantry_covered_line,
    pantry_note,
)
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, humanize, slot_label

# --------------------------------------------------------------------------
# Palette + styles
#
# Every other paragraph style in this file derives from one of the four
# base styles below rather than `getSampleStyleSheet()` directly, so the
# whole document's look lives in one place instead of being re-decided
# per section.
# --------------------------------------------------------------------------

ACCENT_DARK = colors.HexColor("#134e4a")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d1d5db")
ROW_TINT = colors.HexColor("#f9fafb")

PAGE_MARGIN = 18 * mm
CONTENT_WIDTH = letter[0] - 2 * PAGE_MARGIN

_base = getSampleStyleSheet()

STYLES = {
    # Large display heading (page 1 title, "Shopping List", checklist title).
    # Regular weight, not bold — Helvetica has no true light cut, and at this
    # size a regular weight already reads as an editorial display face rather
    # than a shouty banner, which is what the CSIRO reference uses throughout.
    "Heading1": ParagraphStyle(
        "MenuHeading1",
        parent=_base["Title"],
        fontName="Helvetica",
        fontSize=27,
        leading=31,
        textColor=INK,
        alignment=0,
        spaceAfter=10,
    ),
    "Heading2": ParagraphStyle(
        "MenuHeading2",
        parent=_base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        textColor=INK,
        spaceBefore=12,
        spaceAfter=6,
    ),
    "BodyText": ParagraphStyle(
        "MenuBodyText",
        parent=_base["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=14.5,
        textColor=INK,
    ),
    "SubText": ParagraphStyle(
        "MenuSubText",
        parent=_base["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=MUTED,
    ),
}

STYLES.update(
    {
        "Eyebrow": ParagraphStyle(
            "MenuEyebrow",
            parent=STYLES["SubText"],
            fontName="Helvetica-Bold",
            textColor=MUTED,
            spaceAfter=2,
        ),
        # Recipe title — same restrained, regular-weight treatment as
        # Heading1 but sized for a page that's mostly ingredients/method
        # below it, matching the CSIRO reference's plain dark-grey recipe
        # headings (no colour, no bold).
        "RecipeTitle": ParagraphStyle(
            "MenuRecipeTitle", parent=STYLES["Heading1"], fontSize=19, leading=23, spaceAfter=10
        ),
        # In-flow section label ("Breakfast Meals") placed once, directly
        # above the first recipe of that meal type — not a full divider
        # page. Sized close to a recipe title since it reads as the same
        # kind of heading, one level up.
        "CategoryLabel": ParagraphStyle(
            "MenuCategoryLabel",
            parent=STYLES["Heading1"],
            fontSize=22,
            leading=26,
            spaceAfter=16,
        ),
        "SectionHeading": ParagraphStyle(
            "MenuSectionHeading",
            parent=STYLES["Heading2"],
            fontSize=11,
            leading=14,
            textColor=INK,
            spaceBefore=12,
            spaceAfter=4,
        ),
        "GridHeader": ParagraphStyle(
            "MenuGridHeader",
            parent=STYLES["BodyText"],
            fontSize=9.5,
            leading=12,
            textColor=colors.white,
            alignment=1,
            fontName="Helvetica-Bold",
        ),
        "GridLabel": ParagraphStyle(
            "MenuGridLabel", parent=STYLES["BodyText"], fontSize=9.5, leading=12, fontName="Helvetica-Bold"
        ),
        "GridCell": ParagraphStyle("MenuGridCell", parent=STYLES["BodyText"], fontSize=8, leading=10.5),
        "GridTotal": ParagraphStyle(
            "MenuGridTotal",
            parent=STYLES["SubText"],
            fontSize=7.5,
            leading=9.5,
            fontName="Helvetica-Bold",
            textColor=ACCENT_DARK,
        ),
        "ChecklistText": ParagraphStyle(
            "MenuChecklistText",
            parent=STYLES["BodyText"],
            fontSize=10,
            leading=13.5,
            fontName="Helvetica-Bold",
        ),
        "ChecklistNote": ParagraphStyle(
            "MenuChecklistNote", parent=STYLES["SubText"], fontSize=8.5, leading=11, spaceBefore=1
        ),
        # The closing "Makes N servings. Each serving provides..." line —
        # a plain left-aligned sentence, not a boxed/centred footer, so it
        # reads as prose the way the CSIRO reference's "Makes 1 serve." does.
        "ServesLine": ParagraphStyle(
            "MenuServesLine",
            parent=STYLES["BodyText"],
            fontSize=9.5,
            leading=13,
            textColor=MUTED,
        ),
    }
)


def _slot_recipe(by_slot: Dict[str, CookEvent], slot: SlotSpec) -> Optional[Recipe]:
    source_id = slot.id if slot.mode == MODE_COOK else slot.source
    event = by_slot.get(source_id)
    return event.recipe if event else None


def _slot_entry(week_plan: WeekPlan, by_slot: Dict[str, CookEvent], slot: SlotSpec) -> dict:
    """One eating slot's exportable info, shared by both output formats.

    `macros` is `None` for a skipped or ungenerated slot — the caller decides
    how to render "nothing to show" for its format rather than this function
    picking blank text vs. a blank table cell.
    """
    if slot.mode == MODE_SKIP:
        return {"meal_type": slot.meal_type, "dish": "Skipped", "macros": None, "note": None}
    recipe = _slot_recipe(by_slot, slot)
    if recipe is None:
        source_id = slot.id if slot.mode == MODE_COOK else slot.source
        return {
            "meal_type": slot.meal_type,
            "dish": "Not generated",
            "macros": None,
            # Keyed by slot_id now, not day (see WeekPlan.failures) — a
            # leftover slot's own day may have generated fine even though its
            # source cook failed, so the lookup goes through source_id.
            "note": week_plan.failures.get(source_id),
        }
    note = (
        f"leftover from {slot_label(slot.source, short=True)}"
        if slot.mode == MODE_LEFTOVER
        else None
    )
    return {
        "meal_type": slot.meal_type,
        "dish": recipe.name,
        "macros": recipe.per_serving_macros,
        "note": note,
    }


def _day_entries(week_plan: WeekPlan, by_slot: Dict[str, CookEvent], day: str) -> List[dict]:
    return [_slot_entry(week_plan, by_slot, slot) for slot in week_plan.slots if slot.day == day]


def _macro_text(macros: dict) -> str:
    """The per-serving macro line shared by the PDF grid and the Markdown export.

    Fibre is appended last and only when present. `.get` rather than `[]`
    because a `week_plan.json` generated before `Ingredient.fiber_g` existed
    totals without the key — the same pre-migration tolerance
    `history_styles` extends to old `meal_history.json` entries — and a
    zero-fibre line is noise rather than information.
    """
    text = (
        f"{macros['calories']:.0f} kcal · {macros['protein_g']:.0f}g P · "
        f"{macros['net_carbs_g']:.0f}g C · {macros['fat_g']:.0f}g F"
    )
    fiber = macros.get("fiber_g") or 0.0
    return f"{text} · {fiber:.0f}g fibre" if fiber else text


def format_week_menu_markdown(week_plan: WeekPlan) -> str:
    """The whole week as Markdown — one section per day, one line per meal."""
    by_slot = week_plan.by_slot()
    lines = ["# Weekly Menu"]
    if week_plan.generated_at:
        lines.append(f"_Generated {week_plan.generated_at[:16].replace('T', ' ')}_")
    lines.append("")

    for day in week_plan.days:
        lines.append(f"## {day}")
        for entry in _day_entries(week_plan, by_slot, day):
            text = f"**{entry['meal_type'].title()}** — {entry['dish']}"
            if entry["note"]:
                text += f" ({entry['note']})"
            if entry["macros"]:
                text += f" · {_macro_text(entry['macros'])}"
            lines.append(f"- {text}")
        totals = week_plan.day_slot_macros(day)
        lines.append(f"- **Day total** — {_macro_text(totals)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _grid_meal_types(week_plan: WeekPlan) -> List[str]:
    """Meal types in first-seen slot order — both the summary grid's column
    order and the recipe section order.

    Not a fixed constant: a week's slots are the source of truth for which
    meal types actually appear, so a config with a non-default `meal_types`
    list (or no snack slots at all) still produces the right columns.
    """
    seen: List[str] = []
    for slot in week_plan.slots:
        if slot.meal_type not in seen:
            seen.append(slot.meal_type)
    return seen


def _summary_cell(entry: Optional[dict]) -> Paragraph:
    if entry is None:
        return Paragraph("", STYLES["GridCell"])

    text = entry["dish"]
    if entry["note"]:
        text += f" ({entry['note']})"
    if entry["macros"] is None:
        return Paragraph(f"<i>{escape(text)}</i>", STYLES["GridCell"])
    return Paragraph(escape(text), STYLES["GridCell"])


def _summary_table(week_plan: WeekPlan, by_slot: Dict[str, CookEvent]) -> Table:
    """Page 1: a weekly-at-a-glance grid — days down the rows, meal types
    across the columns, so each row reads like one day of a diary
    rather than one meal's history across the week.

    Deliberately doesn't repeat per-meal macros here — that detail lives on
    each recipe's own page. This page answers "what am I eating this week"
    at a glance, not "what's in it".
    """
    meal_types = _grid_meal_types(week_plan)
    entries_by_day = {
        day: {entry["meal_type"]: entry for entry in _day_entries(week_plan, by_slot, day)}
        for day in week_plan.days
    }

    header = [Paragraph("", STYLES["GridHeader"])]
    header += [Paragraph(meal_type.title(), STYLES["GridHeader"]) for meal_type in meal_types]
    header.append(Paragraph("Daily Total", STYLES["GridHeader"]))
    rows = [header]

    for day in week_plan.days:
        row = [Paragraph(day, STYLES["GridLabel"])]
        row += [_summary_cell(entries_by_day[day].get(meal_type)) for meal_type in meal_types]
        row.append(Paragraph(escape(_macro_text(week_plan.day_slot_macros(day))), STYLES["GridTotal"]))
        rows.append(row)

    label_width = 20 * mm
    total_width = 46 * mm
    meal_width = (CONTENT_WIDTH - label_width - total_width) / len(meal_types)
    col_widths = [label_width] + [meal_width] * len(meal_types) + [total_width]

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT_DARK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    # No inner grid — a bottom rule per row plus alternating tints carries
    # the structure instead, which is what makes the CSIRO-style grid read
    # as one calm sheet rather than a spreadsheet.
    for row_index in range(1, len(rows)):
        style_commands.append(("LINEBELOW", (0, row_index), (-1, row_index), 0.5, colors.lightgrey))
        if row_index % 2 == 0:
            style_commands.append(("BACKGROUND", (0, row_index), (-1, row_index), ROW_TINT))
    table.setStyle(TableStyle(style_commands))
    return table


def _prep_checklist_page(session: SundayPrepSession) -> list:
    """Page 2 (when present): a tickable checklist for the Sunday batch-prep
    session, one checkbox per timeline phase, only rendered when a week
    actually has one.
    """
    subtitle_parts = [
        f"{session.total_active_minutes} min active",
        f"{session.total_passive_minutes} min passive",
    ]
    if session.meals_included:
        subtitle_parts.append(f"covers {', '.join(session.meals_included)}")

    flow: list = [
        Paragraph("Batch Cooking &amp; Preparation Checklist", STYLES["Heading1"]),
        Paragraph(escape(" · ".join(subtitle_parts)), STYLES["SubText"]),
        Spacer(1, 10),
    ]

    phase_items = []
    for phase in session.timeline:
        timing = f"{phase.name} — {phase.active_minutes} min active"
        if phase.passive_minutes:
            timing += f" / {phase.passive_minutes} min passive"
        cell = [Paragraph(escape(timing), STYLES["ChecklistText"])]
        if phase.description:
            cell.append(Paragraph(escape(phase.description), STYLES["ChecklistNote"]))
        phase_items.append(ListItem(cell, value="[ ]", spaceBefore=8))

    flow.append(
        ListFlowable(
            phase_items,
            bulletType="bullet",
            leftIndent=20,
            bulletFontName="Helvetica-Bold",
            bulletFontSize=11,
        )
    )

    if session.aggregated_ingredients:
        flow.append(Paragraph("Aggregated Prep", STYLES["SectionHeading"]))
        agg_items = [
            ListItem(
                Paragraph(f"<b>{escape(item)}</b> — {escape(note)}", STYLES["BodyText"]),
                value="[ ]",
            )
            for item, note in session.aggregated_ingredients.items()
        ]
        flow.append(ListFlowable(agg_items, bulletType="bullet", leftIndent=20))

    flow.append(PageBreak())
    return flow


def _ingredient_line(ingredient) -> str:
    return f"{ingredient.quantity_g:.0f}g {ingredient.name}"


def _recipe_meta_line(event: CookEvent) -> str:
    parts = [
        f"{event.day} · {event.meal_type.title()}",
        f"{event.portions} serving{'s' if event.portions != 1 else ''}",
    ]
    if event.recipe.prep_time_minutes:
        parts.append(f"{event.recipe.prep_time_minutes} min prep")
    if event.style:
        parts.append(humanize(event.style))
    if event.cuisine:
        parts.append(humanize(event.cuisine))
    return " · ".join(parts)


def _feeds_note(event: CookEvent) -> Optional[str]:
    """Which other slots this batch also covers, for a bulk-cooked recipe."""
    others = [value for value in event.eaten_by if value != event.slot_id]
    if not others:
        return None
    return "Also feeds: " + ", ".join(slot_label(value) for value in others)


def _serves_line(event: CookEvent) -> str:
    """Builds the "Makes N servings. Each serving provides..." sentence,
    with markup already applied — the caller doesn't need to escape it
    further since every interpolated value is a number or a word this
    module controls, none of it user-supplied recipe text.
    """
    macros = event.recipe.per_serving_macros
    portions = event.portions
    lead = f"Makes {portions} serving{'s' if portions != 1 else ''}."
    fiber = macros.get("fiber_g") or 0.0
    fiber_text = f", {fiber:.0f}g fibre" if fiber else ""
    return (
        f"<b>{lead}</b> Each serving provides {macros['calories']:.0f} kcal, "
        f"{macros['protein_g']:.0f}g protein, {macros['net_carbs_g']:.0f}g carbs, "
        f"{macros['fat_g']:.0f}g fat{fiber_text}."
    )


def _ingredient_table(recipe: Recipe) -> Table:
    """Ingredients as a hairline-ruled list — one row per ingredient, a thin
    grey rule under every row but the last — instead of a bulleted list, so
    a recipe page reads like a printed ledger rather than a slide deck.
    """
    rows = [[Paragraph(escape(_ingredient_line(ingredient)), STYLES["BodyText"])] for ingredient in recipe.ingredients]
    table = Table(rows, colWidths=[CONTENT_WIDTH])
    style_commands = [
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_index in range(len(rows) - 1):
        style_commands.append(("LINEBELOW", (0, row_index), (-1, row_index), 0.5, RULE))
    table.setStyle(TableStyle(style_commands))
    return table


def _recipe_page(event: CookEvent) -> list:
    """One recipe's dedicated page: eyebrow meta, title, a single-column
    hairline-ruled ingredient list, numbered method, and a closing
    "Makes N servings" sentence — no photo (none exist for a generated
    recipe), so the page stays single-column full-width rather than
    reserving dead space for an image that isn't there.
    """
    recipe = event.recipe
    flow: list = [
        Paragraph(escape(_recipe_meta_line(event)), STYLES["Eyebrow"]),
        Paragraph(escape(recipe.name), STYLES["RecipeTitle"]),
    ]

    feeds = _feeds_note(event)
    if feeds:
        flow.append(Paragraph(escape(feeds), STYLES["SubText"]))
        flow.append(Spacer(1, 6))

    flow.append(_ingredient_table(recipe))

    flow.append(Paragraph("Method", STYLES["SectionHeading"]))
    flow.append(
        ListFlowable(
            [
                ListItem(Paragraph(escape(step), STYLES["BodyText"]), spaceBefore=5)
                for step in recipe.instructions
            ],
            bulletType="1",
            leftIndent=16,
            bulletFontSize=9.5,
        )
    )

    if recipe.prep_notes:
        flow.append(Spacer(1, 8))
        flow.append(Paragraph(escape(recipe.prep_notes), STYLES["SubText"]))

    flow.append(Spacer(1, 12))
    flow.append(Paragraph(_serves_line(event), STYLES["ServesLine"]))
    flow.append(PageBreak())
    return flow


def _recipes_by_category(week_plan: WeekPlan) -> List[tuple]:
    """Cook events grouped by meal type, in `_grid_meal_types` order, each
    bucket keeping the week-order the events already arrive in.
    """
    order = _grid_meal_types(week_plan)
    buckets: Dict[str, List[CookEvent]] = {meal_type: [] for meal_type in order}
    for event in week_plan.cook_events:
        buckets.setdefault(event.meal_type, []).append(event)
    return [(meal_type, buckets[meal_type]) for meal_type in order if buckets.get(meal_type)]


def _department_item_table(items: List[ShoppingItem], columns: int = 2) -> Table:
    """One department's items tiled into a fixed number of columns, each row
    hairline-ruled the same way a recipe's ingredient list is — one visual
    language for "things to check off" everywhere in the document."""
    cells = []
    for item in items:
        text = f"[ ]  {escape(item.name)} — {escape(format_quantity(item.name, item.total_amount_g))}"
        if item.buy_late:
            text += "<br/><font size=7 color='#6b7280'>buy fresh closer to the day</font>"
        cells.append(Paragraph(text, STYLES["BodyText"]))

    rows = [cells[i : i + columns] for i in range(0, len(cells), columns)]
    if rows and len(rows[-1]) < columns:
        rows[-1] += [Paragraph("", STYLES["BodyText"])] * (columns - len(rows[-1]))

    col_width = CONTENT_WIDTH / columns
    table = Table(rows, colWidths=[col_width] * columns)
    style_commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for row_index in range(len(rows) - 1):
        style_commands.append(("LINEBELOW", (0, row_index), (-1, row_index), 0.5, RULE))
    table.setStyle(TableStyle(style_commands))
    return table


PantryEntries = Optional[Sequence[Tuple[str, Optional[float]]]]


def _week_shopping_list(week_plan: WeekPlan, pantry: PantryEntries):
    """The week's list, with the pantry already off it.

    One helper for both export paths so a PDF and the mobile page cannot
    disagree about a week — the same reason `repository.catalog_matches`
    exists for the Library grid and `/api/recipes`. `pantry` is
    `planner.inventory_entries` output threaded down from whoever holds a
    config; `None` means nobody does, and the list is what the recipes need.
    """
    shopping_list = aggregate_cook_events(week_plan.cook_events, week_plan.days)
    return apply_pantry(shopping_list, pantry) if pantry else shopping_list


def _shopping_list_pages(week_plan: WeekPlan, pantry: PantryEntries = None) -> list:
    """The whole week's shopping, grouped by department in a
    catalog-style grid, followed by a plain-text page built from the same
    `format_shopping_list_text` the CLI and shopping-drawer copy buttons
    use — so the wording can't drift between the styled table and the copy
    someone pastes into their phone at the shop.
    """
    shopping_list = _week_shopping_list(week_plan, pantry)

    flow: list = [
        Paragraph("Shopping List", STYLES["Heading1"]),
        Paragraph(
            escape(f"Everything for the week, grouped by department — {len(shopping_list.items())} items."),
            STYLES["SubText"],
        ),
        Spacer(1, 10),
    ]

    for department in ordered_departments(shopping_list):
        flow.append(Paragraph(escape(department), STYLES["SectionHeading"]))
        flow.append(_department_item_table(shopping_list.categories[department]))

    covered = pantry_covered_line(shopping_list)
    if covered:
        flow.append(Spacer(1, 6))
        flow.append(Paragraph(escape(covered), STYLES["SubText"]))

    flow.append(PageBreak())
    flow.append(Paragraph("Plain-Text Version", STYLES["Heading2"]))
    flow.append(Paragraph("For copying onto your phone before you shop.", STYLES["SubText"]))
    flow.append(Spacer(1, 6))
    for line in format_shopping_list_text(shopping_list, week_plan.cook_events).splitlines():
        if not line.strip():
            flow.append(Spacer(1, 4))
        else:
            flow.append(Paragraph(escape(line), STYLES["SubText"]))

    return flow


def build_week_menu_pdf(week_plan: WeekPlan, pantry: PantryEntries = None) -> bytes:
    """The whole week as a magazine-style PDF: a page-1 summary grid, an
    optional Sunday prep checklist, one page per recipe grouped into a
    section per meal type, then a department-grouped shopping list.

    Returns bytes rather than writing to disk: the caller (the NiceGUI
    shopping drawer today) hands this straight to `ui.download`, and nothing
    here needs to know whether it's a browser response or a file on disk.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Weekly Menu",
    )

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(doc.pagesize[0] - PAGE_MARGIN, 10 * mm, f"Page {doc.page}")
        canvas.drawString(PAGE_MARGIN, 10 * mm, "Weekly Menu")
        canvas.restoreState()

    by_slot = week_plan.by_slot()

    # --- Page 1: weekly summary grid ---
    story: list = [Paragraph("Weekly Menu", STYLES["Heading1"])]
    if week_plan.generated_at:
        story.append(
            Paragraph(f"Generated {week_plan.generated_at[:16].replace('T', ' ')}", STYLES["SubText"])
        )
    story.append(Spacer(1, 10))
    story.append(_summary_table(week_plan, by_slot))

    if week_plan.failures:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Not generated", STYLES["SectionHeading"]))
        for key, error in week_plan.failures.items():
            story.append(Paragraph(escape(f"{slot_label(key)}: {error}"), STYLES["SubText"]))

    story.append(PageBreak())

    # --- Page 2 (optional): Sunday batch-prep checklist ---
    if week_plan.sunday_prep_session:
        story.extend(_prep_checklist_page(week_plan.sunday_prep_session))

    # --- One page per recipe actually being cooked, grouped by meal type.
    # The category label ("Breakfast Meals") sits once, directly above the
    # first recipe of that meal type, rather than on its own divider page —
    # each recipe already ends in a PageBreak, so the category still starts
    # on a fresh page without spending a whole page on just its name. ---
    for meal_type, events in _recipes_by_category(week_plan):
        for index, event in enumerate(events):
            recipe_flow = _recipe_page(event)
            if index == 0:
                recipe_flow = [
                    Paragraph(escape(f"{meal_type.title()} Meals"), STYLES["CategoryLabel"])
                ] + recipe_flow
            story.extend(recipe_flow)

    # --- Shopping list, grouped by department ---
    if week_plan.cook_events:
        story.extend(_shopping_list_pages(week_plan, pantry))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Mobile HTML export
#
# One scrolling page, not the PDF's paginated layout: a sticky pill nav
# jumps between the day summary, the prep checklist (if any), each meal
# type's recipes, and the shopping list. Every tappable row (a step, a
# shopping item, a prep phase) is a plain `onclick="this.classList.toggle(
# 'done')"` — no `<script>` block needed at all, which keeps the file exactly
# what it looks like: static markup you can open straight from a phone's
# downloads folder with nothing to fetch and nothing to break.
# --------------------------------------------------------------------------

_HTML_STYLE = """
:root {
  --bg: #fbfaf8; --surface: #ffffff; --ink: #1f2937; --muted: #6b7280;
  --rule: #e5e7eb; --accent: #0f766e; --done-bg: rgba(16,185,129,.12);
  --done-ink: #059669;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f172a; --surface: #131f35; --ink: #e2e8f0; --muted: #94a3b8;
    --rule: #253449; --accent: #2dd4bf; --done-bg: rgba(52,211,153,.15);
    --done-ink: #6ee7b7;
  }
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.page-header { padding: 20px 16px 8px; }
.page-header h1 { margin: 0 0 4px; font-size: 22px; }
.subtle { margin: 0; color: var(--muted); font-size: 13px; }
.topnav {
  position: sticky; top: 0; z-index: 5; display: flex; gap: 6px;
  overflow-x: auto; padding: 8px 16px; background: var(--bg);
  border-bottom: 1px solid var(--rule); -webkit-overflow-scrolling: touch;
}
.topnav a {
  flex: none; padding: 6px 12px; border-radius: 999px; background: var(--surface);
  border: 1px solid var(--rule); color: var(--ink); text-decoration: none;
  font-size: 13px; white-space: nowrap;
}
main { max-width: 640px; margin: 0 auto; padding: 8px 16px 48px; }
section { margin-top: 28px; }
h2 {
  font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted);
  margin: 0 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--rule);
}
h3 { font-size: 19px; margin: 0 0 2px; }
h4.label {
  font-size: 11px; letter-spacing: .05em; text-transform: uppercase;
  color: var(--accent); margin: 16px 0 6px;
}
.day-list, .shop-list, .prep-list { list-style: none; margin: 0; padding: 0; }
.day-row, .shop-row, .prep-row {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px;
  padding: 9px 4px; border-bottom: 1px solid var(--rule);
}
.day-row-type { font-weight: 600; min-width: 78px; }
.day-row-dish { flex: 1 1 auto; }
.day-row-meta, .shop-note, .prep-desc { flex-basis: 100%; color: var(--muted); font-size: 12.5px; }
.day-row.muted .day-row-dish { color: var(--muted); font-style: italic; }
.day-total { text-align: right; font-weight: 600; color: var(--accent); padding: 8px 4px 0; }
.failures { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 10px 14px; }
.failures ul { margin: 6px 0 0; padding-left: 18px; }
.recipe {
  background: var(--surface); border: 1px solid var(--rule); border-radius: 12px;
  padding: 16px; margin-bottom: 16px;
}
.eyebrow { margin: 0; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); }
.feeds { margin: 4px 0 0; font-size: 12.5px; color: var(--accent); }
.ingredients { list-style: none; margin: 0; padding: 0; }
.ingredients li { padding: 7px 2px; border-bottom: 1px solid var(--rule); font-size: 14.5px; }
.steps { list-style: none; margin: 0; padding: 0; }
.step {
  display: flex; align-items: flex-start; gap: 10px; padding: 11px 6px;
  border-bottom: 1px solid var(--rule); cursor: pointer; user-select: none;
}
.step-num {
  flex: none; width: 24px; height: 24px; border-radius: 50%;
  border: 1.5px solid var(--muted); color: var(--muted); font-size: 12px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.step-text { flex: 1 1 auto; min-width: 0; }
.step.done { background: var(--done-bg); }
.step.done .step-text { text-decoration: line-through; color: var(--muted); }
.step.done .step-num { background: var(--done-ink); border-color: var(--done-ink); color: var(--surface); }
.prep-note { margin: 10px 0 0; font-size: 12.5px; color: var(--muted); }
.serves { margin: 14px 0 0; font-size: 13px; color: var(--muted); }
.shop-name { flex: 1 1 auto; font-weight: 500; }
.shop-qty { color: var(--muted); font-variant-numeric: tabular-nums; }
.shop-row { cursor: pointer; user-select: none; }
.shop-row.done { background: var(--done-bg); }
.shop-row.done .shop-name { text-decoration: line-through; color: var(--muted); }
.prep-name { font-weight: 600; flex: 1 1 auto; }
.prep-time { color: var(--muted); font-size: 12.5px; }
.prep-row { cursor: pointer; user-select: none; }
.prep-row.done { background: var(--done-bg); }
.prep-row.done .prep-name { text-decoration: line-through; color: var(--muted); }
"""


def _html_slug(value: str) -> str:
    return value.replace(":", "-").replace(" ", "-").lower()


def _html_day_section(week_plan: WeekPlan, by_slot: Dict[str, CookEvent], day: str) -> str:
    rows = []
    for entry in _day_entries(week_plan, by_slot, day):
        bits = []
        if entry["note"]:
            bits.append(html_escape(entry["note"]))
        if entry["macros"]:
            bits.append(html_escape(_macro_text(entry["macros"])))
        meta = " · ".join(bits)
        rows.append(
            f'<li class="day-row{"" if entry["macros"] else " muted"}">'
            f'<span class="day-row-type">{html_escape(entry["meal_type"].title())}</span>'
            f'<span class="day-row-dish">{html_escape(entry["dish"])}</span>'
            + (f'<span class="day-row-meta">{meta}</span>' if meta else "")
            + "</li>"
        )
    total = html_escape(_macro_text(week_plan.day_slot_macros(day)))
    return (
        f'<section class="day" id="day-{_html_slug(day)}">'
        f"<h2>{html_escape(day)}</h2>"
        f'<ul class="day-list">{"".join(rows)}</ul>'
        f'<p class="day-total">Day total — {total}</p>'
        "</section>"
    )


def _html_recipe_card(event: CookEvent) -> str:
    recipe = event.recipe
    macros = recipe.per_serving_macros
    portions_label = f"ALL {event.portions} PORTION{'S' if event.portions != 1 else ''}"
    ingredients = "".join(
        f"<li>{html_escape(_ingredient_line(ingredient))}</li>" for ingredient in recipe.ingredients
    )
    steps = "".join(
        '<li class="step" onclick="this.classList.toggle(\'done\')">'
        f'<span class="step-num">{index}</span>'
        f'<span class="step-text">{html_escape(step)}</span>'
        "</li>"
        for index, step in enumerate(recipe.instructions, start=1)
    )
    fiber = macros.get("fiber_g") or 0.0
    serves = (
        f"Makes {event.portions} serving{'s' if event.portions != 1 else ''}. "
        f"Each serving provides {macros['calories']:.0f} kcal, {macros['protein_g']:.0f}g protein, "
        f"{macros['net_carbs_g']:.0f}g carbs, {macros['fat_g']:.0f}g fat"
        + (f", {fiber:.0f}g fibre" if fiber else "")
        + "."
    )
    feeds = _feeds_note(event)
    feeds_html = f'<p class="feeds">{html_escape(feeds)}</p>' if feeds else ""
    prep_html = f'<p class="prep-note">{html_escape(recipe.prep_notes)}</p>' if recipe.prep_notes else ""
    return (
        f'<article class="recipe" id="recipe-{_html_slug(event.slot_id)}">'
        f'<p class="eyebrow">{html_escape(_recipe_meta_line(event))}</p>'
        f"<h3>{html_escape(recipe.name)}</h3>"
        f"{feeds_html}"
        f'<h4 class="label">{portions_label}</h4>'
        f'<ul class="ingredients">{ingredients}</ul>'
        '<h4 class="label">Method — tap a step when it\'s done</h4>'
        f'<ol class="steps">{steps}</ol>'
        f"{prep_html}"
        f'<p class="serves">{html_escape(serves)}</p>'
        "</article>"
    )


def _html_prep_section(session: SundayPrepSession) -> str:
    subtitle_parts = [
        f"{session.total_active_minutes} min active",
        f"{session.total_passive_minutes} min passive",
    ]
    if session.meals_included:
        subtitle_parts.append("covers " + ", ".join(session.meals_included))

    phases = "".join(
        '<li class="prep-row" onclick="this.classList.toggle(\'done\')">'
        f'<span class="prep-name">{html_escape(phase.name)}</span>'
        '<span class="prep-time">'
        f"{phase.active_minutes} min active"
        + (f" / {phase.passive_minutes} min passive" if phase.passive_minutes else "")
        + "</span>"
        + (f'<span class="prep-desc">{html_escape(phase.description)}</span>' if phase.description else "")
        + "</li>"
        for phase in session.timeline
    )
    aggregated = "".join(
        '<li class="prep-row" onclick="this.classList.toggle(\'done\')">'
        f'<span class="prep-name">{html_escape(item)}</span>'
        f'<span class="prep-desc">{html_escape(note)}</span></li>'
        for item, note in session.aggregated_ingredients.items()
    )
    aggregated_html = (
        f'<h4 class="label">Aggregated Prep</h4><ul class="prep-list">{aggregated}</ul>' if aggregated else ""
    )
    return (
        '<section class="prep" id="prep">'
        "<h2>Batch Cooking &amp; Preparation Checklist</h2>"
        f'<p class="subtle">{html_escape(" · ".join(subtitle_parts))}</p>'
        f'<ul class="prep-list">{phases}</ul>'
        f"{aggregated_html}"
        "</section>"
    )


def _html_shopping_section(week_plan: WeekPlan, pantry: PantryEntries = None) -> str:
    if not week_plan.cook_events:
        return ""
    shopping_list = _week_shopping_list(week_plan, pantry)
    departments = []
    for department in ordered_departments(shopping_list):
        rows = "".join(
            '<li class="shop-row" onclick="this.classList.toggle(\'done\')">'
            f'<span class="shop-name">{html_escape(item.name)}</span>'
            f'<span class="shop-qty">{html_escape(format_quantity(item.name, item.total_amount_g))}</span>'
            + (f'<span class="shop-note">{html_escape(pantry_note(item))}</span>' if pantry_note(item) else "")
            + ('<span class="shop-note">buy fresh closer to the day</span>' if item.buy_late else "")
            + "</li>"
            for item in shopping_list.categories[department]
        )
        departments.append(f'<h4 class="label">{html_escape(department)}</h4><ul class="shop-list">{rows}</ul>')
    count = len(shopping_list.items())
    return (
        '<section class="shopping" id="shopping">'
        "<h2>Shopping List</h2>"
        f'<p class="subtle">Everything for the week, grouped by department — {count} items. '
        "Tap an item to check it off.</p>"
        f"{''.join(departments)}"
        "</section>"
    )


def build_week_menu_html(week_plan: WeekPlan, pantry: PantryEntries = None) -> str:
    """The whole week as one self-contained, mobile-sized HTML page: a
    sticky nav, a day-by-day summary, an optional Sunday prep checklist,
    every recipe with tap-to-strike steps, and a tap-to-check shopping list.

    Returns a `str`, the same "hand it to the caller" shape as
    `format_week_menu_markdown` and `build_week_menu_pdf` (bytes) — the
    caller (`ui.download`) decides how it leaves the process.
    """
    by_slot = week_plan.by_slot()
    generated = f"Generated {week_plan.generated_at[:16].replace('T', ' ')}" if week_plan.generated_at else ""

    nav_links = [f'<a href="#day-{_html_slug(day)}">{html_escape(day)}</a>' for day in week_plan.days]
    if week_plan.sunday_prep_session:
        nav_links.append('<a href="#prep">Prep</a>')
    for meal_type in _grid_meal_types(week_plan):
        nav_links.append(f'<a href="#recipes-{_html_slug(meal_type)}">{html_escape(meal_type.title())}</a>')
    if week_plan.cook_events:
        nav_links.append('<a href="#shopping">Shopping</a>')

    failures_html = ""
    if week_plan.failures:
        items = "".join(
            f"<li>{html_escape(slot_label(key))}: {html_escape(error)}</li>"
            for key, error in week_plan.failures.items()
        )
        failures_html = f'<section class="failures"><h2>Not generated</h2><ul>{items}</ul></section>'

    day_sections = "".join(_html_day_section(week_plan, by_slot, day) for day in week_plan.days)

    recipe_sections = "".join(
        f'<section class="recipes" id="recipes-{_html_slug(meal_type)}">'
        f"<h2>{html_escape(meal_type.title())} Meals</h2>"
        f"{''.join(_html_recipe_card(event) for event in events)}"
        "</section>"
        for meal_type, events in _recipes_by_category(week_plan)
    )

    prep_html = _html_prep_section(week_plan.sunday_prep_session) if week_plan.sunday_prep_session else ""
    shopping_html = _html_shopping_section(week_plan, pantry)

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">\n'
        f"<title>Weekly Menu</title>\n<style>{_HTML_STYLE}</style>\n</head>\n<body>\n"
        '<header class="page-header"><h1>Weekly Menu</h1>'
        f'<p class="subtle">{html_escape(generated)}</p></header>\n'
        f'<nav class="topnav">{"".join(nav_links)}</nav>\n'
        f"<main>\n{failures_html}\n"
        f'<section class="summary" id="summary">{day_sections}</section>\n'
        f"{prep_html}\n{recipe_sections}\n{shopping_html}\n"
        "</main>\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------
# schema.org/Recipe JSON-LD export — for URL-import into Cronometer
#
# A third format on the same `week_plan.cook_events` walk the PDF and mobile
# HTML use for their recipe pages (`_recipes_by_category`), so all three agree
# on which recipes exist. This one emits one standalone HTML page per cook
# event carrying a `<script type="application/ld+json">` Recipe block —
# Cronometer's URL importer resolves each ingredient *string* against its own
# food database, so the exporter's whole job is to emit good strings.
#
# The 2.1a probe (PROMPT-5 Part 0) established two things this depends on:
#   - the page must be served as `text/html` (a raw gist is `text/plain` and
#     is rejected); hosting is out of scope here (PROMPT-5 Part 2) — this only
#     writes the files;
#   - `shopping.display_name` is the wrong normaliser (its canonical collapse
#     and parenthetical stripping resolved to the wrong food); `matcher_name`
#     is the one built for this.
#
# No `nutrition` block, ever: Cronometer computing its own macros from a real
# database is the entire point — a second set of figures here is `design-00`
# F5's "never store a verdict", and Part 3's calibration value is precisely
# that independent comparison.
# --------------------------------------------------------------------------

RECIPE_EXPORT_DIRNAME = "recipe_exports"

_RECIPE_PAGE_STYLE = (
    "body{font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;color:#1f2937}"
    "h1{font-size:1.5rem;margin:0 0 .25rem}.sub{color:#6b7280;margin:0 0 1.5rem}"
    "h2{font-size:1rem;margin:1.5rem 0 .5rem}li{margin:.3rem 0}"
)


def build_recipe_jsonld(event: CookEvent) -> dict:
    """One cook event as a `schema.org/Recipe` dict, ready for `json.dumps`.

    `recipeYield` is the batch the cook event is scaled to — `event.portions`,
    which equals `recipe.servings` after `build_cook_event` — not one serving.
    Cronometer divides by yield exactly as `Recipe.per_serving_macros` does,
    so a bulk cook feeding three dinners is not reported at 3x.

    Ingredient strings are `"<grams> g <matcher_name>"`. Water and the other
    `NON_SHOPPING_INGREDIENTS` are dropped — a 0-kcal line that resolves to
    "Water, Bottled, Generic" (as "Ground black pepper" did in the probe) is
    clutter, not information.
    """
    recipe = event.recipe
    ingredient_lines = [
        f"{ingredient.quantity_g:.0f} g {matcher_name(ingredient.name)}"
        for ingredient in recipe.ingredients
        if normalize_name(ingredient.name) not in NON_SHOPPING_INGREDIENTS
    ]
    data: dict = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.name,
        "recipeYield": f"{event.portions} servings",
        "recipeIngredient": ingredient_lines,
    }
    if recipe.instructions:
        data["recipeInstructions"] = [
            {"@type": "HowToStep", "text": step} for step in recipe.instructions
        ]
    return data


def render_recipe_page(event: CookEvent) -> str:
    """One cook event as a standalone HTML page carrying its Recipe JSON-LD.

    The visible body shows the exact strings that are in the JSON-LD (so the
    file reads as what Cronometer will parse), and an HTML comment carries the
    app's own per-serving macros — kept *out* of the JSON-LD on purpose — for
    the Part 3 cross-check against whatever Cronometer computes.
    """
    data = build_recipe_jsonld(event)
    recipe = event.recipe
    # `</script>` appearing inside the serialised data (a dish name, a step)
    # would close the block early; escaping every `<` is the standard
    # mitigation for JSON embedded in an HTML `<script>`.
    jsonld = json.dumps(data, indent=2, ensure_ascii=False).replace("<", "\\u003c")

    ingredients = "".join(
        f"<li>{html_escape(line)}</li>" for line in data["recipeIngredient"]
    )
    steps = "".join(
        f"<li>{html_escape(step['text'])}</li>"
        for step in data.get("recipeInstructions", [])
    )
    method_html = f"<h2>Method</h2>\n<ol>{steps}</ol>\n" if steps else ""

    macros = recipe.per_serving_macros
    macro_note = "App per-serving macros (not sent to Cronometer): " + ", ".join(
        f"{key} {value:.1f}" for key, value in macros.items()
    )

    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html_escape(recipe.name)}</title>\n"
        f"<style>{_RECIPE_PAGE_STYLE}</style>\n"
        f'<script type="application/ld+json">\n{jsonld}\n</script>\n'
        "</head>\n<body>\n"
        f"<h1>{html_escape(recipe.name)}</h1>\n"
        f'<p class="sub">Makes {event.portions} servings — '
        f"{html_escape(event.day)} {html_escape(event.meal_type)}.</p>\n"
        f"<h2>Ingredients</h2>\n<ul>{ingredients}</ul>\n"
        f"{method_html}"
        f"<!-- {html_escape(macro_note)} -->\n"
        "</body>\n</html>\n"
    )


def _recipe_export_filename(event: CookEvent) -> str:
    """`<slot>-<recipe-slug>.html` — slot-prefixed so a recipe cooked twice in
    one week (a repeated breakfast) does not collide on a single filename."""
    slug = re.sub(r"[^a-z0-9]+", "-", event.recipe.name.lower()).strip("-")
    return f"{_html_slug(event.slot_id)}-{slug or 'recipe'}.html"


def write_recipe_exports(
    week_plan: WeekPlan, out_dir: Optional[str] = None
) -> Tuple[List[str], Dict[str, str]]:
    """Write one JSON-LD recipe page per cook event, for hand-hosting and
    URL-import into Cronometer. Returns `(paths_written, {slot_id: error})`.

    `out_dir` defaults to `data/recipe_exports/` — app-written, gitignored
    (`data/*`), never a tracked directory. A recipe that fails to render is
    recorded and skipped: one bad recipe must not cost the rest of the week's
    export, the same policy as `generate_week_plan`'s per-meal-type catch.
    """
    target = out_dir or os.path.join(DATA_DIR, RECIPE_EXPORT_DIRNAME)
    os.makedirs(target, exist_ok=True)
    written: List[str] = []
    failed: Dict[str, str] = {}
    for event in week_plan.cook_events:
        try:
            path = os.path.join(target, _recipe_export_filename(event))
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(render_recipe_page(event))
            written.append(path)
        except Exception as exc:  # one bad recipe must not stop the others
            failed[event.slot_id] = str(exc)
    return written, failed

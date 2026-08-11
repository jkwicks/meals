"""Formats a generated week into a printable menu — Markdown text and a
magazine-style PDF (CSIRO Total Wellbeing Diet inspired: restrained dark-ink
typography, a teal-header day-by-day grid, a tickable prep checklist, a
single hairline-ruled recipe page per meal grouped by meal type, and a
catalog-style shopping list).

Both walk `WeekPlan.slots` (one `SlotSpec` per eating slot) resolved against
`WeekPlan.by_slot()` (cook events), the same source `WeekPlan.day_slot_macros`
reads — not `PlannerState`/`SlotView`, so this module has no UI dependency
and works the same from the NiceGUI drawer today or a future CLI flag.

`build_week_menu_pdf` needs `reportlab` (pure Python, no system libraries —
unlike `weasyprint`, which needs Cairo/Pango, it installs cleanly into this
project's venv with a plain `pip install`); it's a hard requirement (see
`requirements.txt`), and `ui_app.py` already needs it for the "Download PDF
Menu" button to exist, so importing it at module level costs nothing the app
doesn't already pay.
"""

import io
from typing import Dict, List, Optional
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
from shopping import ShoppingItem, aggregate_cook_events, format_quantity, format_shopping_list_text
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
    return (
        f"{macros['calories']:.0f} kcal · {macros['protein_g']:.0f}g P · "
        f"{macros['net_carbs_g']:.0f}g C · {macros['fat_g']:.0f}g F"
    )


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
    return (
        f"<b>{lead}</b> Each serving provides {macros['calories']:.0f} kcal, "
        f"{macros['protein_g']:.0f}g protein, {macros['net_carbs_g']:.0f}g carbs, "
        f"{macros['fat_g']:.0f}g fat."
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


def _shopping_list_pages(week_plan: WeekPlan) -> list:
    """The whole week's shopping, grouped by department in a
    catalog-style grid, followed by a plain-text page built from the same
    `format_shopping_list_text` the CLI and shopping-drawer copy buttons
    use — so the wording can't drift between the styled table and the copy
    someone pastes into their phone at the shop.
    """
    shopping_list = aggregate_cook_events(week_plan.cook_events, week_plan.days)

    flow: list = [
        Paragraph("Shopping List", STYLES["Heading1"]),
        Paragraph(
            escape(f"Everything for the week, grouped by department — {len(shopping_list.items())} items."),
            STYLES["SubText"],
        ),
        Spacer(1, 10),
    ]

    for department in sorted(shopping_list.categories):
        flow.append(Paragraph(escape(department), STYLES["SectionHeading"]))
        flow.append(_department_item_table(shopping_list.categories[department]))

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


def build_week_menu_pdf(week_plan: WeekPlan) -> bytes:
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
        story.extend(_shopping_list_pages(week_plan))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()

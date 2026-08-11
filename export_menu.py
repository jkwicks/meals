"""Formats a generated week into a printable menu — Markdown text and a PDF.

Both walk `WeekPlan.slots` (one `SlotSpec` per eating slot) resolved against
`WeekPlan.by_slot()` (cook events), the same source `WeekPlan.day_slot_macros`
reads — not `PlannerState`/`SlotView`, so this module has no UI dependency
and works the same from the NiceGUI drawer today or a future CLI flag.

`build_week_menu_pdf` needs `reportlab` (pure Python, no system libraries —
unlike `weasyprint`, which needs Cairo/Pango, it installs cleanly into this
project's venv with a plain `pip install`).
"""

from typing import Dict, List, Optional
from xml.sax.saxutils import escape

from planner import CookEvent, Recipe, WeekPlan
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, humanize, slot_label

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
    """Meal types in first-seen slot order — the summary grid's row order.

    Not a fixed constant: a week's slots are the source of truth for which
    meal types actually appear, so a config with a non-default `meal_types`
    list (or no snack slots at all) still produces the right rows.
    """
    seen: List[str] = []
    for slot in week_plan.slots:
        if slot.meal_type not in seen:
            seen.append(slot.meal_type)
    return seen


def _summary_cell(entry: Optional[dict], cell_style):
    from reportlab.platypus import Paragraph

    if entry is None:
        return Paragraph("", cell_style)

    text = entry["dish"]
    if entry["note"]:
        text += f" ({entry['note']})"
    if entry["macros"] is None:
        return Paragraph(f"<i>{escape(text)}</i>", cell_style)
    return Paragraph(escape(text), cell_style)


def _summary_table(week_plan: WeekPlan, by_slot: Dict[str, CookEvent], styles):
    """Page 1: a 7-day x meal-type grid of dish names, with a daily-macro-total row.

    Deliberately doesn't repeat per-meal macros here — that detail lives on
    each recipe's own page. This page answers "what am I eating this week"
    at a glance, not "what's in it".
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Table, TableStyle

    header_style = ParagraphStyle(
        "GridHeader",
        parent=styles["BodyText"],
        fontSize=9.5,
        leading=12,
        textColor=colors.white,
        alignment=1,
        fontName="Helvetica-Bold",
    )
    label_style = ParagraphStyle(
        "GridLabel", parent=styles["BodyText"], fontSize=9, leading=11, fontName="Helvetica-Bold"
    )
    cell_style = ParagraphStyle("GridCell", parent=styles["BodyText"], fontSize=8, leading=10)
    totals_style = ParagraphStyle(
        "GridTotals",
        parent=styles["BodyText"],
        fontSize=7.5,
        leading=9.5,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1f2937"),
    )

    meal_types = _grid_meal_types(week_plan)
    entries_by_day = {
        day: {entry["meal_type"]: entry for entry in _day_entries(week_plan, by_slot, day)}
        for day in week_plan.days
    }

    rows = [[Paragraph("", header_style)] + [Paragraph(day, header_style) for day in week_plan.days]]
    for meal_type in meal_types:
        row = [Paragraph(meal_type.title(), label_style)]
        for day in week_plan.days:
            row.append(_summary_cell(entries_by_day[day].get(meal_type), cell_style))
        rows.append(row)

    totals_row = [Paragraph("Daily Total", label_style)]
    for day in week_plan.days:
        totals_row.append(Paragraph(escape(_macro_text(week_plan.day_slot_macros(day))), totals_style))
    rows.append(totals_row)

    content_width = 180 * mm
    label_width = 22 * mm
    day_width = (content_width - label_width) / len(week_plan.days)
    table = Table(rows, colWidths=[label_width] + [day_width] * len(week_plan.days), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f3f4f6")),
                ("BACKGROUND", (1, -1), (-1, -1), colors.HexColor("#eef2f7")),
                ("ROWBACKGROUNDS", (1, 1), (-1, -2), [colors.white, colors.HexColor("#f9fafb")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#1f2937")),
                ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#9ca3af")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


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


def _recipe_page(event: CookEvent, styles) -> list:
    """One recipe's dedicated page: title, ingredients, instructions."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        PageBreak,
        Paragraph,
        Spacer,
    )

    eyebrow_style = ParagraphStyle(
        "Eyebrow",
        parent=styles["BodyText"],
        fontSize=9,
        textColor=colors.HexColor("#6b7280"),
        spaceAfter=2,
    )
    title_style = ParagraphStyle(
        "RecipeTitle",
        parent=styles["Title"],
        alignment=0,
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1f2937"),
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "RecipeMeta", parent=styles["BodyText"], fontSize=9.5, textColor=colors.HexColor("#4b5563")
    )
    section_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading3"],
        fontSize=12,
        spaceBefore=14,
        spaceAfter=4,
        textColor=colors.HexColor("#1f2937"),
    )
    body_style = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10, leading=14)
    note_style = ParagraphStyle(
        "PrepNote",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
        textColor=colors.grey,
        spaceBefore=12,
    )

    recipe = event.recipe
    flow = [
        Paragraph(escape(_recipe_meta_line(event)), eyebrow_style),
        Paragraph(escape(recipe.name), title_style),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#d1d5db"), spaceAfter=6),
    ]

    feeds = _feeds_note(event)
    if feeds:
        flow.append(Paragraph(escape(feeds), meta_style))
    flow.append(Paragraph(escape(_macro_text(recipe.per_serving_macros) + " per serving"), meta_style))

    flow.append(Paragraph("Ingredients", section_style))
    flow.append(
        ListFlowable(
            [
                ListItem(Paragraph(escape(_ingredient_line(ingredient)), body_style))
                for ingredient in recipe.ingredients
            ],
            bulletType="bullet",
            leftIndent=14,
            bulletFontSize=8,
        )
    )

    flow.append(Paragraph("Instructions", section_style))
    flow.append(
        ListFlowable(
            [
                ListItem(Paragraph(escape(step), body_style))
                for step in recipe.instructions
            ],
            bulletType="1",
            leftIndent=16,
            bulletFontSize=9,
        )
    )

    if recipe.prep_notes:
        flow.append(Paragraph(escape(recipe.prep_notes), note_style))

    flow.append(Spacer(1, 4))
    flow.append(PageBreak())
    return flow


def build_week_menu_pdf(week_plan: WeekPlan) -> bytes:
    """The whole week as a PDF: a page-1 summary grid, then one page per recipe.

    Returns bytes rather than writing to disk: the caller (the NiceGUI
    shopping drawer today) hands this straight to `ui.download`, and nothing
    here needs to know whether it's a browser response or a file on disk.
    """
    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    note_style = ParagraphStyle("Note", parent=styles["BodyText"], fontSize=8, textColor=colors.grey)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Weekly Menu",
    )

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#9ca3af"))
        canvas.drawRightString(doc.pagesize[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.drawString(18 * mm, 10 * mm, "Weekly Menu")
        canvas.restoreState()

    by_slot = week_plan.by_slot()

    # --- Page 1: weekly summary grid ---
    story = [Paragraph("Weekly Menu", styles["Title"])]
    if week_plan.generated_at:
        story.append(
            Paragraph(f"Generated {week_plan.generated_at[:16].replace('T', ' ')}", note_style)
        )
    story.append(Spacer(1, 10))
    story.append(_summary_table(week_plan, by_slot, styles))

    if week_plan.failures:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Not generated", styles["Heading3"]))
        for key, error in week_plan.failures.items():
            story.append(Paragraph(escape(f"{slot_label(key)}: {error}"), note_style))

    story.append(PageBreak())

    # --- One page per recipe actually being cooked ---
    for event in week_plan.cook_events:
        story.extend(_recipe_page(event, styles))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()

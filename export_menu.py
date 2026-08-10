"""Formats a generated week into a printable menu — Markdown text and a PDF.

Both walk `WeekPlan.slots` (one `SlotSpec` per eating slot) resolved against
`WeekPlan.by_slot()` (cook events), the same source `planner.day_slot_macros`
reads — not `PlannerState`/`SlotView`, so this module has no UI dependency
and works the same from the NiceGUI drawer today or a future CLI flag.

`build_week_menu_pdf` needs `reportlab` (pure Python, no system libraries —
unlike `weasyprint`, which needs Cairo/Pango, it installs cleanly into this
project's venv with a plain `pip install`).
"""

from typing import Dict, List, Optional
from xml.sax.saxutils import escape

from planner import CookEvent, Recipe, WeekPlan, day_slot_macros
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, slot_label

MACRO_ROW_LABELS = ["kcal", "P", "C", "F"]


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
        return {
            "meal_type": slot.meal_type,
            "dish": "Not generated",
            "macros": None,
            "note": week_plan.failures.get(slot.day),
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
        totals = day_slot_macros(week_plan, day)
        lines.append(f"- **Day total** — {_macro_text(totals)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _pdf_row(entry: dict, cell_style) -> list:
    from reportlab.platypus import Paragraph

    dish_text = entry["dish"]
    if entry["note"]:
        dish_text += f" ({entry['note']})"

    if entry["macros"] is None:
        dish = Paragraph(f"<i>{escape(dish_text)}</i>", cell_style)
        return [entry["meal_type"].title(), dish, "", "", "", ""]

    macros = entry["macros"]
    return [
        entry["meal_type"].title(),
        Paragraph(escape(dish_text), cell_style),
        f"{macros['calories']:.0f}",
        f"{macros['protein_g']:.0f}g",
        f"{macros['net_carbs_g']:.0f}g",
        f"{macros['fat_g']:.0f}g",
    ]


def build_week_menu_pdf(week_plan: WeekPlan) -> bytes:
    """The whole week as a PDF — one table per day, in week order.

    Returns bytes rather than writing to disk: the caller (the NiceGUI
    shopping drawer today) hands this straight to `ui.download`, and nothing
    here needs to know whether it's a browser response or a file on disk.
    """
    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    day_style = ParagraphStyle("Day", parent=styles["Heading2"], spaceBefore=10, spaceAfter=4)
    cell_style = ParagraphStyle("Cell", parent=styles["BodyText"], fontSize=9, leading=11)
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

    story = [Paragraph("Weekly Menu", styles["Title"])]
    if week_plan.generated_at:
        story.append(
            Paragraph(f"Generated {week_plan.generated_at[:16].replace('T', ' ')}", note_style)
        )
    story.append(Spacer(1, 8))

    by_slot = week_plan.by_slot()
    header_row = ["Meal", "Dish"] + MACRO_ROW_LABELS
    for day in week_plan.days:
        rows = [header_row]
        for entry in _day_entries(week_plan, by_slot, day):
            rows.append(_pdf_row(entry, cell_style))
        totals = day_slot_macros(week_plan, day)
        rows.append(
            [
                "Day total",
                "",
                f"{totals['calories']:.0f}",
                f"{totals['protein_g']:.0f}g",
                f"{totals['net_carbs_g']:.0f}g",
                f"{totals['fat_g']:.0f}g",
            ]
        )

        table = Table(
            rows, colWidths=[22 * mm, 90 * mm, 16 * mm, 14 * mm, 14 * mm, 14 * mm], repeatRows=1
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f3f4f6")),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(KeepTogether([Paragraph(day, day_style), table]))

    if week_plan.failures:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Not generated", styles["Heading3"]))
        for day, error in week_plan.failures.items():
            story.append(Paragraph(escape(f"{day}: {error}"), note_style))

    doc.build(story)
    return buffer.getvalue()

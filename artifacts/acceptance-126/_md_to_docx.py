"""Render the markdown report to a .docx so it opens by double-click on Windows.

Handles exactly the subset the report uses: h1-h3, paragraphs, bullet and numbered lists, pipe
tables, fenced code, blockquotes, horizontal rules, and inline **bold** / `code`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


def add_inline(paragraph, text: str) -> None:
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            run = paragraph.add_run(piece[2:-2])
            run.bold = True
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            run = paragraph.add_run(piece[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0xA0, 0x30, 0x30)
        else:
            paragraph.add_run(piece)


def flush_table(doc, rows: list[list[str]]) -> None:
    if not rows:
        return
    width = max(len(row) for row in rows)
    table = doc.add_table(rows=0, cols=width)
    table.style = "Table Grid"
    for index, row in enumerate(rows):
        cells = table.add_row().cells
        for col in range(width):
            text = row[col] if col < len(row) else ""
            paragraph = cells[col].paragraphs[0]
            add_inline(paragraph, text)
            for run in paragraph.runs:
                run.font.size = Pt(9)
                if index == 0:
                    run.bold = True
    doc.add_paragraph()


def render(markdown: str) -> Document:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    lines = markdown.splitlines()
    i = 0
    table: list[list[str]] = []
    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            i += 1
            block = []
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            paragraph = doc.add_paragraph()
            paragraph.style = doc.styles["No Spacing"]
            run = paragraph.add_run("\n".join(block))
            run.font.name = "Consolas"
            run.font.size = Pt(8.5)
            i += 1
            continue

        if line.startswith("|"):
            if table:
                table.append([cell.strip() for cell in line.strip().strip("|").split("|")])
            else:
                table = [[cell.strip() for cell in line.strip().strip("|").split("|")]]
            i += 1
            continue
        if table:
            if not all(set(cell) <= set("-: ") and cell for cell in table[-1]):
                pass
            cleaned = [row for row in table if not all(set(cell) <= set("-: ") and cell for cell in row)]
            flush_table(doc, cleaned)
            table = []

        if line.strip() == "---":
            paragraph = doc.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run("• • •")
            run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
            i += 1
            continue

        if line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("> "):
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.left_indent = Pt(18)
            add_inline(paragraph, line[2:])
            for run in paragraph.runs:
                run.italic = True
        elif re.match(r"^\d+\.\s", line):
            paragraph = doc.add_paragraph(style="List Number")
            add_inline(paragraph, re.sub(r"^\d+\.\s", "", line))
        elif line.startswith("- "):
            paragraph = doc.add_paragraph(style="List Bullet")
            add_inline(paragraph, line[2:])
        elif line.strip():
            paragraph = doc.add_paragraph()
            add_inline(paragraph, line.strip())
        i += 1

    if table:
        cleaned = [row for row in table if not all(set(cell) <= set("-: ") and cell for cell in row)]
        flush_table(doc, cleaned)
    return doc


def main() -> int:
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    render(source.read_text(encoding="utf-8")).save(str(target))
    print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Render the session report to one self-contained HTML file.

A narrow Markdown subset is enough for this document: headings, paragraphs, tables, bullet and
numbered lists, fenced code, block quotes, rules, and inline bold/code/links. Anything else is
passed through as text rather than silently dropped.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

INLINE = (
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2">\1</a>'),
)


def inline(text: str) -> str:
    escaped = html.escape(text, quote=False)
    for pattern, replacement in INLINE:
        escaped = pattern.sub(replacement, escaped)
    return escaped


def split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def is_separator(line: str) -> bool:
    cells = split_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells)


def render(source: str) -> str:
    lines = source.splitlines()
    out: list[str] = []
    index = 0
    in_code = False
    code: list[str] = []
    list_kind: str | None = None
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        # A wrapped paragraph is one paragraph: without this, a bold run split across source
        # lines renders its asterisks literally.
        nonlocal paragraph
        if paragraph:
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = []
                in_code = False
            else:
                close_list()
                in_code = True
            index += 1
            continue
        if in_code:
            code.append(line)
            index += 1
            continue

        if not stripped:
            flush_paragraph()
            close_list()
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < len(lines) and is_separator(lines[index + 1]):
            flush_paragraph()
            close_list()
            header = split_row(stripped)
            index += 2
            body: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                body.append(split_row(lines[index]))
                index += 1
            out.append("<table><thead><tr>")
            out.extend(f"<th>{inline(cell)}</th>" for cell in header)
            out.append("</tr></thead><tbody>")
            for row in body:
                out.append("<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>")
            out.append("</tbody></table>")
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if re.fullmatch(r"-{3,}", stripped):
            flush_paragraph()
            close_list()
            out.append("<hr>")
            index += 1
            continue

        if stripped.startswith("> "):
            flush_paragraph()
            close_list()
            out.append(f"<blockquote>{inline(stripped[2:])}</blockquote>")
            index += 1
            continue

        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        ordered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet or ordered:
            flush_paragraph()
            kind = "ul" if bullet else "ol"
            if list_kind != kind:
                close_list()
                out.append(f"<{kind}>")
                list_kind = kind
            out.append(f"<li>{inline((bullet or ordered).group(1))}</li>")
            index += 1
            continue

        if list_kind and out and out[-1].endswith("</li>"):
            # A wrapped list item is the same item, not a paragraph after the list.
            out[-1] = out[-1][: -len("</li>")] + " " + inline(stripped) + "</li>"
            index += 1
            continue

        close_list()
        paragraph.append(stripped)
        index += 1

    flush_paragraph()
    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_paragraph()
    close_list()
    return "\n".join(out)


STYLE = """
:root { color-scheme: light dark; }
body { margin: 0 auto; max-width: 1080px; padding: 48px 28px 96px;
  font: 16px/1.65 "Segoe UI", system-ui, -apple-system, sans-serif; color: #1b1f24; background: #ffffff; }
h1 { font-size: 2.1rem; line-height: 1.2; margin: 0 0 8px; letter-spacing: -0.02em; }
h2 { font-size: 1.45rem; margin: 42px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #e3e7ec; }
h3 { font-size: 1.14rem; margin: 28px 0 10px; }
h4 { font-size: 1rem; margin: 22px 0 8px; }
p { margin: 10px 0; }
table { border-collapse: collapse; width: 100%; margin: 14px 0 22px; font-size: 0.94rem; }
th, td { border: 1px solid #dfe3e8; padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: #f4f6f8; font-weight: 600; }
tbody tr:nth-child(even) { background: #fbfcfd; }
code { font-family: "Cascadia Mono", Consolas, monospace; font-size: 0.88em;
  background: #f2f4f7; padding: 1px 5px; border-radius: 4px; }
pre { background: #0f172a; color: #e2e8f0; padding: 16px 18px; border-radius: 10px; overflow-x: auto; }
pre code { background: none; color: inherit; padding: 0; font-size: 0.86rem; }
blockquote { margin: 16px 0; padding: 10px 16px; border-left: 4px solid #94a3b8;
  background: #f8fafc; color: #334155; }
hr { border: 0; border-top: 1px solid #e3e7ec; margin: 34px 0; }
ul, ol { margin: 10px 0 14px; padding-left: 26px; }
li { margin: 5px 0; }
a { color: #0b62d6; }
@media (prefers-color-scheme: dark) {
  body { background: #0b0f14; color: #dbe3ec; }
  h2 { border-color: #1f2a36; }
  th { background: #131b24; }
  th, td { border-color: #22303d; }
  tbody tr:nth-child(even) { background: #0f151d; }
  code { background: #151d27; }
  blockquote { background: #111922; color: #b6c2cf; border-color: #33506b; }
  a { color: #74b0ff; }
}
@media print { body { max-width: none; } pre { white-space: pre-wrap; } }
"""


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: report-to-html.py <input.md> <output.html>", file=sys.stderr)
        return 2
    source = Path(sys.argv[1]).read_text(encoding="utf-8")
    title = next(
        (line.lstrip("# ").strip() for line in source.splitlines() if line.startswith("# ")),
        "Report",
    )
    body = render(source)
    document = (
        "<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title)}</title>\n<style>{STYLE}</style>\n</head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )
    Path(sys.argv[2]).write_text(document, encoding="utf-8")
    print(f"written {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

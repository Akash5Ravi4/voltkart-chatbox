import io
import json
import re
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from app.charts import render_chart

ACCENT = colors.HexColor("#2f5bea")
MUTED = colors.HexColor("#6b6b70")
LINE = colors.HexColor("#d8d8d4")
SHADE = colors.HexColor("#f3f3f0")

# the built-in PDF fonts can't draw these, so swap them first
REPLACEMENTS = {
    "₹": "Rs ", "→": "->", "←": "<-", "≥": ">=", "≤": "<=",
    "✓": "", "\u00a0": " ", "\u200b": "",
}


def clean(text) -> str:
    text = str(text)
    for a, b in REPLACEMENTS.items():
        text = text.replace(a, b)
    return text.encode("cp1252", errors="replace").decode("cp1252")


def esc(text) -> str:
    return escape(clean(text))


def inline(text) -> str:
    t = esc(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", t)
    return t


def code_para(text, style) -> Paragraph:
    """Wrapping, monospaced block that keeps line breaks and indentation."""
    lines = []
    for line in esc(text).split("\n"):
        n = len(line) - len(line.lstrip(" "))
        lines.append("&nbsp;" * n + line.lstrip(" "))
    return Paragraph("<br/>".join(lines), style)


def make_styles() -> dict:
    def s(name, **kw):
        return ParagraphStyle(name, **kw)

    return {
        "title": s("title", fontName="Helvetica-Bold", fontSize=16, leading=20, spaceAfter=4),
        "meta": s("meta", fontName="Helvetica", fontSize=9, textColor=MUTED, leading=12),
        "who": s("who", fontName="Helvetica-Bold", fontSize=10, leading=13,
                 spaceBefore=12, spaceAfter=3, keepWithNext=1),
        "body": s("body", fontName="Helvetica", fontSize=10, leading=14, spaceAfter=4),
        "cell": s("cell", fontName="Helvetica", fontSize=8.5, leading=11),
        "cell_b": s("cell_b", fontName="Helvetica-Bold", fontSize=8.5, leading=11),
        "src_label": s("src_label", fontName="Helvetica-Bold", fontSize=9,
                       textColor=ACCENT, spaceBefore=4, spaceAfter=2, leftIndent=10),
        "cite_title": s("cite_title", fontName="Helvetica-Bold", fontSize=8.5,
                        leading=11, leftIndent=10, spaceBefore=4),
        "cite_meta": s("cite_meta", fontName="Helvetica", fontSize=8, leading=10.5,
                       textColor=MUTED, leftIndent=10),
        "code": s("code", fontName="Courier", fontSize=7.5, leading=9.5, leftIndent=10,
                  backColor=SHADE, borderPadding=3, spaceBefore=3, spaceAfter=4),
        "quote": s("quote", fontName="Helvetica-Oblique", fontSize=8, leading=10.5,
                   leftIndent=14, spaceAfter=2),
        "small": s("small", fontName="Helvetica", fontSize=7.5, textColor=MUTED,
                   leftIndent=10, spaceAfter=3),
    }


# ---------------------------------------------------------------- tables

def grid_table(rows, width, cell, head) -> Table:
    ncols = max(len(r) for r in rows)
    rows = [list(r) + [""] * (ncols - len(r)) for r in rows]
    data = [
        [Paragraph(inline(c), head if ri == 0 else cell) for c in r]
        for ri, r in enumerate(rows)
    ]
    t = Table(data, colWidths=[width / ncols] * ncols, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), SHADE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def is_separator(cells: list[str]) -> bool:
    filled = [c for c in cells if c]
    return bool(filled) and all(re.fullmatch(r":?-+:?", c) for c in filled)


# ---------------------------------------------------------------- markdown

def markdown_flowables(text: str, st: dict, width: float) -> list:
    out, para = [], []
    lines = (text or "").splitlines()

    def flush():
        if para:
            out.append(Paragraph("<br/>".join(inline(l) for l in para), st["body"]))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = split_row(lines[i])
                if not is_separator(cells):
                    rows.append(cells)
                i += 1
            if rows:
                out.append(grid_table(rows, width, st["cell"], st["cell_b"]))
                out.append(Spacer(1, 4))
            continue

        if not line.strip():
            flush()
        else:
            h = re.match(r"^#{1,6}\s+(.*)", line)
            b = re.match(r"^\s*[-*]\s+(.*)", line)
            if h:
                para.append("**" + h.group(1) + "**")
            elif b:
                para.append("• " + b.group(1))
            else:
                para.append(line)
        i += 1

    flush()
    return out or [Paragraph(" ", st["body"])]


# ---------------------------------------------------------------- citations

def citation_flowables(c: dict, st: dict, width: float) -> list:
    out = []
    if c.get("type") == "table":
        tables = ", ".join(c.get("tables") or [])
        out.append(Paragraph(esc(f"[{c.get('ref')}] Table query - {tables}"), st["cite_title"]))
        out.append(code_para(c.get("sql", ""), st["code"]))
        cols, rows = c.get("columns") or [], (c.get("rows") or [])[:15]
        if cols and rows:
            data = [cols] + [["" if v is None else str(v) for v in r] for r in rows]
            out.append(grid_table(data, width, st["cell"], st["cell_b"]))
            total = c.get("row_count", len(rows))
            if total > len(rows):
                out.append(Paragraph(f"Showing {len(rows)} of {total} rows", st["small"]))
    elif c.get("type") == "web":
        out.append(Paragraph(esc(f"[{c.get('ref')}] Web - {c.get('title', '')}"), st["cite_title"]))
        out.append(Paragraph(esc(c.get("url", "")), st["cite_meta"]))
        text = (c.get("text") or "").strip()
        if len(text) > 500:
            text = text[:500] + "..."
        if text:
            out.append(Paragraph(esc(text).replace("\n", "<br/>"), st["quote"]))
    return out


# ---------------------------------------------------------------- the PDF

def fmt_time(s: str, tz_minutes: int) -> str:
    try:
        d = datetime.strptime(s, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=tz_minutes)
    except (TypeError, ValueError):
        return str(s)
    sign = "+" if tz_minutes >= 0 else "-"
    h, m = divmod(abs(tz_minutes), 60)
    return f"{d:%d %b %Y, %H:%M} (UTC{sign}{h:02d}:{m:02d})"


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_chat_pdf(chat: dict, messages: list[dict], tz_minutes: int = 0) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=clean(chat.get("title") or "Chat"),
    )
    st = make_styles()
    width = doc.width

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    story = [
        Paragraph(esc(chat.get("title") or "Chat"), st["title"]),
        Paragraph(
            f"VOLTKART CHATBOX | {len(messages)} messages | exported {fmt_time(now, tz_minutes)}",
            st["meta"],
        ),
        Spacer(1, 4),
    ]

    for m in messages:
        is_user = m["role"] == "user"
        who = "You" if is_user else "Assistant"
        when = esc(fmt_time(m["created_at"], tz_minutes))
        ws = m.get("web_search")
        web = "" if ws is None else f" | Web search: {'ON' if ws else 'OFF'}"
        story.append(
            Paragraph(
                f"{who} <font color='#6b6b70' size='8'>&nbsp;&nbsp;{when}{web}</font>",
                st["who"],
            )
        )
        story.extend(markdown_flowables(m["content"], st, width))

        if not is_user:
            try:
                charts = json.loads(m.get("charts") or "[]")
            except (TypeError, ValueError):
                charts = []
            for ch in charts:
                try:
                    png = render_chart(ch)
                except Exception:
                    continue
                w = width * 0.92
                story.append(Image(io.BytesIO(png), width=w, height=w * 4.2 / 7.5))
                if ch.get("cite"):
                    story.append(Paragraph(f"Chart source: [{ch['cite']}]", st["small"]))

        if not is_user and m.get("citations"):
            try:
                cites = json.loads(m["citations"])
            except (TypeError, ValueError):
                cites = []
            if cites:
                story.append(Paragraph("Sources", st["src_label"]))
                for c in cites:
                    story.extend(citation_flowables(c, st, width - 10 * mm))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
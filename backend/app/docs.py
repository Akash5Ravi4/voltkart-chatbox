import re
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import fitz  # pymupdf
from dateutil import parser as dateparser
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)"
)
ISO_RE = re.compile(r"(?<!\d)(\d{4})[-_./](\d{1,2})[-_./](\d{1,2})(?!\d)")
COMPACT_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
DMY_RE = re.compile(
    rf"(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTHS}\.?,?\s+\d{{4}}", re.I
)
MDY_RE = re.compile(
    rf"{MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}", re.I
)
DATE_KEYWORDS = (
    "effective", "last updated", "updated", "version date",
    "issued", "revised", "dated", "date",
)


def find_dates(text: str) -> list[tuple[int, date]]:
    """All dates found in text as (position, date), in order of appearance."""
    found = []
    for rx in (ISO_RE, COMPACT_RE):
        for m in rx.finditer(text):
            try:
                found.append(
                    (m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                )
            except ValueError:
                pass
    for rx in (DMY_RE, MDY_RE):
        for m in rx.finditer(text):
            try:
                found.append((m.start(), dateparser.parse(m.group(0)).date()))
            except (ValueError, OverflowError):
                pass
    return sorted(found, key=lambda x: x[0])


def date_from_filename(name: str) -> str | None:
    found = find_dates(Path(name).stem)
    return found[0][1].isoformat() if found else None


def date_from_body(text: str) -> str | None:
    head = text[:3000]
    found = find_dates(head)
    if not found:
        return None
    # prefer a date that sits right after words like "Effective date"
    for pos, d in found:
        window = head[max(0, pos - 60):pos].lower()
        if any(k in window for k in DATE_KEYWORDS):
            return d.isoformat()
    return found[0][1].isoformat()


# ---------------------------------------------------------------- family

def family_from_filename(name: str) -> str:
    stem = Path(name).stem
    for rx in (ISO_RE, COMPACT_RE, DMY_RE, MDY_RE):
        stem = rx.sub(" ", stem)
    stem = re.sub(r"\bv\d+(\.\d+)*\b|\bversion\b", " ", stem, flags=re.I)
    stem = re.sub(r"[^a-zA-Z0-9]+", " ", stem).strip().lower()
    return stem or Path(name).stem.lower()


def resolve_family(conn, family: str) -> str:
    """Reuse an existing family name if a near-identical one exists."""
    for (existing,) in conn.execute("SELECT DISTINCT family FROM documents").fetchall():
        if SequenceMatcher(None, family, existing).ratio() >= 0.85:
            return existing
    return family


# ---------------------------------------------------------------- reading

def looks_like_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 70:
        return False
    if s.startswith("#"):
        return True
    if s.endswith((".", ",", ";")):
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 4 and s.isupper():
        return True
    return bool(
        re.match(r"^(section|article|part)?\s*\d+(\.\d+)*[.):]?\s+[A-Za-z]", s, re.I)
    )


def _pdf_sections(path) -> list[dict]:
    out = []
    with fitz.open(path) as pdf:
        for i, page in enumerate(pdf, start=1):
            text = page.get_text().strip()
            if text:
                out.append({"location": f"Page {i}", "text": text})
    return out


def _sections_from_lines(lines: list[str], first_label: str) -> list[dict]:
    """Group lines under headings. The heading line stays inside its section."""
    sections, label, buf = [], first_label, []

    def flush():
        text = "\n".join(buf).strip()
        if text:
            sections.append({"location": label, "text": text})

    for line in lines:
        if looks_like_heading(line):
            flush()
            label = line.strip().lstrip("#").strip()[:80]
            buf = [line.strip()]
        else:
            buf.append(line.rstrip())
    flush()
    return sections


def _docx_sections(path) -> list[dict]:
    doc = Document(path)
    lines: list[str] = []
    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            p = Paragraph(child, doc)
            text = p.text.strip()
            if not text:
                lines.append("")
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if style.lower().startswith("heading") and not text.startswith("#"):
                text = "# " + text  # lets the shared heading logic pick it up
            lines.append(text)
        elif child.tag.endswith("}tbl"):
            for row in Table(child, doc).rows:
                cells, prev = [], None
                for c in row.cells:
                    t = c.text.strip()
                    if t != prev:  # merged cells repeat; skip duplicates
                        cells.append(t)
                    prev = t
                lines.append(" | ".join(cells))
    return _sections_from_lines(lines, "Introduction")


def _txt_sections(path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    return _sections_from_lines(text.splitlines(), "Introduction")


def extract_sections(path, file_type: str) -> list[dict]:
    if file_type == "pdf":
        return _pdf_sections(path)
    if file_type == "docx":
        return _docx_sections(path)
    return _txt_sections(path)


# ---------------------------------------------------------------- chunking

def chunk_text(text: str, max_chars: int = 1000) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    for p in paras:
        if len(p) <= max_chars:
            units.append(p)
        else:
            units.extend(ln.strip() for ln in p.split("\n") if ln.strip())

    chunks, cur = [], ""
    for u in units:
        while len(u) > max_chars:  # a single very long line: hard split
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(u[:max_chars])
            u = u[max_chars:]
        if cur and len(cur) + len(u) + 1 > max_chars:
            chunks.append(cur)
            cur = u
        else:
            cur = f"{cur}\n{u}" if cur else u
    if cur:
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------- ingest

def ingest_document(conn, file_id: int, path, file_type: str, filename: str) -> dict:
    sections = extract_sections(path, file_type)
    full_text = "\n".join(s["text"] for s in sections)
    if not full_text.strip():
        raise ValueError("No readable text found (is this a scanned PDF?).")

    fdate = date_from_filename(filename)
    bdate = date_from_body(full_text)
    doc_date = fdate or bdate
    family = resolve_family(conn, family_from_filename(filename))

    conn.execute(
        "INSERT INTO documents (file_id, family, doc_date, filename_date, body_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (file_id, family, doc_date, fdate, bdate),
    )
    n = 0
    for s in sections:
        for i, c in enumerate(chunk_text(s["text"])):
            conn.execute(
                "INSERT INTO chunks (file_id, location, chunk_index, text) "
                "VALUES (?, ?, ?, ?)",
                (file_id, s["location"], i, c),
            )
            n += 1

    return {
        "family": family,
        "doc_date": doc_date,
        "filename_date": fdate,
        "body_date": bdate,
        "date_conflict": bool(fdate and bdate and fdate != bdate),
        "chunks": n,
    }
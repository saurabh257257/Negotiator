"""Read a component list from an uploaded CSV / Excel / PDF file. Files are processed in memory only."""
import csv
import io

from . import ai, catalog

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 500
NAME_H = ("component", "part", "item", "description", "material", "name")
QTY_H = ("qty", "quantity", "nos", "count", "pcs")
UNIT_H = ("unit", "uom")


class ImportProblem(Exception):
    """A user-facing reason the file could not be read."""


def _find(headers, keys):
    for i, h in enumerate(headers):
        if any(k in h for k in keys):
            return i
    return None


def _num(v):
    try:
        x = float(str(v).replace(",", "").strip())
        return x if 0 < x < 1e7 else None
    except ValueError:
        return None


def rows_to_components(rows) -> list[dict]:
    rows = [[("" if c is None else str(c).strip()) for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    name_i = qty_i = unit_i = None
    start = 0
    for idx, r in enumerate(rows[:10]):
        low = [c.lower() for c in r]
        ni = _find(low, NAME_H)
        if ni is not None:
            name_i, qty_i, unit_i, start = ni, _find(low, QTY_H), _find(low, UNIT_H), idx + 1
            break
    if name_i is None:  # no header row: first column is the name, second (if numeric) the quantity
        name_i, start = 0, 0
        qty_i = 1 if rows and len(rows[0]) > 1 and _num(rows[0][1]) is not None else None
    out = []
    for r in rows[start:]:
        name = r[name_i].strip() if name_i < len(r) else ""
        if not name or len(name) < 2:
            continue
        qty = _num(r[qty_i]) if qty_i is not None and qty_i < len(r) else None
        unit = r[unit_i] if unit_i is not None and unit_i < len(r) else ""
        out.append({"name": name[:200], "qty": qty or 1.0, "unit": unit[:20], "template": catalog.classify(name)})
        if len(out) >= MAX_ROWS:
            break
    return out


def parse_csv(data: bytes):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return rows_to_components(list(csv.reader(io.StringIO(text))))


def parse_xlsx(data: bytes):
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportProblem("That Excel file could not be opened. Save it as .xlsx and try again.") from exc
    for ws in wb.worksheets:
        rows = [list(r) for _, r in zip(range(2000), ws.iter_rows(values_only=True))]
        comps = rows_to_components(rows)
        if comps:
            return comps
    return []


def parse_pdf(data: bytes):
    if not ai.configured():
        raise ImportProblem("Reading PDF files needs AI, which is not configured yet. Upload an Excel or CSV "
                            "file instead, or add components by hand.")
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:30])
    except Exception as exc:
        raise ImportProblem("That PDF could not be read.") from exc
    if not text.strip():
        raise ImportProblem("No text found in the PDF (scanned PDFs are not supported yet).")
    try:
        return ai.extract_components(text)
    except ai.AIError as exc:
        raise ImportProblem(str(exc)) from exc


def parse_upload(filename: str, data: bytes) -> list[dict]:
    if len(data) > MAX_BYTES:
        raise ImportProblem("That file is larger than 5 MB.")
    ext = (filename or "").lower().rsplit(".", 1)[-1]
    parser = {"csv": parse_csv, "xlsx": parse_xlsx, "pdf": parse_pdf}.get(ext)
    if not parser:
        raise ImportProblem("Upload a .xlsx, .csv or .pdf file.")
    comps = parser(data)
    if not comps:
        raise ImportProblem("No components found in the file. Check that it has a column such as "
                            "'Component' or 'Description'.")
    return comps

"""Table extraction and serialisation (GFM when simple, HTML when complex)."""
from __future__ import annotations
import html


def _cell(c) -> str:
    return "" if c is None else " ".join(str(c).replace(" ", " ").split())


def to_gfm(rows: list[list[str]], header_rows: int = 1) -> str:
    rows = [[_cell(c).replace("|", "\\|") for c in r] for r in rows]
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    out = ["| " + " | ".join(head) + " |", "| " + " | ".join(["---"] * ncol) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def to_html(rows: list[list[str]], spans: dict | None = None, header_rows: int = 1) -> str:
    """spans: {(r,c): (rowspan, colspan)}; cells covered by a span are skipped via spans['skip']."""
    spans = spans or {}
    skip = spans.get("skip", set())
    out = ["<table>"]
    for r, row in enumerate(rows):
        tag = "th" if r < header_rows else "td"
        cells = []
        for c, v in enumerate(row):
            if (r, c) in skip:
                continue
            rs, cs = spans.get((r, c), (1, 1))
            attr = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs}"' if cs > 1 else "")
            cells.append(f"<{tag}{attr}>{html.escape(_cell(v))}</{tag}>")
        out.append("  <tr>" + "".join(cells) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


def analyse(rows: list[list]) -> dict:
    """Detect structural complexity: empty-cell gaps that look like merged cells, ragged rows."""
    clean = [[_cell(c) for c in r] for r in rows]
    ncols = {len(r) for r in clean}
    empty_ratio = sum(1 for r in clean for c in r if not c) / max(1, sum(len(r) for r in clean))
    first_row_empty = sum(1 for c in clean[0] if not c) if clean else 0
    ragged = len(ncols) > 1
    multirow_header = len(clean) > 1 and sum(1 for c in clean[1] if not c) > len(clean[1]) // 2 and first_row_empty > 0
    complex_ = ragged or multirow_header or empty_ratio > 0.35 or first_row_empty > 0
    return {"rows": len(clean), "cols": max(ncols) if ncols else 0, "empty_ratio": round(empty_ratio, 3),
            "ragged": ragged, "complex": complex_, "multirow_header": multirow_header}


def find_tables(page):
    """Return [(bbox, rows)] using PyMuPDF's table finder; ignore degenerate detections."""
    out = []
    try:
        tabs = page.find_tables()
    except Exception:
        return out
    for t in getattr(tabs, "tables", []):
        rows = t.extract()
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2 and any(_cell(c) for r in rows for c in r):
            out.append((tuple(t.bbox), rows))
    return out


def serialise(rows: list[list]) -> tuple[str, dict]:
    info = analyse(rows)
    if info["complex"]:
        return to_html(rows, header_rows=1), {**info, "format": "html"}
    return to_gfm(rows), {**info, "format": "gfm"}

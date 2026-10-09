"""Per-page passes 1-4, 8, 9: inspect, layout, reading order, extraction, tables, figures.
Output is a JSON-serialisable page state, saved after every page (resume unit)."""
from __future__ import annotations
import hashlib, re
from pathlib import Path
from . import layout, tables as tbl, cleanup, ocr, inline
from .util import write_json

MIN_NATIVE_CHARS = 25
FIG_MIN = 40.0


def _inside(inner, outer, frac=0.6) -> bool:
    ix0, iy0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix1, iy1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    a = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return a > 0 and ((ix1 - ix0) * (iy1 - iy0)) / a >= frac


def prepass_lines(page):
    """(text, y_center) for margin detection."""
    res = []
    d = page.get_text("dict")
    for b in d["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t:
                res.append((t, (l["bbox"][1] + l["bbox"][3]) / 2))
    return res


def _shaded_boxes(page):
    boxes = []
    pr = page.rect
    try:
        for dr in page.get_drawings():
            if dr.get("fill") and dr["rect"].width > 100 and dr["rect"].height > 30:
                r = dr["rect"]
                if r.width * r.height < pr.width * pr.height * 0.6:
                    boxes.append(tuple(r))
    except Exception:
        pass
    return boxes


def _render_clip(page, bbox, zoom=2.0) -> bytes:
    import pymupdf
    rect = pymupdf.Rect(bbox) & page.rect
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect, alpha=False)
    return pix.tobytes("png")


def process_page(page, ch_no: int, margins: set[str], assets_dir: Path, fig_counter: dict,
                 fig_hashes: set, ocr_enabled: bool = True, save_images: bool = True) -> dict:
    pno = page.number + 1
    pw, ph = page.rect.width, page.rect.height
    issues, dropped = [], []
    texts, images = layout.extract_blocks(page)

    # --- PASS 1: source inspection
    native_chars = sum(len(b.text) for b in texts)
    state = {"page": pno, "width": pw, "height": ph, "native_chars": native_chars, "ocr": None, "blocks": [],
             "dropped": [], "issues": issues, "src_text": ""}

    # --- OCR fallback only when native text is (nearly) absent
    if native_chars < MIN_NATIVE_CHARS and (images or native_chars == 0):
        full = (0.0, 0.0, pw, ph)
        if ocr_enabled and ocr.available():
            png = _render_clip(page, full, 3.0)
            text, conf = ocr.ocr_png(png)
            if text.strip():
                lines = [l for l in text.splitlines() if l.strip()]
                bl = {"id": f"ch{ch_no:03d}-p{pno:04d}-b001", "kind": "text", "bbox": list(full), "order": 0, "column": 0,
                      "lines": [{"text": l, "marked": l, "x0": 0, "y0": 0, "y1": 0, "size": 0, "bold": False, "italic": False} for l in lines],
                      "ocr": True, "confidence": round(conf, 3), "box": -1}
                state["blocks"].append(bl)
                state["ocr"] = {"used": True, "confidence": round(conf, 3)}
                sev = "HIGH" if conf < 0.8 else "MEDIUM"
                issues.append({"severity": sev, "code": "OCR_USED", "message": f"Page {pno} had no native text; OCR used (confidence {conf:.0%}). Verify numbers, doses, Greek letters.", "page": pno})
                if re.search(r"\d", text):
                    issues.append({"severity": "HIGH", "code": "OCR_NUMERIC_UNVERIFIED", "message": f"OCR text on page {pno} contains numeric values that must be verified against the image.", "page": pno})
                state["src_text"] = text
                return state
        # unreadable
        reason = "OCR unavailable" if not (ocr_enabled and ocr.available()) else "OCR returned no text"
        state["ocr"] = {"used": False, "reason": reason}
        state["blocks"].append({"id": f"ch{ch_no:03d}-p{pno:04d}-b001", "kind": "uncertain", "bbox": list(full), "order": 0, "column": 0,
                                "lines": [], "confidence": 0.0})
        issues.append({"severity": "HIGH", "code": "UNREADABLE_PAGE", "message": f"Page {pno} has no extractable text ({reason}). Marked UNCERTAIN.", "page": pno})
        return state

    # --- PASS 8: tables first so their text is not duplicated as paragraphs
    found_tables = tbl.find_tables(page)
    table_bboxes = [t[0] for t in found_tables]

    # --- PASS 2/3: layout + reading order (text blocks outside tables)
    boxes = _shaded_boxes(page)
    body = []
    for b in texts:
        if any(_inside(b.bbox, tb, 0.5) for tb in table_bboxes):
            continue
        # margin / page-number removal (running headers, footers)
        if b.bbox[1] < ph * 0.08 or b.bbox[3] > ph * 0.92:
            keys = [cleanup.normalize_margin(l.text) for l in b.lines]
            if all((k in margins) or cleanup.is_page_number(l.text) for k, l in zip(keys, b.lines)):
                dropped.append(list(b.bbox))
                continue
        body.append(b)

    # figures: image blocks that are big enough
    figs = [im for im in images if (im.bbox[2] - im.bbox[0]) >= FIG_MIN and (im.bbox[3] - im.bbox[1]) >= FIG_MIN]
    items = body + [layout.RawBlock(t, [], "table", pno) for t in table_bboxes] + figs
    ordered = layout.order_blocks(items, pw)

    # --- PASS 4: extraction into serialisable blocks
    ti = 0
    for n, b in enumerate(ordered, 1):
        bid = f"ch{ch_no:03d}-p{pno:04d}-b{n:03d}"
        base = {"id": bid, "bbox": [round(x, 2) for x in b.bbox], "order": n, "column": b.column}
        if b.kind == "text":
            box = next((i for i, r in enumerate(boxes) if _inside(b.bbox, r, 0.9)), -1)
            lines = []
            for l in b.lines:
                lines.append({"text": l.text, "marked": inline.render_spans(l.spans, whole_bold=l.bold, whole_italic=l.italic),
                              "x0": round(l.bbox[0], 2), "y0": round(l.bbox[1], 2), "y1": round(l.bbox[3], 2),
                              "size": round(l.size, 2), "bold": l.bold, "italic": l.italic})
            state["blocks"].append({**base, "kind": "text", "lines": lines, "box": box, "confidence": 1.0})
        elif b.kind == "table":
            rows = next(r for bb, r in found_tables if tuple(bb) == tuple(b.bbox))
            ti += 1
            md, info = tbl.serialise(rows)
            state["blocks"].append({**base, "kind": "table", "rows": [[tbl._cell(c) for c in r] for r in rows], "markdown": md,
                                    "table_info": info, "confidence": 0.6 if info["complex"] else 0.9})
        elif b.kind == "image":
            fig = {**base, "kind": "figure", "confidence": 0.9}
            if save_images:
                png = _render_clip(page, b.bbox)
                h = hashlib.sha1(png).hexdigest()
                if h in fig_hashes:
                    fig["duplicate"] = True
                    issues.append({"severity": "LOW", "code": "FIGURE_DUPLICATE", "message": f"Duplicate image on page {pno} skipped", "page": pno})
                else:
                    fig_hashes.add(h)
                    fig_counter["n"] += 1
                    fn = f"figure_tmp_{fig_counter['n']:03d}.png"
                    assets_dir.mkdir(parents=True, exist_ok=True)
                    (assets_dir / fn).write_bytes(png)
                    fig["image"] = fn
            state["blocks"].append(fig)

    # vector figures (no raster): drawings cluster above a "Figure" caption without image
    _vector_figures(page, state, ch_no, assets_dir, fig_counter, fig_hashes, save_images)

    # independent source text for numeric/word QA: words not in dropped regions
    words = page.get_text("words")
    kept = []
    for w in words:
        c = ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
        if any(d[0] <= c[0] <= d[2] and d[1] <= c[1] <= d[3] for d in dropped):
            continue
        kept.append(cleanup.normalize_chars(w[4]))
    state["src_text"] = " ".join(kept)
    state["dropped"] = dropped
    return state


def _vector_figures(page, state, ch_no, assets_dir, fig_counter, fig_hashes, save_images):
    caps = [b for b in state["blocks"] if b["kind"] == "text" and b["lines"]
            and re.match(r"^\s*(Figure|Fig\.)\s+\d", b["lines"][0]["text"])]
    if not caps:
        return
    try:
        drawings = [d["rect"] for d in page.get_drawings() if d["rect"].width > 2 and d["rect"].height > 2]
    except Exception:
        return
    for cap in caps:
        if any(b["kind"] == "figure" and abs(b["bbox"][3] - cap["bbox"][1]) < 60 for b in state["blocks"]):
            continue
        cx0, cy0, cx1, cy1 = cap["bbox"]
        near = [r for r in drawings if r.y1 <= cy0 + 2 and r.y0 >= cy0 - 380 and r.x0 >= cx0 - 40 and r.x1 <= cx1 + 40]
        if len(near) < 4:
            continue
        x0 = min(r.x0 for r in near); y0 = min(r.y0 for r in near); x1 = max(r.x1 for r in near); y1 = max(r.y1 for r in near)
        if x1 - x0 < FIG_MIN or y1 - y0 < FIG_MIN:
            continue
        bb = (x0 - 2, y0 - 2, x1 + 2, y1 + 2)
        fig = {"id": cap["id"] + "-vf", "kind": "figure", "bbox": [round(v, 2) for v in bb], "order": cap["order"] - 0.5,
               "column": cap["column"], "confidence": 0.7, "vector": True}
        if save_images:
            png = _render_clip(page, bb)
            h = hashlib.sha1(png).hexdigest()
            if h not in fig_hashes:
                fig_hashes.add(h)
                fig_counter["n"] += 1
                fn = f"figure_tmp_{fig_counter['n']:03d}.png"
                assets_dir.mkdir(parents=True, exist_ok=True)
                (assets_dir / fn).write_bytes(png)
                fig["image"] = fn
        state["blocks"].append(fig)
    state["blocks"].sort(key=lambda b: b["order"])

"""Per-page passes 1-4, 8, 9: inspect, layout, reading order, extraction, tables, figures.
Output is a JSON-serialisable page state, saved after every page (resume unit)."""
from __future__ import annotations
import hashlib, re
from pathlib import Path
from . import layout, tables as tbl, cleanup, ocr, inline
from .util import write_json

MIN_NATIVE_CHARS = 25
FIG_MIN = 40.0


def _is_junk(text: str) -> bool:
    """Margin text that is mostly control / private-use glyphs (e.g. an image-backed copyright line with no text map)."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    bad = sum(1 for c in chars if ord(c) < 32 or 0xE000 <= ord(c) <= 0xF8FF or c == "\ufffd")
    return bad / len(chars) >= 0.25


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
    texts = [b for b in texts if not any(_inside(b.bbox, tb, 0.5) for tb in table_bboxes)]
    composites, claimed = _composite_figures(page, texts, images, table_bboxes, ph)
    for b in texts:
        if id(b) in claimed:
            continue
        # margin / page-number removal (running headers, footers)
        if b.bbox[1] < ph * 0.08 or b.bbox[3] > ph * 0.92:
            keys = [cleanup.normalize_margin(l.text) for l in b.lines]
            junk = _is_junk(" ".join(l.text for l in b.lines))
            running = junk or (re.match(r"^\s*chapter\s+\d+\b", b.lines[0].text, re.I) is not None and b.bbox[1] < ph * 0.08)
            if running or all((k in margins) or cleanup.is_page_number(l.text) for k, l in zip(keys, b.lines)):
                dropped.append(list(b.bbox))
                continue
        body.append(b)

    # figures: image blocks that are big enough
    figs = [im for im in images if id(im) not in claimed and (im.bbox[2] - im.bbox[0]) >= FIG_MIN and (im.bbox[3] - im.bbox[1]) >= FIG_MIN]
    items = body + [layout.RawBlock(t, [], "table", pno) for t in table_bboxes] + figs + composites
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
        elif b.kind in ("image", "composite"):
            fig = {**base, "kind": "figure", "confidence": 0.9}
            if b.kind == "composite":
                fig["labels"] = b.payload["labels"]; fig["composite"] = True; fig["confidence"] = 0.8
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

    # independent source text for numeric/word QA: words not in dropped regions
    words = page.get_text("words")
    kept = []
    for w in words:
        c = ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
        if any(d[0] <= c[0] <= d[2] and d[1] <= c[1] <= d[3] for d in dropped):
            continue
        kept.append(cleanup.normalize_chars(w[4]))
    state["src_text"] = " ".join(kept)
    state["src_words"] = [[round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1), cleanup.normalize_chars(w[4])] for w in words
                          if not any(d[0] <= (w[0] + w[2]) / 2 <= d[2] and d[1] <= (w[1] + w[3]) / 2 <= d[3] for d in dropped)]
    state["dropped"] = dropped
    return state


_CAP_FIG = re.compile(r"^\s*(Figure|Fig\.)\s+\d")


def _composite_figures(page, texts, images, table_bboxes, ph):
    """Figures built from several image fragments / vector drawings with live text labels.
    The region next to a 'Fig. N' caption is rendered as one PNG; the label text is kept (not dropped) as figure text."""
    caps = [b for b in texts if b.lines and _CAP_FIG.match(b.lines[0].text)]
    if not caps:
        return [], set()
    try:
        drawings = [d["rect"] for d in page.get_drawings() if d["rect"].width > 2 and d["rect"].height > 2
                    and not any(_inside(tuple(d["rect"]), tb, 0.5) for tb in table_bboxes)]
    except Exception:
        drawings = []
    out, claimed = [], set()
    from collections import Counter as _Counter
    _sz = _Counter()
    for t in texts:
        for l in t.lines:
            _sz[round(l.size * 2) / 2] += len(l.text)
    page_body = _sz.most_common(1)[0][0] if _sz else 10.0
    for cap in caps:
        cx0, cy0, cx1, cy1 = cap.bbox
        low = cy1 - 330
        for o in caps:                               # another caption above (overlapping columns) bounds the window
            if o is not cap and o.bbox[3] <= cy0 and min(cx1, o.bbox[2]) - max(cx0, o.bbox[0]) > 0.5 * min(cx1 - cx0, o.bbox[2] - o.bbox[0]):
                low = max(low, o.bbox[3])
        frags = [im.bbox for im in images if id(im) not in claimed and im.bbox[3] <= cy1 + 15 and im.bbox[1] >= low]
        frags += [tuple(r) for r in drawings if r.y1 <= cy1 + 15 and r.y0 >= low and not (r.x0 >= cx0 - 1 and r.y0 >= cy0 - 1)]
        if not frags:
            continue
        ux0 = min(f[0] for f in frags); uy0 = min(f[1] for f in frags); ux1 = max(f[2] for f in frags); uy1 = max(f[3] for f in frags)
        if ux1 - ux0 < FIG_MIN or uy1 - uy0 < 10:
            continue
        x_hi = max(ux1, cx1) + 5 if cx0 >= ux1 - 5 else ux1 + 15           # caption beside the figure widens the window
        labels = [t for t in texts if t is not cap and id(t) not in claimed and not _CAP_FIG.match(t.lines[0].text)
                  and (t.bbox[1] >= uy0 - 12 or (t.bbox[1] >= uy0 - 30 and max(l.size for l in t.lines) < page_body * 0.95 or t.bbox[1] >= uy0 - 30 and t.bbox[3] - t.bbox[1] < 14 and t.bbox[1] > low))
                  and t.bbox[3] <= cy1 + 15 and t.bbox[0] >= ux0 - 50 and t.bbox[2] <= x_hi
                  and not (t.bbox[0] >= cx0 - 1 and t.bbox[1] >= cy0 - 1 and t.bbox[3] <= cy1 + 1)]
        if len(labels) < 1 and len(frags) < 3:
            continue                                                       # plain single image: normal path
        region = [ux0, uy0, ux1, uy1]
        for t in labels:
            region = [min(region[0], t.bbox[0]), min(region[1], t.bbox[1]), max(region[2], t.bbox[2]), max(region[3], t.bbox[3])]
        rb = layout.RawBlock(tuple(region), [], "composite", page.number + 1)
        lab = []
        for t in sorted(labels, key=lambda t: (round(t.bbox[1] / 4), t.bbox[0])):
            lines = [l.text.strip() for l in t.lines if l.text.strip()]
            bul = [i for i, l in enumerate(lines) if re.match(r"^[•●▪■◦·‣○]", l)]
            if bul:
                cur = []
                for l in lines:
                    if re.match(r"^[•●▪■◦·‣○]\s*", l):
                        cur.append(re.sub(r"^[•●▪■◦·‣○]\s*", "", l))
                    elif cur:
                        cur[-1] += " " + l
                    else:
                        cur.append(l)
                lab += cur
            else:
                lab.append(" ".join(lines))
        rb.payload = {"labels": lab}
        for t in labels:
            claimed.add(id(t))
        for im in images:
            if im.bbox[0] >= region[0] - 1 and im.bbox[2] <= region[2] + 1 and im.bbox[1] >= region[1] - 1 and im.bbox[3] <= region[3] + 1:
                claimed.add(id(im))
        out.append(rb)
    return out, claimed

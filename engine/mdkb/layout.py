"""Layout analysis + reading-order reconstruction (two-column aware)."""
from __future__ import annotations
from dataclasses import dataclass
from . import cleanup


@dataclass
class Line:
    text: str
    bbox: tuple
    size: float
    bold: bool
    italic: bool
    spans: list  # [(text, bold, italic, size)]


@dataclass
class RawBlock:
    bbox: tuple
    lines: list
    kind: str = "text"      # text | image
    page: int = 0
    column: int = 0
    order: int = 0

    @property
    def size(self):
        s = [l.size for l in self.lines if l.size]
        return sum(s) / len(s) if s else 0.0

    @property
    def bold(self):
        return bool(self.lines) and all(l.bold for l in self.lines)

    @property
    def text(self):
        return "\n".join(l.text for l in self.lines)


def extract_blocks(page) -> tuple[list[RawBlock], list[RawBlock]]:
    """Return (text_blocks, image_blocks) in raw PDF order, using PyMuPDF dict output.
    Spans carry bold/italic/sub/sup so inline formatting can be rebuilt."""
    d = page.get_text("dict")
    texts, images = [], []
    for b in d["blocks"]:
        if b["type"] == 1:
            images.append(RawBlock(tuple(b["bbox"]), [], "image", page.number + 1))
            continue
        lines = []
        for l in b.get("lines", []):
            raw = [s for s in l["spans"] if s["text"].strip()]
            if not raw:
                continue
            size = max(s["size"] for s in raw)
            base = max(s["origin"][1] for s in raw if s["size"] >= size * 0.9)
            spans = []
            for s in l["spans"]:
                if not s["text"]:
                    continue
                small = s["size"] < size * 0.8
                sup = bool(s["flags"] & 1) or (small and s["origin"][1] < base - size * 0.15)
                sub = (not sup) and small and s["origin"][1] > base + size * 0.05
                spans.append({"text": cleanup.normalize_chars(s["text"]), "size": s["size"],
                              "bold": bool(s["flags"] & 16) or "bold" in s["font"].lower(),
                              "italic": bool(s["flags"] & 2) or "italic" in s["font"].lower() or "oblique" in s["font"].lower(),
                              "sup": sup, "sub": sub})
            txt = "".join(s["text"] for s in spans)
            nonspace = [s for s in spans if s["text"].strip()]
            lines.append(Line(txt, tuple(l["bbox"]), size, all(s["bold"] for s in nonspace),
                              all(s["italic"] for s in nonspace), spans))
        # split where line style changes (heading line vs body text) so headings never hide inside a block
        group = []
        for ln in lines:
            if group and (abs(ln.size - group[-1].size) > 1.0 or (ln.bold != group[-1].bold and (ln.bold or group[-1].bold) and
                                                                   (len(ln.text) < 100 and len(group[-1].text) < 100 or ln.bold))):
                texts.append(_mk(group, page)); group = []
            group.append(ln)
        if group:
            texts.append(_mk(group, page))
    return texts, images


def _mk(lines, page):
    x0 = min(l.bbox[0] for l in lines); y0 = min(l.bbox[1] for l in lines)
    x1 = max(l.bbox[2] for l in lines); y1 = max(l.bbox[3] for l in lines)
    return RawBlock((x0, y0, x1, y1), list(lines), "text", page.number + 1)


def detect_columns(blocks: list[RawBlock], page_width: float):
    """Find the gutter. Returns x of the column boundary or None for single-column pages.
    A page is two-column when many narrow blocks fall cleanly on both sides of the centre line
    with no body block straddling it."""
    if page_width <= 0:
        return None
    mid = page_width / 2
    body = [b for b in blocks if (b.bbox[2] - b.bbox[0]) < page_width * 0.7]
    left = [b for b in body if b.bbox[2] <= mid + page_width * 0.04]
    right = [b for b in body if b.bbox[0] >= mid - page_width * 0.04]
    straddle = [b for b in body if b.bbox[0] < mid - page_width * 0.04 and b.bbox[2] > mid + page_width * 0.04]
    if len(left) >= 2 and len(right) >= 2 and len(straddle) <= max(1, len(body) // 10):
        gutter = (max(b.bbox[2] for b in left) + min(b.bbox[0] for b in right)) / 2
        return gutter
    return None


def order_blocks(blocks: list[RawBlock], page_width: float) -> list[RawBlock]:
    """Reading order: full-width blocks split the page into bands; inside a band, left column then right.
    Never interleaves left/right lines."""
    if not blocks:
        return []
    gutter = detect_columns(blocks, page_width)
    ordered = sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
    if gutter is None:
        for i, b in enumerate(ordered):
            b.column, b.order = 0, i
        return ordered
    margin = page_width * 0.04

    def is_full(b):
        return b.bbox[0] < gutter - margin and b.bbox[2] > gutter + margin

    result, band_l, band_r = [], [], []

    def flush():
        result.extend(sorted(band_l, key=lambda b: (b.bbox[1], b.bbox[0])))
        result.extend(sorted(band_r, key=lambda b: (b.bbox[1], b.bbox[0])))
        band_l.clear(); band_r.clear()

    for b in ordered:
        if is_full(b):
            flush()
            b.column = 0
            result.append(b)
        elif (b.bbox[0] + b.bbox[2]) / 2 < gutter:
            b.column = 1; band_l.append(b)
        else:
            b.column = 2; band_r.append(b)
    flush()
    for i, b in enumerate(result):
        b.order = i
    return result


def body_font_size(blocks_by_page: list[list[RawBlock]]) -> float:
    """Most common (length-weighted) font size = body text size."""
    from collections import Counter
    c = Counter()
    for blocks in blocks_by_page:
        for b in blocks:
            for l in b.lines:
                c[round(l.size * 2) / 2] += len(l.text)
    return c.most_common(1)[0][0] if c else 10.0

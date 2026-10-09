"""Passes 5-7, 10, 11: cleanup, heading reconstruction, lists/callouts, references, Markdown assembly.
Input: list of page states (in page order). Output: AssembledChapter."""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field
from . import cleanup
from .util import stable_section_id, fingerprint, slugify

BULLET_RE = re.compile(r"^\s*([•●▪■◦·‣○]|[-–—*](?=\s))\s*(.*)$")
NUM_RE = re.compile(r"^\s*(\d{1,2})[.)]\s+(\S.*)$")
LETTER_RE = re.compile(r"^\s*(\(?[a-z]\)|\([ivx]+\))\s+(\S.*)$")
CALLOUT_RE = re.compile(r"^\s*(KEY POINTS?|KEY CONCEPTS?|CLINICAL PEARLS?|PEARLS?|IMPORTANT|CONTROVERSIES|SUMMARY|WARNING|CAUTION|NOTE|BOX \d[\d.]*[^\n]{0,60})\s*:?\s*$", re.I)
CAPTION_RE = re.compile(r"^\s*(Figure|Fig\.|Table)\s+(\d{1,4})(?:[.\-](\d{1,3}))?\b")
REF_HEAD_RE = re.compile(r"^\s*(references?|bibliography|suggested readings?|further reading)\s*$", re.I)
REF_ITEM_RE = re.compile(r"^\s*(?:\[(\d{1,4})\]|(\d{1,4})[.)])\s+(\S.*)$")
TERMINAL = tuple(".!?:;)]\"'”’")
LARGE_TABLE_ROWS = 40


@dataclass
class Out:
    kind: str                 # heading|paragraph|list|table|figure|caption|callout|footnote|reference|uncertain|marker
    md: str
    page: int
    bbox: list
    block_id: str
    raw: str = ""
    cleaned: str = ""
    confidence: float = 1.0
    level: int = 0
    order: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class Assembled:
    items: list[Out]
    issues: list[dict]
    headings: list[tuple[int, str, str]]    # level, text, section_id
    refs: dict[int, str]
    footnotes: list[tuple[str, str, int]]
    tables: list[dict]
    figures: list[dict]
    unresolved_xref_candidates: list = field(default_factory=list)


def _line_text(l):
    return l["text"]


def body_size(pages: list[dict]) -> float:
    c = Counter()
    for p in pages:
        for b in p["blocks"]:
            if b["kind"] == "text" and not b.get("ocr"):
                for l in b["lines"]:
                    c[round(l["size"] * 2) / 2] += len(l["text"])
    return c.most_common(1)[0][0] if c else 10.0


def _paragraph_groups(block):
    """Split a text block into paragraph / list-item groups (PASS 7)."""
    groups, cur, left = [], None, min((l["x0"] for l in block["lines"]), default=0)
    prev = None
    for l in block["lines"]:
        t = l["text"]
        m_b, m_n, m_l = BULLET_RE.match(t), NUM_RE.match(t), LETTER_RE.match(t)
        new_item = bool(m_b or m_n or m_l)
        if m_n and re.match(r"^\s*\d{1,2}[.)]\s+\d", t) and not cur:
            pass
        if new_item:
            cur = {"kind": "item", "lines": [l], "indent": max(0, round((l["x0"] - left) / 14)),
                   "marker": (m_n.group(1) + "." if m_n else "-"), "num": m_n.group(1) if m_n else None}
            groups.append(cur)
        else:
            gap = (l["y0"] - prev["y1"]) if prev else 0
            indented = prev is not None and (l["x0"] - left) > 6 and prev["text"].rstrip().endswith(TERMINAL) and prev["x0"] - left < 3
            para_break = prev is not None and (gap > 0.6 * l["size"] or (indented and prev["text"].rstrip().endswith(".")))
            if cur is None or para_break or (cur["kind"] == "item" and gap > 0.9 * l["size"]):
                cur = {"kind": "para", "lines": [l]}
                groups.append(cur)
            else:
                cur["lines"].append(l)
        prev = l
    return groups


def _strip_marker(first_marked: str) -> str:
    s = first_marked
    for rx in (BULLET_RE, NUM_RE, LETTER_RE):
        m = rx.match(s)
        if m:
            return m.group(m.lastindex)
    return s


def assemble(pages: list[dict], chapter, book_slug: str, vocab: cleanup.Vocab | None, convention: dict | None = None,
             fig_dir_rel: str = "", ) -> Assembled:
    convention = convention or {}
    issues: list[dict] = []
    bsize = convention.get("body_size") or body_size(pages)

    # ---- PASS 6a: collect heading sizes
    cand_sizes = Counter()
    title_norm = re.sub(r"\W+", "", chapter.title.lower())

    def is_title_block(p, b):
        if p["page"] != chapter.start_page or b["kind"] != "text" or not b["lines"]:
            return False
        raw_ = " ".join(l["text"] for l in b["lines"])
        norm = re.sub(r"\W+", "", raw_.lower())
        return bool(re.match(r"^\s*chapter\s+\d+", raw_, re.I) or (title_norm and norm and len(norm) > 4 and (norm in title_norm or title_norm in norm)))

    for p in pages:
        for b in p["blocks"]:
            if b["kind"] == "text" and not b.get("ocr") and 0 < len(b["lines"]) <= 3 and not (is_title_block(p, b) and p["blocks"].index(b) < 3):
                s = round(max(l["size"] for l in b["lines"]) * 2) / 2
                txt = " ".join(l["text"] for l in b["lines"])
                if s >= bsize * 1.12 and len(txt) <= 160:
                    cand_sizes[s] += 1
    sizes_desc = sorted(cand_sizes, reverse=True)
    size_level = {s: min(2 + i, 4) for i, s in enumerate(sizes_desc)}

    items: list[Out] = []
    used_ids: set[str] = set()
    heading_stack: list[tuple[int, str]] = []
    headings: list[tuple[int, str, str]] = []
    in_refs = False
    refs: dict[int, str] = {}
    footnotes: list[tuple[str, str, int]] = []
    tables: list[dict] = []
    figures: list[dict] = []
    cur_pages: list[dict] = []

    def add(kind, md, p, b, raw="", cleaned="", conf=1.0, level=0, **extra):
        o = Out(kind, md, p["page"], b["bbox"], b["id"], raw, cleaned or md, conf, level, len(items), extra)
        items.append(o)
        return o

    for p in pages:
        pno = p["page"]
        blocks = sorted(p["blocks"], key=lambda b: b["order"])
        i = 0
        while i < len(blocks):
            b = blocks[i]
            i += 1
            if b["kind"] == "uncertain":
                add("uncertain", f"<!-- UNCERTAIN: source text unreadable, page {pno} -->\n\n[Unreadable source text]", p, b, conf=0.0)
                continue
            if b["kind"] == "figure":
                if b.get("duplicate"):
                    continue
                figures.append({"block": b, "page": pno})
                add("figure", "", p, b, conf=b["confidence"], figure=len(figures) - 1)
                continue
            if b["kind"] == "table":
                tables.append({"block": b, "page": pno})
                add("table", "", p, b, raw=" ".join(" ".join(r) for r in b["rows"]), conf=b["confidence"], table=len(tables) - 1)
                continue

            # ----- text block
            lines = b["lines"]
            raw = "\n".join(l["text"] for l in lines)
            if b.get("ocr"):
                para = cleanup.join_lines([l["text"] for l in lines], vocab)
                o = add("paragraph", para, p, b, raw=raw, conf=b["confidence"])
                o.extra["ocr"] = True
                continue
            first = lines[0]["text"].strip()
            # chapter title dropped on first page (title comes from outline/metadata)
            if pno == chapter.start_page and not headings and all(x.kind == "chapter_title" for x in items) and is_title_block(p, b):
                add("chapter_title", "", p, b, raw=raw)
                continue
            # footnote: small font in lower page area, starting with number/symbol
            ph = p["height"]
            fsize = max(l["size"] for l in lines)
            if fsize < bsize * 0.88 and b["bbox"][1] > ph * 0.72 and re.match(r"^\s*(\d{1,2}|[*†‡§])\s*\S", first):
                m = re.match(r"^\s*(\d{1,2}|[*†‡§])\s*(.*)$", first)
                text = cleanup.join_lines([m.group(2)] + [l["marked"] for l in lines[1:]], vocab)
                label = f"p{pno}n{m.group(1)}" if m.group(1).isdigit() else f"p{pno}s{len(footnotes)+1}"
                footnotes.append((label, text, pno))
                add("footnote", "", p, b, raw=raw, cleaned=text, label=label)
                continue
            # caption
            if CAPTION_RE.match(first):
                txt = cleanup.join_lines([l["marked"] for l in lines], vocab)
                add("caption", txt, p, b, raw=raw, cleaned=txt, caption=CAPTION_RE.match(first).groups())
                continue
            # callout keyword
            if CALLOUT_RE.match(first) and (b["lines"][0]["bold"] or first.isupper() or b.get("box", -1) >= 0):
                label = CALLOUT_RE.match(first).group(1).strip().upper()
                rest = lines[1:]
                inner = _render_groups(rest, b, vocab, issues, pno) if rest else ""
                add("callout", f"> **{label}**" + (("\n>\n" + "\n".join("> " + x if x else ">" for x in inner.split("\n"))) if inner else ""), p, b, raw=raw, conf=0.9, label=label, box=b.get("box", -1), open=not rest)
                continue
            # callout continuation (previous callout still open, same shaded box)
            if items and items[-1].kind == "callout" and b.get("box", -1) >= 0 and items[-1].extra.get("box") == b.get("box") and items[-1].page == pno:
                inner = _render_groups(lines, b, vocab, issues, pno)
                items[-1].md += "\n>\n" + "\n".join("> " + x if x else ">" for x in inner.split("\n"))
                items[-1].raw += "\n" + raw
                continue
            if items and items[-1].kind == "callout" and items[-1].extra.get("open") and items[-1].page == pno:
                inner = _render_groups(lines, b, vocab, issues, pno)
                items[-1].md += "\n>\n" + "\n".join("> " + x if x else ">" for x in inner.split("\n"))
                items[-1].raw += "\n" + raw
                items[-1].extra["open"] = False
                continue

            # heading detection (PASS 6)
            hd = _detect_heading(b, lines, bsize, size_level, sizes_desc)
            if hd:
                level, conf = hd
                text = cleanup.join_lines([l["text"] for l in lines], vocab)
                if REF_HEAD_RE.match(text):
                    in_refs = True
                    level = 2
                elif in_refs and level <= 2:
                    in_refs = False
                # keep hierarchy sane: do not skip more than one level
                depth = heading_stack[-1][0] if heading_stack else 1
                if level > depth + 1:
                    level = depth + 1
                    conf = min(conf, 0.7)
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                sid = stable_section_id(book_slug, chapter.number, [h[1] for h in heading_stack], used_ids)
                headings.append((level, text, sid))
                o = add("heading", f"{'#' * level} {text}\n<!-- section_id: {sid} -->", p, b, raw=raw, cleaned=text, conf=conf, level=level, section_id=sid)
                if conf < 0.75:
                    issues.append({"severity": "MEDIUM", "code": "HEADING_UNCERTAIN", "message": f"Heading detected with low confidence ({conf:.2f}): '{text[:70]}'", "page": pno, "block_id": b["id"]})
                continue

            # references
            if in_refs:
                body = _render_refs(lines, vocab, refs)
                add("reference", body, p, b, raw=raw)
                continue

            # lists / paragraphs
            rendered = _render_groups(lines, b, vocab, issues, pno)
            kind = "list" if any(g["kind"] == "item" for g in _paragraph_groups(b)) and all(g["kind"] == "item" for g in _paragraph_groups(b)) else "paragraph"
            o = add(kind, rendered, p, b, raw=raw, conf=1.0)
            o.extra["x0"] = min(l["x0"] for l in lines)
            o.extra["col"] = b["column"]

    # ---- merge paragraphs continuing across columns/pages (never across other block types)
    merged: list[Out] = []
    for o in items:
        if (o.kind == "paragraph" and merged and merged[-1].kind == "paragraph"
                and not merged[-1].extra.get("ocr") and not o.extra.get("ocr")
                and not merged[-1].md.rstrip().endswith(TERMINAL) and "\n" not in o.md
                and o.md[:1].islower() and "\n" not in merged[-1].md):
            prev = merged[-1]
            prev.md = cleanup.join_lines([prev.md, o.md], vocab)
            prev.raw += "\n" + o.raw
            prev.extra.setdefault("merged", []).append({"page": o.page, "bbox": o.bbox, "block_id": o.block_id})
            continue
        merged.append(o)
    items = merged
    for k, o in enumerate(items):
        o.order = k

    # section / heading path per item
    stack: list[tuple[int, str]] = []
    sid = ""
    for o in items:
        if o.kind == "heading":
            while stack and stack[-1][0] >= o.level:
                stack.pop()
            stack.append((o.level, o.cleaned))
            sid = o.extra["section_id"]
        o.extra["heading_path"] = [h[1] for h in stack]
        o.extra["section_id"] = sid

    return Assembled(items, issues, headings, refs, footnotes, tables, figures)


def _detect_heading(b, lines, bsize, size_level, sizes_desc):
    if len(lines) > 3:
        return None
    text = " ".join(l["text"] for l in lines).strip()
    if not text or len(text) > 160 or BULLET_RE.match(text) and not text[0].isalnum():
        return None
    size = round(max(l["size"] for l in lines) * 2) / 2
    if size >= bsize * 1.12 and size in size_level and not text.endswith((".", ",")) or (size >= bsize * 1.12 and size in size_level and len(text) < 90 and not text.endswith(",")):
        return size_level[size], 0.92
    allbold = all(l["bold"] for l in lines)
    if allbold and len(lines) <= 2 and len(text) < 100 and not text.endswith((".", ",", ";")) and not CAPTION_RE.match(text) and not NUM_RE.match(text):
        lvl = min((max(size_level.values()) if size_level else 2) + 1, 5)
        return lvl, 0.7
    return None


def _render_groups(lines, block, vocab, issues, pno) -> str:
    sub = {"lines": lines}
    groups = _paragraph_groups(sub)
    out = []
    for g in groups:
        iss: list = []
        if g["kind"] == "item":
            first = g["lines"][0]
            marked = [first["marked"]] + [l["marked"] for l in g["lines"][1:]]
            marked[0] = _strip_marker(marked[0]) if _strip_marker(marked[0]) != marked[0] else _strip_marker(first["text"])
            text = cleanup.join_lines(marked, vocab, iss)
            prefix = ("  " * g["indent"]) + (f"{g['num']}. " if g["num"] else "- ")
            out.append(prefix + text)
        else:
            text = cleanup.join_lines([l["marked"] for l in g["lines"]], vocab, iss)
            out.append(text)
        for sev, code, msg in iss:
            issues.append({"severity": sev, "code": code, "message": msg, "page": pno})
    # items stay tight; paragraphs separated by blank line
    res, prev_kind = [], None
    for g, t in zip(groups, out):
        if res:
            res.append("\n" if (g["kind"] == "item" and prev_kind == "item") else "\n\n")
        res.append(t)
        prev_kind = g["kind"]
    return "".join(res)


def _render_refs(lines, vocab, refs: dict[int, str]) -> str:
    entries, cur = [], None
    for l in lines:
        m = REF_ITEM_RE.match(l["text"])
        if m:
            cur = {"n": int(m.group(1) or m.group(2)), "lines": [m.group(3)]}
            entries.append(cur)
        elif cur is not None:
            cur["lines"].append(l["text"])
        else:
            cur = {"n": None, "lines": [l["text"]]}
            entries.append(cur)
    out = []
    for e in entries:
        text = cleanup.join_lines(e["lines"], vocab)
        if e["n"] is not None:
            refs[e["n"]] = text
            out.append(f'<a id="ref-{e["n"]}"></a>[{e["n"]}] {text}')
        else:
            out.append(text)
    return "\n\n".join(out)

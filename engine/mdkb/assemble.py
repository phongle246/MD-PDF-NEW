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
    excluded: list = field(default_factory=list)      # [(page, bbox)] source regions intentionally not in Markdown
    repairs: list = field(default_factory=list)


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

    # ---- PASS 6a: heading style ranking. A style = (font size, ALL-CAPS). Larger size ranks higher; at equal size ALL-CAPS ranks higher.
    title_norm = re.sub(r"\W+", "", chapter.title.lower())

    def is_title_block(p, b):
        if p["page"] != chapter.start_page or b["kind"] != "text" or not b["lines"]:
            return False
        raw_ = " ".join(l["text"] for l in b["lines"])
        norm = re.sub(r"\W+", "", raw_.lower())
        return bool(re.match(r"^\s*chapter\s+%d\b" % chapter.number, raw_, re.I) or (title_norm and norm and len(norm) > 4 and (norm in title_norm or title_norm in norm) and max(l["size"] for l in b["lines"]) >= bsize * 1.3))

    # blocks at/after a different chapter's heading (reading order) are not part of this chapter: keep them out of style statistics
    foreign: set[str] = set()
    _stop, _seen = False, False
    for p in pages:
        for b in sorted(p["blocks"], key=lambda b: b["order"]):
            if _stop:
                foreign.add(b["id"]); continue
            if b["kind"] == "text" and not b.get("ocr") and b["lines"] and not is_title_block(p, b):
                m_ = re.match(r"^\s*chapter\s+(\d+)\b", b["lines"][0]["text"], re.I)
                if m_ and int(m_.group(1)) != chapter.number and max(l["size"] for l in b["lines"]) >= bsize * 1.3 and _seen:
                    _stop = True; foreign.add(b["id"]); continue
                _seen = True

    style_count = Counter()
    for p in pages:
        for b in p["blocks"]:
            if b["id"] in foreign:
                continue
            if b["kind"] == "text" and not b.get("ocr") and 0 < len(b["lines"]) <= 3 and not is_title_block(p, b):
                st = _heading_style(b["lines"], bsize)
                if st:
                    style_count[st] += 1
    styles_ranked = sorted(style_count, key=lambda k: (-k[0], not k[1]))
    size_level = {st: min(2 + i, 5) for i, st in enumerate(styles_ranked)}
    sizes_desc = styles_ranked

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

    title_ids = {b["id"] for p in pages if p["page"] == chapter.start_page for b in p["blocks"] if is_title_block(p, b)}
    excluded: list = []
    repairs: list = []
    stop = False
    seen_title = False
    next_ch = re.compile(r"^\s*chapter\s+(\d+)\b", re.I)
    for p in pages:
        if stop:
            for b in p["blocks"]:
                excluded.append((p["page"], b["bbox"]))
            continue
        pno = p["page"]
        blocks = sorted(p["blocks"], key=lambda b: b["order"])
        i = 0
        while i < len(blocks):
            b = blocks[i]
            i += 1
            if stop:
                excluded.append((pno, b["bbox"]))
                continue
            # a heading for a *different* chapter ends this chapter (chapters may share a page)
            if b["kind"] == "text" and not b.get("ocr") and b["lines"] and not is_title_block(p, b):
                m = next_ch.match(b["lines"][0]["text"])
                if m and int(m.group(1)) != chapter.number and max(l["size"] for l in b["lines"]) >= bsize * 1.3 and (items or headings):
                    stop = True
                    excluded.append((pno, b["bbox"]))
                    issues.append({"severity": "LOW", "code": "CHAPTER_BOUNDARY", "message": f"Content from page {pno} onward belongs to Chapter {m.group(1)} and was excluded here", "page": pno})
                    continue
            if b["kind"] == "uncertain":
                add("uncertain", f"<!-- UNCERTAIN: source text unreadable, page {pno} -->\n\n[Unreadable source text]", p, b, conf=0.0)
                continue
            if b["kind"] == "figure":
                if b.get("duplicate"):
                    continue
                figures.append({"block": b, "page": pno})
                add("figure", "", p, b, conf=b["confidence"], figure=len(figures) - 1, labels=b.get("labels") or [])
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
            if b["id"] in title_ids:
                if not seen_title:
                    stale = [x for x in items if x.kind != "chapter_title"]
                    items[:] = [x for x in items if x.kind == "chapter_title"]       # content before the chapter heading belongs to the previous chapter
                    for x in stale:
                        excluded.append((x.page, x.bbox))
                    if stale:                                                        # their headings/refs/footnotes go too
                        headings.clear(); heading_stack.clear(); used_ids.clear(); refs.clear(); footnotes.clear(); in_refs = False
                    if stale:
                        issues.append({"severity": "LOW", "code": "CHAPTER_BOUNDARY", "message": f"{len(stale)} block(s) before the chapter heading on page {pno} belong to the previous chapter and were excluded", "page": pno})
                seen_title = True
                add("chapter_title", "", p, b, raw=raw)
                excluded.append((pno, b["bbox"]))
                continue
            # byline (authors) directly under the chapter title: keep as plain italic text, not a heading
            if seen_title and not headings and all(x.kind == "chapter_title" for x in items) and len(lines) <= 2 and all(l["italic"] for l in lines) and len(raw) < 120:
                add("paragraph", "*" + cleanup.join_lines([l["text"] for l in lines], vocab) + "*", p, b, raw=raw, byline=True)
                seen_title = False
                items[-1].extra["byline"] = True
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
                txt = cleanup.repair_artifacts(cleanup.join_lines([l["marked"] for l in lines], vocab), vocab, repairs)
                add("caption", txt, p, b, raw=raw, cleaned=txt, caption=CAPTION_RE.match(first).groups())
                continue
            # caption continuation: short unstyled block directly under a caption that does not end its sentence
            if items and items[-1].kind == "caption" and items[-1].page == pno and len(lines) <= 2 and not lines[0]["bold"] \
                    and 0 <= b["bbox"][1] - items[-1].bbox[3] < 6 and not items[-1].md.rstrip().endswith((".", ")")) and items[-1].extra.get("cont", 0) < 2:
                items[-1].md = cleanup.join_lines([items[-1].md] + [l["marked"] for l in lines], vocab)
                items[-1].raw += "\n" + raw
                items[-1].extra["cont"] = items[-1].extra.get("cont", 0) + 1
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
            rendered = cleanup.repair_artifacts(_render_groups(lines, b, vocab, issues, pno), vocab, repairs)
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

    A = Assembled(items, issues, headings, refs, footnotes, tables, figures)
    A.excluded, A.repairs = excluded, repairs
    return A


def _heading_style(lines, bsize):
    """Return the heading style key (size, all_caps) if the block looks like a heading by typography, else None."""
    if len(lines) > 3:
        return None
    text = " ".join(l["text"] for l in lines).strip()
    if not text or len(text) > 160 or (BULLET_RE.match(text) and not text[0].isalnum()):
        return None
    size = round(max(l["size"] for l in lines) * 2) / 2
    allbold = all(l["bold"] for l in lines)
    big = size >= bsize * 1.12
    if all(l["italic"] for l in lines) and not allbold:
        return None                                  # italic-only lines (bylines, species names) are not headings
    if not (big or (allbold and size >= bsize * 0.98)):
        return None
    if text.endswith((",", ";")) or (not big and text.endswith(".")) or CAPTION_RE.match(text) or NUM_RE.match(text):
        return None
    if not big and (len(lines) > 2 or len(text) > 100):
        return None
    letters = [c for c in text if c.isalpha()]
    return (size, bool(letters) and all(c.isupper() for c in letters))


def _detect_heading(b, lines, bsize, size_level, sizes_desc):
    st = _heading_style(lines, bsize)
    if st is None or st not in size_level:
        return None
    big = st[0] >= bsize * 1.12
    return size_level[st], (0.92 if big else 0.8)


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

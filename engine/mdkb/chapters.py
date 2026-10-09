"""Chapter detection: PDF outline first, then text patterns. Never invents titles."""
from __future__ import annotations
import re
from .models import Chapter

CH_RE = re.compile(r"^\s*(?:chapter|ch\.?)\s+(\d{1,4})\b[\s.:\-–—]*(.*)$", re.I)
NUM_TITLE_RE = re.compile(r"^\s*(\d{1,4})\s*[.:\-–—]?\s+([A-Z][^\n]{3,120})$")


def _clean_title(t: str) -> tuple[int | None, str]:
    t = re.sub(r"\s+", " ", t).strip()
    m = CH_RE.match(t)
    if m:
        return int(m.group(1)), m.group(2).strip(" .:-–—") or t
    m = re.match(r"^(\d{1,4})\s*[.:\-–—]?\s+(.+)$", t)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, t


def from_outline(doc, level: int | None = None) -> list[Chapter]:
    toc = doc.get_toc(simple=True)          # [lvl, title, page]
    if not toc:
        return []
    levels = sorted({e[0] for e in toc})
    use = level or levels[0]
    # prefer the level whose entries look most like chapters (number-prefixed)
    best, best_score = use, -1
    for lv in levels[:3]:
        ents = [e for e in toc if e[0] == lv]
        score = sum(1 for e in ents if _clean_title(e[1])[0] is not None) / max(1, len(ents))
        if score > best_score + 0.2 and len(ents) >= 2:
            best, best_score = lv, score
    entries = [e for e in toc if e[0] == best and e[2] >= 1]
    chapters: list[Chapter] = []
    n = doc.page_count
    for i, (_, title, page) in enumerate(entries):
        end = (entries[i + 1][2] - 1) if i + 1 < len(entries) else n
        end = max(page, end) if entries[i + 1:] and entries[i + 1][2] > page else max(page, end)
        num, clean = _clean_title(title)
        chapters.append(Chapter(num if num is not None else i + 1, clean, page, min(end, n),
                                0.95 if num is not None else 0.8, "outline"))
    return _dedupe(chapters)


def _btext(b) -> str:
    return " ".join(" ".join("".join(s["text"] for s in l["spans"]).split()) for l in b["lines"]).strip()


def from_text_patterns(doc, max_pages: int | None = None) -> list[Chapter]:
    """Find 'Chapter N' headings in large type. Starts may be mid-page (chapters can share a page)."""
    found = []
    for pno in range(doc.page_count if max_pages is None else min(max_pages, doc.page_count)):
        page = doc[pno]
        d = page.get_text("dict")
        sizes = [s["size"] for b in d["blocks"] if b["type"] == 0 for l in b["lines"] for s in l["spans"]]
        if not sizes:
            continue
        body = sorted(sizes)[len(sizes) // 2]
        for b in d["blocks"]:
            if b["type"] != 0:                                                 # running headers are excluded by the size test below
                continue
            txt = _btext(b)
            size = max((s["size"] for l in b["lines"] for s in l["spans"]), default=0)
            m = CH_RE.match(txt)
            if m and size >= body * 1.3:
                title = m.group(2).strip(" .:-–—")
                if not title:                                                  # title is in the next big block
                    nxt = [x for x in d["blocks"] if x["type"] == 0 and x["bbox"][1] >= b["bbox"][3] - 1 and x["bbox"][1] - b["bbox"][3] < 40]
                    nxt.sort(key=lambda x: x["bbox"][1])
                    if nxt:
                        n0 = nxt[0]
                        if max(s["size"] for l in n0["lines"] for s in l["spans"]) >= body * 1.3:
                            title = _btext(n0)
                found.append((pno + 1, int(m.group(1)), title, txt, b["bbox"][1] > page.rect.height * 0.25))
                break
    chapters = []
    for i, (pg, num, title, full, mid) in enumerate(found):
        end = found[i + 1][0] - 1 if i + 1 < len(found) else doc.page_count
        if i + 1 < len(found) and found[i + 1][4]:
            end = found[i + 1][0]                                              # next chapter starts mid-page: shared page
        c = Chapter(num, title or full, pg, max(pg, end), 0.8 if title else 0.5, "heading-pattern")
        if mid and pg > 1:
            c.notes.append("starts mid-page")
        if i + 1 < len(found) and found[i + 1][4]:
            c.notes.append("shares last page with next chapter")
        chapters.append(c)
    return _dedupe(chapters)


def _dedupe(chs: list[Chapter]) -> list[Chapter]:
    seen, out = set(), []
    for c in chs:
        k = (c.start_page, c.title.lower())
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def detect(doc) -> list[Chapter]:
    chs = from_outline(doc)
    if len(chs) < 2:
        alt = from_text_patterns(doc)
        if len(alt) > len(chs):
            chs = alt
    if not chs:
        return []
    return validate(chs, doc.page_count)


def validate(chs: list[Chapter], page_count: int) -> list[Chapter]:
    """Flag overlaps/gaps/duplicated numbers; low confidence -> user review."""
    chs.sort(key=lambda c: c.start_page)
    nums = [c.number for c in chs]
    for i, c in enumerate(chs):
        c.end_page = min(c.end_page, page_count)
        shared = i + 1 < len(chs) and "shares last page with next chapter" in c.notes
        if i + 1 < len(chs) and c.end_page >= chs[i + 1].start_page and chs[i + 1].start_page > c.start_page and not shared:
            c.end_page = chs[i + 1].start_page - 1
            c.notes.append("end page trimmed to next chapter start")
        if nums.count(c.number) > 1:
            c.confidence = min(c.confidence, 0.4)
            c.notes.append("duplicate chapter number")
        if c.end_page < c.start_page:
            c.confidence = min(c.confidence, 0.3)
            c.notes.append("invalid page range")
    return chs


def needs_review(chs: list[Chapter], threshold: float = 0.6) -> list[Chapter]:
    return [c for c in chs if c.confidence < threshold]


def whole_book_fallback(doc, title: str) -> list[Chapter]:
    """No chapters detectable: expose the whole PDF as one flagged unit (title comes from metadata, not invented)."""
    return [Chapter(1, title or "Untitled", 1, doc.page_count, 0.3, "manual", notes=["no chapters detected; review required"])]

"""Cross-reference detection/resolution (see Chapter N, Table N.M, Fig. N.M)."""
from __future__ import annotations
import re

XREF_RE = re.compile(r"\b(?:see\s+|See\s+)?(Chapter|Chap\.|Table|Fig\.|Figure)\s+(\d{1,4})(?:\.(\d{1,3}))?", )


def find_refs(text: str):
    for m in XREF_RE.finditer(text):
        yield m.start(), m.end(), m.group(1), int(m.group(2)), m.group(3)


def resolve(text: str, current_chapter: int, converted: dict[int, str], unresolved: list[dict], page: int | None = None) -> str:
    """Link refs whose target chapter is converted; record the rest. `converted` maps chapter number -> relative md path.
    Table/Figure N.M link to chapter N. Text is otherwise unchanged."""
    out, last = [], 0
    for s, e, kind, a, b in find_refs(text):
        label = text[s:e]
        # only the 'Kind N(.M)' part becomes the link text (not a leading 'see')
        k = re.search(r"(Chapter|Chap\.|Table|Fig\.|Figure)", label).start()
        pre, core = label[:k], label[k:]
        target_ch = a
        if kind.startswith("Chap") and False:
            pass
        if target_ch == current_chapter and not kind.startswith("Chap"):
            out.append(text[last:e]); last = e
            continue
        out.append(text[last:s] + pre)
        if target_ch in converted:
            anchor = ""
            if kind in ("Table",):
                anchor = f"#table-{a}-{b}" if b else ""
            elif kind in ("Fig.", "Figure"):
                anchor = f"#figure-{a}-{b}" if b else ""
            out.append(f"[{core}]({converted[target_ch]}{anchor})")
        else:
            out.append(core)
            unresolved.append({"text": core, "kind": kind, "target_chapter": target_ch, "page": page, "from_chapter": current_chapter})
        last = e
    out.append(text[last:])
    return "".join(out)

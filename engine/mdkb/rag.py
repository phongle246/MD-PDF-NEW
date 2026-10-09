"""RAG packaging: semantic-section chunks of the source Markdown (no summarising)."""
from __future__ import annotations
import json, re
from .util import atomic_write, read_json

MAX_CHARS = 1800


def clean_for_rag(md: str) -> str:
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    md = re.sub(r'<a id="[^"]*"></a>', "", md)
    return md.strip()


def build_chunks(project, ch, A, items, filename) -> list[dict]:
    d = project.data
    sections: list[dict] = []
    cur = None
    for o in items:
        if o.kind in ("chapter_title", "footnote") or (o.kind == "figure" and not o.md):
            continue
        sid = o.extra.get("section_id") or f"{d['slug']}-ch{ch.number}"
        if o.kind == "heading" or cur is None or cur["section_id"] != sid:
            cur = {"section_id": sid, "heading_path": o.extra.get("heading_path", []), "paras": [], "pages": set(), "types": set()}
            sections.append(cur)
        if o.kind != "heading":
            cur["paras"].append(o)
        cur["pages"].add(o.page)
        cur["types"].add(o.kind)
    chunks, n = [], 0
    for s in sections:
        groups, buf = [], []
        for o in s["paras"]:
            t = clean_for_rag(o.md)
            if not t:
                continue
            if buf and sum(len(x[0]) for x in buf) + len(t) > MAX_CHARS:
                groups.append(buf); buf = []
            buf.append((t, o))
        if buf:
            groups.append(buf)
        for gi, g in enumerate(groups):
            n += 1
            pages = sorted({o.page for _, o in g} or s["pages"])
            ctype = "reference" if all(o.kind == "reference" for _, o in g) else "table" if all(o.kind == "table" for _, o in g) else "text"
            refs = sorted({int(x) for t, _ in g for x in re.findall(r"\[(\d{1,3})\]", t)})[:50]
            text = "\n\n".join(t for t, _ in g)
            if s["heading_path"]:
                text = " > ".join(s["heading_path"]) + "\n\n" + text
            chunks.append({"id": f"{s['section_id']}#{gi + 1}", "book": d["title"], "edition": d["edition"], "chapter": ch.number,
                           "chapter_title": ch.title, "section_id": s["section_id"], "heading_path": s["heading_path"],
                           "source_pages": pages, "text": text, "content_type": ctype, "markdown_file": f"chapters/{filename}",
                           **({"references": refs} if refs else {})})
    return chunks


def write_chapter_chunks(project, number, chunks):
    atomic_write(project.state_dir / "state" / project.ch_key(number) / "chunks.jsonl",
                 "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + ("\n" if chunks else ""))


def merge_all(project):
    out = []
    for c in project.chapters():
        f = project.state_dir / "state" / project.ch_key(c.number) / "chunks.jsonl"
        if f.exists():
            out.append(f.read_text("utf-8"))
    atomic_write(project.root / "rag" / "chunks.jsonl", "".join(out))

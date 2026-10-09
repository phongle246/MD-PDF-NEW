"""Local full-text search (SQLite FTS5). The DB is a rebuildable cache, not canonical data."""
from __future__ import annotations
import re, sqlite3
from pathlib import Path
from .rag import clean_for_rag


def _conn(project):
    p = project.state_dir / "search.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS blocks USING fts5(text, chapter UNINDEXED, chapter_title UNINDEXED, heading_path UNINDEXED, "
              "section_id UNINDEXED, page UNINDEXED, block_id UNINDEXED, type UNINDEXED, md_file UNINDEXED, specialty UNINDEXED, tokenize='porter unicode61')")
    return c


def index_chapter(project, number, ch, filename, items, chunks=None):
    c = _conn(project)
    with c:
        c.execute("DELETE FROM blocks WHERE chapter = ?", (number,))
        for o in items:
            if o.kind in ("chapter_title", "figure") and not o.md:
                continue
            t = clean_for_rag(o.md if o.kind != "table" else _table_text(o))
            if not t:
                continue
            c.execute("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (t, number, ch.title, " > ".join(o.extra.get("heading_path", [])), o.extra.get("section_id", ""),
                       o.page, o.block_id, o.kind, f"chapters/{filename}", project.data.get("specialty", "")))
    c.close()


def _table_text(o):
    return re.sub(r"<[^>]+>|\|", " ", o.md)


def reindex_from_source_maps(project):
    """Rebuild FTS from chapters + source maps (used after copying a project folder elsewhere)."""
    import json
    c = _conn(project)
    with c:
        c.execute("DELETE FROM blocks")
    c.close()
    # chunks.jsonl is portable and carries pages/headings
    f = project.root / "rag" / "chunks.jsonl"
    if not f.exists():
        return 0
    c = _conn(project); n = 0
    with c:
        for line in f.read_text("utf-8").splitlines():
            r = json.loads(line)
            c.execute("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (r["text"], r["chapter"], r["chapter_title"], " > ".join(r["heading_path"]), r["section_id"],
                       (r["source_pages"] or [0])[0], r["id"], r["content_type"], r["markdown_file"], ""))
            n += 1
    c.close()
    return n


def _fts_query(q: str) -> str:
    q = q.strip()
    if q.startswith('"') and q.endswith('"') and len(q) > 2:
        return q
    toks = re.findall(r"[\w\-]+", q)
    return " ".join(f'"{t}"' for t in toks) if toks else '""'


def search(project, q: str, chapter: int | None = None, content_type: str | None = None, specialty: str | None = None, limit: int = 30):
    if not q.strip():
        return []
    c = _conn(project)
    sql = ("SELECT snippet(blocks, 0, '[[', ']]', ' … ', 24), chapter, chapter_title, heading_path, section_id, page, block_id, type, md_file, bm25(blocks) "
           "FROM blocks WHERE blocks MATCH ?")
    args: list = [_fts_query(q)]
    if chapter is not None:
        sql += " AND chapter = ?"; args.append(chapter)
    if content_type:
        sql += " AND type = ?"; args.append(content_type)
    if specialty:
        sql += " AND specialty LIKE ?"; args.append(f"%{specialty}%")
    sql += " ORDER BY bm25(blocks) LIMIT ?"
    args.append(limit)
    rows = c.execute(sql, args).fetchall()
    c.close()
    return [{"snippet": r[0], "chapter": r[1], "chapter_title": r[2], "heading_path": r[3], "section_id": r[4], "page": r[5],
             "block_id": r[6], "type": r[7], "markdown_file": r[8], "book": project.title} for r in rows]

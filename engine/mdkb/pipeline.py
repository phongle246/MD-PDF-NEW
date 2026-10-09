"""Chapter conversion: 15 passes, page-level persistence, resumable."""
from __future__ import annotations
import re, shutil, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import pymupdf
from . import APP_VERSION, PARSER_VERSION, cleanup, pagepass, assemble as asm, qa, xrefs, report as reportmod, rag, search
from .models import Issue
from .util import atomic_write, write_json, read_json, sha256_text, fingerprint, slugify, sha256_file
from .project import Project, open_pdf

STAGES = ["Parsing pages", "Reading layout", "Extracting text", "Extracting tables", "Extracting figures",
          "Building Markdown", "Running QA", "Indexing"]


class Interrupted(Exception):
    pass


class Cancelled(Interrupted):
    pass


def _emit(cb, **ev):
    if cb:
        cb(ev)


def convert_chapter(project: Project, number: int, progress_cb=None, should_stop=None, force: bool = False,
                    ocr_enabled: bool = True, resume: bool = True) -> dict:
    ch = project.chapter(number)
    key = project.ch_key(number)
    sdir = project.ch_state_dir(number)
    pages_dir = sdir / "pages"
    if force and sdir.exists():
        shutil.rmtree(sdir)
    pages_dir.mkdir(parents=True, exist_ok=True)
    if not project.verify_source():
        raise RuntimeError("Source PDF is missing or changed since project creation (SHA-256 mismatch).")
    pr = project.progress(number)
    project.set_progress(number, status="PROCESSING", stage=STAGES[0], started=_now(), completed_pages=pr.get("completed_pages", []) if resume else [])
    doc = open_pdf(project.pdf_path)
    img_rel_dir = f"assets/images/{key}"
    assets_dir = project.root / img_rel_dir
    try:
        page_nums = list(range(ch.start_page, ch.end_page + 1))
        # ---- PASS 1/2: source inspection + chapter-level prepass (margins)
        _emit(progress_cb, chapter=number, stage=STAGES[0], page=ch.start_page, done=0, total=len(page_nums))
        pre = read_json(sdir / "prepass.json")
        if not pre:
            lines_by_page, heights = {}, {}
            for p in page_nums:
                pg = doc[p - 1]
                lines_by_page[p] = pagepass.prepass_lines(pg)
                heights[p] = pg.rect.height
            margins = sorted(cleanup.find_repeated_margin_lines(lines_by_page, heights))
            pre = {"margins": margins}
            write_json(sdir / "prepass.json", pre)
        margins = set(pre["margins"])

        fig_counter = {"n": len(list(assets_dir.glob("figure_tmp_*.png"))) if assets_dir.exists() else 0}
        fig_hashes: set = set(read_json(sdir / "fig_hashes.json", []))
        done_pages = set()
        for idx, p in enumerate(page_nums):
            if should_stop and should_stop():
                raise Cancelled()
            pf = pages_dir / f"page_{p:05d}.json"
            if resume and pf.exists():
                try:
                    st = read_json(pf)
                    if st and st.get("page") == p:
                        done_pages.add(p)
                        continue
                except Exception:
                    pass
            _emit(progress_cb, chapter=number, stage=STAGES[1], page=p, done=idx, total=len(page_nums))
            state = pagepass.process_page(doc[p - 1], number, margins, assets_dir, fig_counter, fig_hashes, ocr_enabled=ocr_enabled)
            _emit(progress_cb, chapter=number, stage=STAGES[2], page=p, done=idx, total=len(page_nums))
            write_json(pf, state)
            write_json(sdir / "fig_hashes.json", sorted(fig_hashes))
            done_pages.add(p)
            project.set_progress(number, completed_pages=sorted(done_pages), stage=STAGES[2], last_page=p)
        pages = [read_json(pages_dir / f"page_{p:05d}.json") for p in page_nums]
        return _finish(project, ch, pages, doc, progress_cb, ocr_enabled)
    except Interrupted:
        project.set_progress(number, status="PROCESSING", stage="interrupted")
        raise
    except Exception as e:
        project.set_progress(number, status="FAILED", error=str(e), stage="failed")
        raise
    finally:
        doc.close()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _finish(project: Project, ch, pages: list[dict], doc, progress_cb, ocr_enabled) -> dict:
    number = ch.number
    key = project.ch_key(number)
    issues: list[Issue] = []
    for p in pages:
        for i in p.get("issues", []):
            issues.append(Issue(i["severity"], i["code"], i["message"], i.get("page"), i.get("block_id")))

    # ---- PASS 5: cleanup vocabulary from the whole chapter
    _emit(progress_cb, chapter=number, stage=STAGES[5], page=ch.end_page, done=len(pages), total=len(pages))
    texts = [l["text"] for p in pages for b in p["blocks"] if b["kind"] == "text" for l in b["lines"]]
    mem = project.conversion_memory()
    vocab = cleanup.build_vocab(texts + mem.get("vocab_seed", []))
    convention = {"body_size": None}
    book_slug = project.data["slug"]

    # ---- PASS 6, 7, 10: structure, lists/callouts, references
    A = asm.assemble(pages, ch, book_slug, vocab, convention)
    for i in A.issues:
        issues.append(Issue(i["severity"], i["code"], i["message"], i.get("page"), i.get("block_id")))

    # ---- figures: final names, caption association
    img_dir_rel = f"assets/images/{key}"
    assets = project.root / img_dir_rel
    figure_files: dict[int, str] = {}
    figure_nums: dict[int, str] = {}
    items = A.items
    cap_used = set()
    for idx, o in enumerate(items):
        if o.kind != "figure":
            continue
        fb = A.figures[o.extra["figure"]]["block"]
        # nearest caption after (or before) on the same page
        cap = None
        for j in list(range(idx + 1, min(idx + 4, len(items)))) + list(range(idx - 1, max(idx - 3, -1), -1)):
            c = items[j]
            if c.kind == "caption" and c.extra["caption"][0].startswith("Fig") and j not in cap_used and c.page == o.page:
                cap = j
                break
        if cap is not None:
            cap_used.add(cap)
            ct = items[cap].extra["caption"]
            fnum = f"{ct[1]}_{int(ct[2] or 0):02d}" if ct[2] else f"{ct[1]}"
            o.extra["caption_idx"] = cap
        else:
            fnum = f"{number}_{o.extra['figure'] + 1:02d}"
            issues.append(Issue("MEDIUM", "FIGURE_NO_CAPTION", f"Figure on page {o.page} has no associated caption", o.page, o.block_id))
        fname = f"figure_{fnum}.png"
        src = assets / fb.get("image", "") if fb.get("image") else None
        if src and src.exists():
            dst = assets / fname
            n = 2
            while dst.exists() and dst != src and fname in figure_files.values():
                fname = f"figure_{fnum}_{n}.png"; dst = assets / fname; n += 1
            shutil.move(str(src), str(dst)) if src != dst else None
            figure_files[o.extra["figure"]] = fname
            alt = f"Figure {ct[1]}.{ct[2]}" if cap is not None and ct[2] else "Figure"
            o.md = f"![{alt}]({'../' + img_dir_rel}/{fname})"
            o.extra["file"] = f"{img_dir_rel}/{fname}"
        elif fb.get("image") is None:
            o.md = ""
            issues.append(Issue("HIGH", "FIGURE_FILE_MISSING", f"Figure image missing on page {o.page}", o.page, o.block_id))
    # captions not attached to a figure/table
    for idx, o in enumerate(items):
        if o.kind == "caption" and o.extra["caption"][0].startswith("Fig") and idx not in cap_used:
            issues.append(Issue("MEDIUM", "CAPTION_NO_FIGURE", f"Figure caption without extracted image (page {o.page}): {o.md[:50]}", o.page, o.block_id))

    # ---- tables: large -> separate file; caption linking
    tables_dir = project.root / "tables" / key
    table_records = []
    for idx, o in enumerate(items):
        if o.kind != "table":
            continue
        tb = A.tables[o.extra["table"]]["block"]
        info = tb["table_info"]
        cap = None
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(items) and items[j].kind == "caption" and items[j].extra["caption"][0] == "Table" and j not in cap_used:
                cap = j; cap_used.add(j); break
        label = ""
        tnum = f"{number}_{len(table_records) + 1:02d}"
        if cap is not None:
            c = items[cap].extra["caption"]
            label = f"Table {c[1]}.{c[2]}" if c[2] else f"Table {c[1]}"
            if c[2]:
                tnum = f"{c[1]}_{int(c[2]):02d}"
            items[cap].extra["table_label"] = label
        rec = {"file": None, "rows": info["rows"], "cols": info["cols"], "format": info["format"], "page": o.page, "label": label, "complex": info["complex"], "confidence": tb["confidence"]}
        md = tb["markdown"]
        if info["rows"] > asm.LARGE_TABLE_ROWS:
            tfile = tables_dir / f"table_{tnum}.md"
            atomic_write(tfile, (f"<!-- source_page: {o.page} -->\n\n" + md + "\n"))
            rec["file"] = f"tables/{key}/table_{tnum}.md"
            ref_name = label or f"Table {tnum.replace('_', '.')}"
            o.md = f"[{ref_name} — full table ({info['rows']} rows × {info['cols']} columns)](../{rec['file']})"
            issues.append(Issue("LOW", "TABLE_SEPARATE_FILE", f"Large table stored separately: {rec['file']}", o.page, o.block_id))
        else:
            o.md = md
        # table QA (PASS 8 QA): structure + numeric integrity vs raw cells
        raw_numbers = qa.numeric_tokens(" ".join(" ".join(r) for r in tb["rows"]))
        got = qa.numeric_tokens(md if not rec["file"] else md)
        if raw_numbers - got:
            issues.append(Issue("CRITICAL", "TABLE_NUMERIC_MISMATCH", f"Table on page {o.page}: numbers lost in serialisation", o.page, o.block_id))
        if info["complex"]:
            issues.append(Issue("MEDIUM", "TABLE_COMPLEX", f"Complex table on page {o.page} serialised as HTML; verify merged cells/headers", o.page, o.block_id))
        if tb["confidence"] < 0.7:
            rec["review"] = True
        table_records.append(rec)
        o.extra["record"] = rec
        o.extra["anchor"] = tnum

    # ---- footnote marker linking: <sup>N</sup> on same page -> [^label]
    fn_by_page: dict[int, dict[str, str]] = {}
    for label, text, pno in A.footnotes:
        m = re.match(r"p\d+n(\d+)$", label)
        if m:
            fn_by_page.setdefault(pno, {})[m.group(1)] = label
    for o in items:
        if o.kind in ("paragraph", "list") and o.page in fn_by_page:
            for n_, label in fn_by_page[o.page].items():
                o.md = re.sub(rf"<sup>{n_}</sup>", f"[^{label}]", o.md, count=1)

    # ---- PASS 11: markdown assembly
    _emit(progress_cb, chapter=number, stage=STAGES[5], page=ch.end_page, done=len(pages), total=len(pages))
    converted = project.converted_map()
    converted[number] = None
    rel_converted = {n: (f"{Path(p).name}" if p else "") for n, p in converted.items() if p}
    unresolved: list[dict] = []
    md_blocks: list[tuple[Out, str]] = []
    parts: list[str] = []
    entries: list[dict] = []        # source-map entries
    used_pages: list[int] = []
    last_page = None
    body_chunks: list[tuple[str, dict]] = []   # (md, meta) for body (QA/RAG)
    cap_by_table = {}
    for idx, o in enumerate(items):
        if o.kind in ("chapter_title", "footnote") or (o.kind == "figure" and not o.md):
            if o.kind == "footnote":
                pass
            continue
        text = o.md
        if o.kind in ("paragraph", "list", "callout", "caption"):
            text = xrefs.resolve(text, number, rel_converted, unresolved, o.page)
        if o.kind == "caption":
            tl = o.extra.get("table_label")
            ctype, a, b = o.extra["caption"]
            anchor = ""
            if ctype == "Table":
                anchor = f'<a id="table-{a}-{b}"></a>' if b else ""
            elif b:
                anchor = f'<a id="figure-{a}-{b}"></a>'
            text = f"{anchor}**{text.split('.', 1)[0]}.**{text.split('.', 1)[1]}" if False else f"{anchor}{text}"
        if o.page != last_page:
            parts.append(f"<!-- source_page: {o.page} -->")
            last_page = o.page
            used_pages.append(o.page)
        parts.append(text)
        body_chunks.append((text, {"o": o}))
        entries.append({"block_id": o.block_id, "type": o.kind, "section_id": o.extra.get("section_id", ""),
                        "source_page": o.page, "bbox": [round(x, 1) for x in o.bbox], "markdown_file": f"chapters/{{FILE}}",
                        "heading_path": o.extra.get("heading_path", []), "fingerprint": fingerprint(o.cleaned or o.md),
                        "order": o.order, "confidence": round(o.confidence, 2), "raw_hash": sha256_text(o.raw)[:12] if o.raw else "",
                        **({"merged_from": o.extra["merged"]} if o.extra.get("merged") else {})})
    for label, text, pno in A.footnotes:
        parts.append(f"[^{label}]: {text}")
    body_md = "\n\n".join(p for p in parts if p != "")

    # contents (H2 only, capped)
    h2 = [(t, s) for lvl, t, s in A.headings if lvl == 2][:30]
    contents = ""
    if len(h2) >= 3:
        contents = "## Contents\n\n" + "\n".join(f"- [{t}](#{_gh_anchor(t)})" for t, _ in h2) + "\n\n"
    specialty = project.data.get("specialty", "")
    filename = f"{key}_{slugify(ch.title, 50)}.md"
    fm = _front_matter(project, ch, pages, A, specialty)
    md_full = f"{fm}\n# {ch.title}\n<!-- section_id: {book_slug_id(project, ch)} -->\n\n{contents}{body_md}\n"
    # ---- PASS 12-14: QA
    _emit(progress_cb, chapter=number, stage=STAGES[6], page=ch.end_page, done=len(pages), total=len(pages))
    src_text = " ".join(p.get("src_text", "") for p in pages)
    # remove chapter title page text that we intentionally drop from body
    drop_text = " ".join(" ".join(l["text"] for l in b.get("lines", [])) for p in pages for b in p["blocks"] if False)
    chap_title_raw = " ".join(o.raw for o in items if o.kind == "chapter_title")
    src_for_qa = src_text
    # footnote markers we convert
    md_for_qa = body_md
    issues += qa.numeric_qa(_minus_text(src_for_qa, chap_title_raw), md_for_qa)
    issues += qa.fidelity_qa(qa.word_counter(src_for_qa) - qa.word_counter(chap_title_raw), qa.word_counter(md_for_qa))
    issues += qa.structure_qa([(h[0], h[1]) for h in A.headings])
    issues += qa.citation_qa(body_md.split("<a id=\"ref-")[0], set(A.refs))
    tl = {p["page"]: len(p.get("src_text", "")) for p in pages}
    issues += qa.completeness_qa([p["page"] for p in pages], list(range(ch.start_page, ch.end_page + 1)), tl)
    # figure/caption expected vs extracted
    expected_figs = sum(1 for o in items if o.kind == "caption" and o.extra["caption"][0].startswith("Fig"))
    got_figs = sum(1 for o in items if o.kind == "figure" and o.md)
    if expected_figs > got_figs:
        issues.append(Issue("HIGH", "FIGURE_COUNT", f"{expected_figs} figure captions but {got_figs} figures extracted"))
    for o in items:
        if o.kind == "figure" and o.extra.get("file") and not (project.root / o.extra["file"]).exists():
            issues.append(Issue("HIGH", "FIGURE_BROKEN_PATH", f"Broken image path {o.extra['file']}"))
    expected_tabs = sum(1 for o in items if o.kind == "caption" and o.extra["caption"][0] == "Table")
    if expected_tabs > len(table_records):
        issues.append(Issue("HIGH", "TABLE_COUNT", f"{expected_tabs} table captions but {len(table_records)} tables extracted"))
    issues += _uncertain_issues(items)

    status = qa.status_from(issues, any(t.get("review") for t in table_records))

    # ---- write files
    atomic_write(project.root / "chapters" / filename, md_full)
    for e in entries:
        e["markdown_file"] = f"chapters/{filename}"
    smap = {"chapter": number, "title": ch.title, "markdown_file": f"chapters/{filename}", "source_pdf": project.data["source_name"],
            "source_sha256": project.data["source_sha256"], "pages": [ch.start_page, ch.end_page], "entries": entries,
            "sections": [{"section_id": s, "level": l, "title": t} for l, t, s in A.headings]}
    write_json(project.root / "source_maps" / f"{key}.json", smap)
    write_json(project.root / "metadata" / f"unresolved_{key}.json", unresolved)
    _merge_unresolved(project)

    # ---- PASS 15: index + RAG metadata
    _emit(progress_cb, chapter=number, stage=STAGES[7], page=ch.end_page, done=len(pages), total=len(pages))
    chunks = rag.build_chunks(project, ch, A, items, filename)
    rag.write_chapter_chunks(project, number, chunks)
    rag.merge_all(project)
    search.index_chapter(project, number, ch, filename, items, chunks)

    counts = Counter(i.severity for i in issues)
    rep = reportmod.write(project, ch, filename, status, issues, pages, A, table_records, figure_files, items, unresolved, ocr_enabled)
    md_hash = sha256_text(md_full)
    project.set_progress(number, status=status, stage="done", markdown_file=f"chapters/{filename}", completed_pages=[p["page"] for p in pages],
                         issue_counts=dict(counts), finished=_now(), markdown_sha256=md_hash, report=rep, error=None)
    _update_memory(project, A, margins=read_json(project.ch_state_dir(number) / "prepass.json", {}).get("margins", []), body=asm.body_size(pages))
    from . import bookfiles
    bookfiles.write_all(project)
    _emit(progress_cb, chapter=number, stage="Done", page=ch.end_page, done=len(pages), total=len(pages), status=status)
    return {"chapter": number, "status": status, "markdown_file": f"chapters/{filename}", "issues": [i.to_dict() for i in issues],
            "report": rep, "counts": dict(counts), "figures": len(figure_files), "tables": len(table_records)}


def _gh_anchor(t: str) -> str:
    a = re.sub(r"[^\w\- ]", "", t.lower()).strip().replace(" ", "-")
    return a


def book_slug_id(project, ch):
    return f"{project.data['slug']}-ch{ch.number}"


def _front_matter(project, ch, pages, A, specialty) -> str:
    d = project.data

    def q(s): return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
    tags = []
    lines = ["---",
             f"book_title: {q(d['title'])}", f"edition: {q(d['edition'])}", f"chapter_number: {ch.number}",
             f"chapter_title: {q(ch.title)}", f"source_file: {q(d['source_name'])}",
             f"source_pages: {q(f'{ch.start_page}-{ch.end_page}')}", 'language: "en"', 'content_type: "textbook_chapter"',
             "specialty:"] + ([f"  - {s}" for s in (specialty.split(",") if specialty else [])] or ["  []"][:0]) + \
            ["tags: []", f"source_sha256: {q(d['source_sha256'])}", f"conversion_version: {q(PARSER_VERSION + '/' + APP_VERSION)}",
             f"converted_at: {q(_now())}", "---"]
    return "\n".join(lines) + "\n"


def _uncertain_issues(items) -> list[Issue]:
    out = []
    for o in items:
        if o.kind == "uncertain":
            out.append(Issue("HIGH", "UNCERTAIN_TEXT", f"Unreadable source text on page {o.page}", o.page, o.block_id))
    return out


def _merge_unresolved(project: Project):
    allu = []
    for f in sorted((project.root / "metadata").glob("unresolved_ch_*.json")):
        allu += read_json(f, [])
    write_json(project.root / "metadata" / "unresolved_references.json", allu)


def _update_memory(project: Project, A, margins, body):
    mem = project.conversion_memory()
    mem.setdefault("naming", {"chapter_file": "ch_{nnn}_{slug}.md", "figure": "figure_{chapter}_{nn}.png", "table": "table_{chapter}_{nn}.md"})
    mem["body_font_size"] = body
    mem["running_headers"] = sorted(set(mem.get("running_headers", [])) | set(margins))
    mem["heading_patterns"] = {"levels_seen": sorted({h[0] for h in A.headings})}
    mem["table_rules"] = {"gfm_if_simple": True, "html_if_complex": True, "separate_file_rows_gt": asm.LARGE_TABLE_ROWS}
    project.save_conversion_memory(mem)


def resolve_all_xrefs(project: Project):
    """After converting more chapters, re-link previously unresolved cross-references by re-assembling affected chapters' text."""
    from . import xrefs as X
    unresolved = read_json(project.root / "metadata" / "unresolved_references.json", [])
    conv = {n: Path(p).name for n, p in project.converted_map().items()}
    still, linked = [], 0
    by_ch: dict[int, list[dict]] = {}
    for u in unresolved:
        by_ch.setdefault(u["from_chapter"], []).append(u)
    for fch, us in by_ch.items():
        pr = project.progress(fch)
        mf = pr.get("markdown_file")
        if not mf:
            still += us
            continue
        path = project.root / mf
        text = path.read_text("utf-8")
        for u in us:
            if u["target_chapter"] in conv:
                pat = re.compile(r"(?<!\[)" + re.escape(u["text"]) + r"(?!\]\()")
                new, n = pat.subn(f"[{u['text']}]({conv[u['target_chapter']]})", text, count=1)
                if n:
                    text = new; linked += 1; continue
            still.append(u)
        atomic_write(path, text)
    write_json(project.root / "metadata" / "unresolved_references.json", still)
    return {"linked": linked, "remaining": len(still)}


def _minus_text(src: str, drop: str) -> str:
    """Remove intentionally-dropped tokens (e.g. chapter number on the title line) from source text, one occurrence each."""
    for w in drop.split():
        src = src.replace(w, "", 1)
    return src

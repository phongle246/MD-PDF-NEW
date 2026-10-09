import json, re, zipfile, shutil
from pathlib import Path
import pytest, pymupdf
import fixtures
from mdkb import pipeline, search, export, rag, ocr, indexes
from mdkb.project import Project, inspect_pdf, PDFError, open_pdf
from mdkb.util import read_json, sha256_file


def md_of(project, n):
    return (project.root / project.progress(n)["markdown_file"]).read_text("utf-8")


def test_inspect_detects_chapters_from_outline(book_pdf):
    info = inspect_pdf(book_pdf)
    assert [(c["number"], c["title"], c["start_page"], c["end_page"]) for c in info["chapters"]] == \
        [(1, "Thalassemia and Anemia", 1, 3), (2, "Asthma in Children", 4, 5)]
    assert info["has_outline"] and info["native_text_ratio"] == 1.0


def test_chapter_detection_without_outline(tmp_path):
    doc = pymupdf.open()
    for k, name in enumerate(["Alpha Topic", "Beta Topic"], 1):
        for pg in range(2):
            p = doc.new_page()
            if pg == 0:
                p.insert_text((72, 100), f"Chapter {k} {name}", fontsize=24)
            for j in range(12):
                p.insert_text((72, 160 + j * 14), "ordinary body text line that fills the page nicely", fontsize=10)
    doc.save(tmp_path / "n.pdf")
    chs = inspect_pdf(tmp_path / "n.pdf")["chapters"]
    assert [(c["number"], c["title"], c["start_page"], c["end_page"]) for c in chs] == [(1, "Alpha Topic", 1, 2), (2, "Beta Topic", 3, 4)]
    assert chs[0]["source"] == "heading-pattern"


def test_encrypted_and_corrupt_pdf_errors(tmp_path):
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"not a pdf")
    with pytest.raises(PDFError):
        open_pdf(bad)
    doc = pymupdf.open(); doc.new_page(); enc = tmp_path / "enc.pdf"
    doc.save(enc, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    with pytest.raises(PDFError, match="encrypted"):
        open_pdf(enc)


def test_convert_chapter_markdown_quality(project):
    r = pipeline.convert_chapter(project, 1)
    assert r["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")
    md = md_of(project, 1)
    assert md.startswith("---\n") and 'chapter_number: 1' in md and "source_sha256" in md          # YAML front matter
    assert "pediatric patient" in md and "pedi-" not in md                                           # dehyphenation
    assert "Long-term" in md                                                                          # real hyphen kept
    # two-column order: left column first, then right column
    assert md.index("Anemia is defined") < md.index("Thalassemia is an inherited") < md.index("The second column continues")
    assert "fi" in md and "## Definition and Epidemiology" in md and "### Pathophysiology" in md
    assert "<!-- source_page: 1 -->" in md and "<!-- source_page: 2 -->" in md
    assert "Test Textbook of Pediatrics" not in md.split("---", 2)[2]                               # running header removed
    assert "| Type | Hb (g/dL) | MCV (fL)" in md and "| Major | <7.0 | 55 |" in md                    # GFM table
    assert re.search(r"!\[Figure 1.1\]\(\.\./assets/images/ch_001/figure_1_01\.png\)", md)
    assert "Figure 1.1. Peripheral blood smear showing microcytosis." in md                          # caption verbatim
    assert "> **KEY POINTS**" in md and "> - Screen early" in md                                      # callout + list
    assert '<a id="ref-3"></a>[3] Author D.' in md and "doi:10.0000/abc" in md                       # references verbatim
    assert "[2,3]" in md                                                                              # citations untouched
    assert (project.root / "assets/images/ch_001/figure_1_01.png").stat().st_size > 100


def test_ordered_list_and_crossref_second_chapter(project):
    pipeline.convert_chapter(project, 1); pipeline.convert_chapter(project, 2)
    md = md_of(project, 2)
    assert "1. Assess severity.\n2. Give bronchodilator.\n3. Reassess after 1 hour." in md
    assert "[Chapter 1](ch_001_thalassemia-and-anemia.md)" in md
    assert not read_json(project.root / "metadata/unresolved_references.json")


def test_unresolved_xref_then_resolved(project):
    pipeline.convert_chapter(project, 2)
    un = read_json(project.root / "metadata/unresolved_references.json")
    assert {u["text"] for u in un} == {"Chapter 1", "Table 1.1"}
    pipeline.convert_chapter(project, 1)
    res = pipeline.resolve_all_xrefs(project)
    assert res["linked"] == 2 and res["remaining"] == 0
    assert "[Chapter 1](ch_001_thalassemia-and-anemia.md)" in md_of(project, 2)


def test_source_map_traceability(project):
    pipeline.convert_chapter(project, 1)
    sm = read_json(project.root / "source_maps/ch_001.json")
    assert sm["source_sha256"] == project.data["source_sha256"]
    e = [x for x in sm["entries"] if "hemoglobin" in x["fingerprint"] or True]
    assert all(x["source_page"] in (1, 2, 3) and len(x["bbox"]) == 4 and x["markdown_file"].startswith("chapters/") for x in sm["entries"])
    secs = {s["section_id"] for s in sm["sections"]}
    assert "test-textbook-of-pediatrics-ch1-definition-and-epidemiology" in secs
    assert any(x["type"] == "table" and x["source_page"] == 2 for x in sm["entries"])


def test_stable_section_ids_across_reruns(project):
    pipeline.convert_chapter(project, 1)
    a = [s["section_id"] for s in read_json(project.root / "source_maps/ch_001.json")["sections"]]
    pipeline.convert_chapter(project, 1, force=True)
    b = [s["section_id"] for s in read_json(project.root / "source_maps/ch_001.json")["sections"]]
    assert a == b


def test_numeric_qa_clean_for_fixture(project):
    r = pipeline.convert_chapter(project, 1)
    assert not [i for i in r["issues"] if "NUMBER" in i["code"] or i["code"].startswith("TEXT_LOSS")]


def test_conversion_report_and_files(project):
    pipeline.convert_chapter(project, 1)
    rep = (project.root / "reports/ch_001_conversion_report.md").read_text()
    for h in ("## Status", "## Numeric QA", "## Citation QA", "## Tables", "## Figures", "## OCR", "## Human Review Recommended"):
        assert h in rep
    for f in ("README.md", "00_BOOK_INDEX.md", "book.yaml", "manifest.json", "metadata/conversion_memory.json", "rag/chunks.jsonl"):
        assert (project.root / f).exists(), f
    idx = (project.root / "00_BOOK_INDEX.md").read_text()
    assert "[Thalassemia and Anemia](chapters/ch_001_thalassemia-and-anemia.md)" in idx and "Completed" in idx
    man = read_json(project.root / "manifest.json")
    assert man["source_sha256"] == project.data["source_sha256"] and man["chapter_files"][0]["file"].startswith("chapters/")
    assert any(a.endswith("figure_1_01.png") for a in man["assets"])


def test_rag_chunks_semantic_and_faithful(project):
    pipeline.convert_chapter(project, 1)
    chunks = [json.loads(l) for l in (project.root / "rag/chunks.jsonl").read_text().splitlines()]
    assert chunks and all({"id", "book", "chapter", "section_id", "heading_path", "source_pages", "text", "content_type"} <= set(c) for c in chunks)
    c = next(c for c in chunks if "Anemia is defined" in c["text"])
    assert c["heading_path"] == ["Definition and Epidemiology"] and c["source_pages"] == [1]
    assert not any("<!--" in c["text"] for c in chunks)


def test_search_open_markdown_and_source_page(project):
    pipeline.convert_chapter(project, 1); pipeline.convert_chapter(project, 2)
    res = search.search(project, "deferasirox")
    assert res and res[0]["chapter"] == 1 and res[0]["page"] == 1 and "[[deferasirox]]" in res[0]["snippet"]
    assert res[0]["heading_path"].endswith("Pathophysiology")
    assert search.search(project, "salbutamol", chapter=2)[0]["page"] == 4
    assert search.search(project, "salbutamol", chapter=1) == []
    assert search.search(project, "ferritin", content_type="paragraph")


def test_search_rebuild_from_portable_files(project):
    pipeline.convert_chapter(project, 1)
    shutil.rmtree(project.state_dir / "search.sqlite", ignore_errors=True)
    (project.state_dir / "search.sqlite").unlink(missing_ok=True)
    assert search.reindex_from_source_maps(project) > 0
    assert search.search(project, "reticulocyte")


def test_resume_after_interruption(project):
    calls = {"n": 0}
    def stop():
        calls["n"] += 1
        return calls["n"] > 2          # allow two pages, then "crash"
    with pytest.raises(pipeline.Interrupted):
        pipeline.convert_chapter(project, 1, should_stop=stop)
    pr = project.progress(1)
    assert pr["status"] == "PROCESSING" and pr["completed_pages"] == [1, 2]
    assert project.chapter_status(1) == "PARTIAL"                               # nobody is running it: app died
    reopened = Project(project.root)                                            # "reopen app"
    seen = []
    pipeline.convert_chapter(reopened, 1, progress_cb=lambda e: seen.append(e["page"]) if e["stage"] == "Reading layout" else None)
    assert seen == [3]                                                           # only the missing page was processed
    assert reopened.progress(1)["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")
    clean = Project.create(project.root.parent / "clean", project.pdf_path)
    pipeline.convert_chapter(clean, 1)
    strip = lambda t: re.sub(r"converted_at:.*", "", t)
    assert strip(md_of(reopened, 1)) == strip(md_of(clean, 1))


def test_state_survives_reopen_multiple_chapters(project):
    pipeline.convert_chapter(project, 1); pipeline.convert_chapter(project, 2)
    ov = Project(project.root).overview()
    assert [c["status"] for c in ov["chapters"]] == ["COMPLETED", "COMPLETED"] and ov["percent"] == 100


def test_source_pdf_never_modified(project):
    before = sha256_file(project.pdf_path)
    pipeline.convert_chapter(project, 1)
    assert sha256_file(project.pdf_path) == before == project.data["source_sha256"]


def test_changed_source_is_refused(project, tmp_path):
    pipeline.convert_chapter(project, 1)
    with open(project.pdf_path, "ab") as f:
        f.write(b"\n%tampered")
    with pytest.raises(RuntimeError, match="SHA-256"):
        pipeline.convert_chapter(project, 2)
    fixtures.two_column_book(project.pdf_path)   # restore (deterministic content differs only by ids) -- tolerate


def test_many_figures_chapter(tmp_path):
    pdf = fixtures.two_column_book(tmp_path / "f.pdf", many_figures=True)
    p = Project.create(tmp_path / "pf", pdf)
    r = pipeline.convert_chapter(p, 1)
    files = sorted(f.name for f in (p.root / "assets/images/ch_001").glob("*.png"))
    assert len(files) == 4 and files[0] == "figure_1_01.png" and not list((p.root / "assets/images/ch_001").glob("figure_tmp*"))
    md = md_of(p, 1)
    for i in range(1, 5):
        assert f"Figure 1.{i}." in md
    assert not [i for i in r["issues"] if i["code"] in ("FIGURE_COUNT", "FIGURE_NO_CAPTION", "CAPTION_NO_FIGURE")]


def test_scanned_page_without_ocr_is_marked_uncertain(tmp_path):
    pdf = fixtures.two_column_book(tmp_path / "s.pdf", with_scanned=True)
    p = Project.create(tmp_path / "ps", pdf)
    assert p.chapter(3).start_page == 6
    r = pipeline.convert_chapter(p, 3, ocr_enabled=False)
    md = md_of(p, 3)
    assert "<!-- UNCERTAIN: source text unreadable, page 6 -->" in md and "[Unreadable source text]" in md
    assert r["status"] == "REVIEW_REQUIRED" and any(i["code"] == "UNREADABLE_PAGE" for i in r["issues"])


def test_scanned_page_with_ocr_backend_is_flagged(tmp_path):
    pdf = fixtures.two_column_book(tmp_path / "s.pdf", with_scanned=True)
    p = Project.create(tmp_path / "ps", pdf)
    ocr.set_backend(lambda png: ("Dose is 2.5 mg daily\nfor children", 0.7))
    try:
        r = pipeline.convert_chapter(p, 3)
    finally:
        ocr.set_backend(None)
    assert "Dose is 2.5 mg daily for children" in md_of(p, 3)
    codes = {i["code"] for i in r["issues"]}
    assert {"OCR_USED", "OCR_NUMERIC_UNVERIFIED"} <= codes and r["status"] == "REVIEW_REQUIRED"


def test_native_pages_never_ocrd(project):
    called = []
    ocr.set_backend(lambda png: called.append(1) or ("x", 1.0))
    try:
        pipeline.convert_chapter(project, 1)
    finally:
        ocr.set_backend(None)
    assert called == []


def test_large_table_goes_to_separate_file(tmp_path):
    doc = pymupdf.open(); p = doc.new_page()
    doc.set_toc([[1, "Chapter 1 Big Table", 1]])
    p.insert_text((72, 60), "Table 1.1 Big values", fontsize=10)
    for r in range(45):
        for c in range(3):
            rect = pymupdf.Rect(72 + c * 90, 70 + r * 15, 72 + (c + 1) * 90, 70 + (r + 1) * 15)
            p.draw_rect(rect, width=0.5); p.insert_text((rect.x0 + 3, rect.y0 + 11), f"{r}.{c}5" if r else f"H{c}", fontsize=8)
    doc.save(tmp_path / "t.pdf")
    pr = Project.create(tmp_path / "pt", tmp_path / "t.pdf")
    res = pipeline.convert_chapter(pr, 1)
    tf = pr.root / "tables/ch_001/table_1_01.md"
    assert tf.exists() and "44.25" in tf.read_text()
    assert "(../tables/ch_001/table_1_01.md)" in md_of(pr, 1)


def test_export_zip_portable(project, tmp_path):
    pipeline.convert_chapter(project, 1)
    z = export.export_zip(project, tmp_path / "out.zip")
    out = tmp_path / "unz"
    zipfile.ZipFile(z).extractall(out)
    assert not (out / ".mdkb").exists() and not list(out.rglob("*.pdf"))
    md = (out / project.progress(1)["markdown_file"]).read_text()
    for img in re.findall(r"\]\((\.\./assets/[^)]+)\)", md):
        assert (out / "chapters" / img).resolve().exists()
    z2 = export.export_zip(project, tmp_path / "pdf.zip", include_pdf=True)
    assert any(n.startswith("source/") for n in zipfile.ZipFile(z2).namelist())
    z3 = export.export_zip(project, tmp_path / "rag.zip", rag_only=True)
    assert "rag/chunks.jsonl" in zipfile.ZipFile(z3).namelist()
    z4 = export.export_zip(project, tmp_path / "ch.zip", chapter=1)
    assert any("figure_1_01" in n for n in zipfile.ZipFile(z4).namelist())


def test_manual_chapter_correction(project):
    project.set_chapters([{"number": 1, "title": "Whole Thing", "start_page": 1, "end_page": 5}])
    assert [c.title for c in project.chapters()] == ["Whole Thing"] and project.chapters()[0].source == "manual"


def test_indexes_are_labelled_navigation(project):
    pipeline.convert_chapter(project, 1)
    indexes.build(project)
    t = (project.root / "indexes/diseases.md").read_text()
    assert "Generated navigation index — not part of the original textbook." in t and "thalassemia" in t.lower()


def test_low_confidence_chapters_flagged(tmp_path):
    from mdkb.models import Chapter
    from mdkb import chapters
    c = chapters.validate([Chapter(1, "A", 1, 5), Chapter(1, "B", 6, 9)], 10)
    assert all(x.confidence < 0.6 for x in c) and len(chapters.needs_review(c)) == 2


def test_chapters_sharing_a_page(tmp_path):
    pdf = fixtures.shared_page_book(tmp_path / "s.pdf")
    p = Project.create(tmp_path / "ps", pdf, title="Test Book")
    chs = {c.number: c for c in p.chapters()}
    assert (chs[10].start_page, chs[10].end_page, chs[11].start_page, chs[11].end_page) == (1, 2, 2, 2)
    pipeline.convert_chapter(p, 10); pipeline.convert_chapter(p, 11)
    a, b = md_of(p, 10), md_of(p, 11)
    assert "Complications are rare in 3% of children." in a and "Beta disorders" not in a          # chapter 10 ends at chapter 11's heading
    assert "Beta disorders affect 12% of infants." in b and "Complications" not in b              # nothing from chapter 10 leaks in
    assert "Chapter 10  Alpha" not in a and "# Beta Disorders" in b
    assert "## OUTCOME" in a and "OUTCOME" not in b                                                 # previous chapter's tail heading does not leak
    assert "\x01" not in a and "\x01" not in b                                                       # undecodable footer dropped


def test_heading_levels_from_typography_and_byline(tmp_path):
    pdf = fixtures.shared_page_book(tmp_path / "s.pdf")
    p = Project.create(tmp_path / "ps", pdf, title="Test Book")
    pipeline.convert_chapter(p, 10)
    md = md_of(p, 10)
    assert "\n## ETIOLOGY\n" in md and "\n## PROGNOSIS\n" in md and "\n### Clinical Course\n" in md   # same style -> same level
    assert "*Jane Q. Author*" in md and "## Jane" not in md                                           # byline is not a heading


def test_composite_figure_keeps_label_text(tmp_path):
    pdf = fixtures.shared_page_book(tmp_path / "s.pdf")
    p = Project.create(tmp_path / "ps", pdf, title="Test Book")
    r = pipeline.convert_chapter(p, 10)
    md = md_of(p, 10)
    assert "<summary>Figure text / labels</summary>" in md and "- WARNING SIGNS" in md and "- Liver: AST >= 1000" in md
    assert "Fig. 10.1 Case classification and severity levels." in md
    assert (p.root / "assets/images/ch_010/figure_10_01.png").exists()
    assert not [i for i in r["issues"] if i["code"] in ("FIGURE_NO_CAPTION", "CAPTION_NO_FIGURE", "TEXT_LOSS")]


def test_font_encoding_artifacts_repaired_and_logged(tmp_path):
    pdf = fixtures.shared_page_book(tmp_path / "s.pdf")
    p = Project.create(tmp_path / "ps", pdf, title="Test Book")
    r = pipeline.convert_chapter(p, 10)
    md = md_of(p, 10)
    assert "The disease is common. There are many cases." in md and "after onset" in md
    assert any(i["code"] == "ARTIFACT_REPAIRED" for i in r["issues"])
    assert not [i for i in r["issues"] if "NUMBER" in i["code"]]


def test_junk_footer_does_not_break_two_column_order(tmp_path):
    pdf = fixtures.two_column_with_junk_footer(tmp_path / "j.pdf")
    p = Project.create(tmp_path / "pj", pdf, title="Junk")
    pipeline.convert_chapter(p, 5)
    md = md_of(p, 5)
    assert md.index("Left column first") < md.index("Left column second") < md.index("Right column paragraph")

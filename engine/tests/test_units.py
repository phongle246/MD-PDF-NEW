from collections import Counter
import json
from mdkb import cleanup, qa, tables, xrefs, layout, inline
from mdkb.util import stable_section_id, fingerprint, slugify


def V(*t): return cleanup.build_vocab(list(t))


def test_dehyphenation_joins_broken_word():
    assert cleanup.join_lines(["the pedi-", "atric patient"], V("the patient")) == "the pediatric patient"


def test_dehyphenation_keeps_real_compounds():
    assert cleanup.join_lines(["a long-", "term plan"], V("long-term plan")) == "a long-term plan"
    assert cleanup.join_lines(["case-", "control study"], V("case control")) == "case-control study"


def test_dehyphenation_keeps_capitalised_continuation():
    assert cleanup.join_lines(["Smith-", "Jones syndrome"], V("")) == "Smith-Jones syndrome"


def test_dehyphenation_uses_book_vocabulary():
    assert cleanup.join_lines(["anti-", "body titres"], V("antibody levels")) == "antibody titres"


def test_vocab_ignores_line_end_fragments():
    v = V("end of pedi-", "atric ward")
    assert "pedi" not in v.words


def test_line_joining_into_paragraph():
    assert cleanup.join_lines(["one two", "three  four", "five"]) == "one two three four five"


def test_ligatures_and_greek_preserved():
    s = cleanup.normalize_chars("ﬁrst ﬂow α-thalassemia β 5 µg ≥ 3")
    assert s.startswith("first flow") and "α" in s and "β" in s and "µg" in s and "≥" in s


def test_repeated_margin_lines_detected():
    pages = {i: [("Running Header Title", 20), (f"{i}", 780), ("body text", 300)] for i in range(1, 6)}
    m = cleanup.find_repeated_margin_lines(pages, {i: 800 for i in pages})
    assert "running header title" in m and "#" in m


def test_numeric_fingerprint_detects_changes():
    src = "Give 2.5 mg every 8 hours; ferritin 1000 ng/mL (range 500-1000) in 25% [3]."
    assert qa.numeric_qa(src, src) == []
    changed = src.replace("2.5", "25")
    codes = {i.code for i in qa.numeric_qa(src, changed)}
    assert {"MISSING_NUMBER", "UNEXPECTED_NUMBER", "CHANGED_NUMBER"} <= codes


def test_numeric_missing_dose_is_high_severity():
    iss = qa.numeric_qa("Give 2.5 mg daily", "Give mg daily")
    assert any(i.severity == "HIGH" and i.code == "MISSING_NUMBER" for i in iss)


def test_numeric_ignores_markup_and_page_comments():
    assert qa.numeric_qa("dose 5 mg", "<!-- source_page: 99 -->\n\n![Figure 1.1](a_1_01.png) dose **5** mg<sup>2</sup>".replace("<sup>2</sup>", "")) == []


def test_citation_qa():
    assert qa.citation_qa("text [1] and [2–3]", {1, 2, 3}) == []
    assert qa.citation_qa("text [9]", {1}) and qa.citation_qa("text [1]", set())[0].code == "REFERENCES_MISSING"
    assert qa.expand_citation("23–25") == {23, 24, 25}


def test_structure_qa_flags_skips():
    assert qa.structure_qa([(1, "a"), (2, "b"), (3, "c")]) == []
    assert qa.structure_qa([(2, "a"), (4, "b")])[0].code == "HEADING_SKIP"


def test_table_serialisation_simple_gfm():
    md, info = tables.serialise([["A", "B"], ["1", "2.5"], ["3", "4"]])
    assert info["format"] == "gfm" and md.splitlines()[1].startswith("| ---") and "2.5" in md


def test_table_serialisation_complex_html():
    md, info = tables.serialise([["Group", "", "Total"], ["", "n", ""], ["x", "1", "2"]])
    assert info["format"] == "html" and "<table>" in md and "<th>Group</th>" in md


def test_table_cells_escape_pipes():
    assert "\\|" in tables.to_gfm([["a|b", "c"], ["1", "2"]])


def test_stable_ids_deterministic_and_unique():
    used = set()
    a = stable_section_id("nelson22", 123, ["Clinical Manifestations"], used)
    b = stable_section_id("nelson22", 123, ["Clinical Manifestations"], used)
    assert a == "nelson22-ch123-clinical-manifestations" and b == a + "-2"
    assert stable_section_id("nelson22", 123, ["Clinical Manifestations"], set()) == a


def test_xref_resolution_links_converted_and_records_rest():
    unresolved = []
    out = xrefs.resolve("see Chapter 1 and Table 1.2, also Chapter 201.", 5, {1: "ch_001_a.md"}, unresolved, 7)
    assert "[Chapter 1](ch_001_a.md)" in out and "[Table 1.2](ch_001_a.md#table-1-2)" in out
    assert "Chapter 201." in out and "[Chapter 201" not in out
    assert unresolved == [{"text": "Chapter 201", "kind": "Chapter", "target_chapter": 201, "page": 7, "from_chapter": 5}]


def test_inline_sub_superscripts():
    assert inline.render_spans([{"text": "PaO", "bold": False, "italic": False}, {"text": "2", "bold": False, "italic": False, "sub": True}]) == "PaO₂"
    assert inline.render_spans([{"text": "HCO3", "bold": False, "italic": False}, {"text": "-", "bold": False, "italic": False, "sup": True}]).endswith("⁻")
    assert "<sup>12</sup>" in inline.render_spans([{"text": "text", "bold": False, "italic": False}, {"text": "12", "bold": False, "italic": False, "sup": True}])


def _rb(x0, y0, x1, y1):
    return layout.RawBlock((x0, y0, x1, y1), [layout.Line("t", (x0, y0, x1, y1), 10, False, False, [])])


def test_reading_order_two_column_never_interleaves():
    blocks = [_rb(72, 100, 290, 130), _rb(320, 100, 540, 130), _rb(72, 140, 290, 170), _rb(320, 140, 540, 170),
              _rb(72, 180, 290, 210), _rb(320, 180, 540, 210)]
    cols = [b.bbox[0] for b in layout.order_blocks(blocks, 612)]
    assert cols == [72, 72, 72, 320, 320, 320]


def test_reading_order_full_width_heading_splits_bands():
    h = _rb(72, 60, 540, 80)
    l1, r1, l2, r2 = _rb(72, 100, 290, 130), _rb(320, 100, 540, 130), _rb(72, 140, 290, 170), _rb(320, 140, 540, 170)
    wide = _rb(72, 200, 540, 220)
    l3, r3 = _rb(72, 240, 290, 270), _rb(320, 240, 540, 270)
    order = layout.order_blocks([r3, l3, wide, r2, l2, r1, l1, h], 612)
    assert [(b.bbox[0], b.bbox[1]) for b in order] == [(72, 60), (72, 100), (72, 140), (320, 100), (320, 140), (72, 200), (72, 240), (320, 240)]


def test_reading_order_single_column():
    order = layout.order_blocks([_rb(72, 200, 540, 230), _rb(72, 100, 540, 130)], 612)
    assert [b.bbox[1] for b in order] == [100, 200]


def test_slug_and_fingerprint():
    assert slugify("Cystic Fibrosis: Overview") == "cystic-fibrosis-overview"
    assert fingerprint("A  b\nC") == fingerprint("a b c")

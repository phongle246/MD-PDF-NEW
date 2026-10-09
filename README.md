# MD-PDF — Textbook PDF → Structured Markdown Knowledge Base

A **local-first desktop application** that converts textbook PDFs (e.g. *Nelson Textbook of Pediatrics*) into a
**source-faithful, traceable Markdown knowledge base**: per-chapter Markdown with YAML metadata, extracted figures,
preserved tables/references, page-level source maps, full-text search, side-by-side PDF/Markdown review, and RAG-ready chunks.

> Canonical rule: *every Markdown block can be traced back to its page and bounding box in the original PDF.* No summarising,
> paraphrasing, translating or "fixing" of textbook text. The output is a clean English intermediate representation that a
> later Vietnamese translation workflow can trust.

## Tech stack (and why)

| Layer | Choice | Why |
|---|---|---|
| Document engine | **Python 3.10+ / PyMuPDF** (+ stdlib `sqlite3` FTS5, `http.server`) | Best open text-native PDF layout data (spans, fonts, bboxes, drawings, tables); zero heavy deps. Resumable, long-running work is easy in a separate process. |
| UI | **React + TypeScript (Vite)** | Document-focused UI, dark mode. |
| Desktop shell | **Tauri 2** (Rust) | Small, native file dialogs, spawns/stops the Python sidecar. |
| Link | Local HTTP API on `127.0.0.1:8765` | Keeps the engine usable from CLI, UI, or tests. |

Tauri was kept as requested. The pdfplumber dependency from the brief was **not** needed: PyMuPDF's `find_tables()` covers MVP table needs, which keeps installation to a single wheel.

## Architecture

```
React UI (app/) ──HTTP──▶ mdkb engine (engine/mdkb)  ──▶ Book_Project/ (plain Markdown + JSON)
      ▲ Tauri shell (src-tauri/) starts/stops engine        └─ .mdkb/  (rebuildable cache: page state, FTS5 index)
```

Pipeline per chapter (`engine/mdkb/pipeline.py`), 15 passes:

| Pass | Where | What |
|---|---|---|
| 1 Source inspection | `project.inspect_pdf`, `pagepass` | metadata, outline, native-text vs scanned check |
| 2 Layout / 3 Reading order | `layout.py` | gutter detection; full-width blocks split bands; **left column → right column**, never line-interleaved |
| 4 Text extraction | `pagepass.py` | spans with bold/italic/sub/sup; raw text kept alongside cleaned text |
| 5 Cleanup | `cleanup.py` | join wrapped lines, running header/footer removal, ligatures, **safe dehyphenation** (vocabulary-based; `long-term` is kept) |
| 6 Headings | `assemble.py` | font-size ranking → levels, bold-only fallback flagged at low confidence, stable section IDs |
| 7 Lists / callouts | `assemble.py` | nested bullets/numbered lists, KEY POINTS-style boxes → blockquote |
| 8 Tables | `tables.py` | GFM when simple, HTML when merged/ragged, separate file when > 40 rows |
| 9 Figures | `pagepass.py` | PNG per figure (raster + vector), caption association, duplicate detection |
| 10 Citations / references | `assemble.py`, `xrefs.py` | markers untouched, reference list verbatim with `ref-N` anchors, cross-reference linking |
| 11 Markdown assembly | `pipeline.py` | YAML front matter, Contents, `<!-- source_page: N -->`, footnotes |
| 12–14 QA | `qa.py` | word-recall, **numeric fingerprint** (MISSING/CHANGED/UNEXPECTED), table numerics, citations, headings, completeness, figures |
| 15 Index / RAG | `rag.py`, `search.py`, `indexes.py` | semantic-section chunks, FTS5, navigation indexes |

Persistence: each page's extraction is written atomically to `.mdkb/state/ch_NNN/pages/` as soon as it is done. A crash
leaves the chapter `PARTIAL`; re-running only processes missing pages (`tests/test_pipeline.py::test_resume_after_interruption`).

## Project (output) folder

```
Book_Project/
├── README.md  00_BOOK_INDEX.md  book.yaml  manifest.json
├── chapters/ch_001_title.md        # YAML front matter + source-faithful Markdown
├── assets/images/ch_001/figure_1_01.png
├── tables/ch_001/table_1_04.md     # only for very large tables
├── source_maps/ch_001.json         # block_id, section_id, source_page, bbox, heading_path, fingerprint
├── indexes/{diseases,drugs,signs_symptoms,topics}.md   # GENERATED navigation — never textbook text
├── rag/chunks.jsonl
├── reports/ch_001_conversion_report.md
├── metadata/{unresolved_references.json,conversion_memory.json}
└── .mdkb/                           # app cache only; safe to delete, everything else is plain files
```

## Markdown conventions

- **Page markers:** `<!-- source_page: 2471 -->` at every PDF page change. The UI's *Source mode* shows `[Trang gốc: 2471]`.
- **Section IDs:** `<!-- section_id: nelson22-ch123-clinical-manifestations -->`; deterministic from book + chapter + heading text, unchanged on re-run.
- **Tables:** GFM if simple; HTML (`rowspan/colspan`-capable) if complex; `REVIEW_REQUIRED` if confidence is low.
- **Figures:** `![Figure 1.1](../assets/images/ch_001/figure_1_01.png)` then the verbatim caption. Never base64, never redrawn.
- **Citations:** `[12]`, `[23–25]`, superscript numbers (`<sup>12</sup>`) are preserved; never renumbered.
- **Uncertain text:** `<!-- UNCERTAIN: source text unreadable, page 6 -->` + `[Unreadable source text]` — never guessed.
- **Generated vs source:** AI/heuristic output exists only in `indexes/` and metadata, labelled *"Generated navigation index — not part of the original textbook."*
- Default output is CommonMark/GFM; an *Obsidian* view mode (callouts) is optional at display/export time.

## Install & run (development)

Requirements: Python ≥ 3.10, Node ≥ 18, and for the desktop shell Rust + Tauri 2 prerequisites.

```bash
# 1. engine
cd engine && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # PyMuPDF + pytest
python -m mdkb serve                # http://127.0.0.1:8765

# 2. UI (browser dev mode; in a browser you type the PDF path instead of using a native picker)
cd app && npm install && npm run dev       # http://localhost:1420

# 3. desktop app (starts the engine automatically)
cd app && npx tauri dev
```

CLI (no UI needed):

```bash
python -m mdkb inspect book.pdf
python -m mdkb new book.pdf ./Nelson22 --title "Nelson Textbook of Pediatrics" --edition 22
python -m mdkb convert ./Nelson22 --chapters 1,2      # omit --chapters for the whole book; re-run to resume
python -m mdkb search ./Nelson22 "iron chelation"
python -m mdkb indexes ./Nelson22
python -m mdkb export ./Nelson22 ./Nelson22.zip [--rag-only] [--include-pdf]
```

### Build / package
`cd app && npx tauri build`. For distribution bundle the engine as a sidecar (e.g. `pyinstaller -F -n mdkb engine/mdkb/__main__.py`,
then declare it under `bundle.externalBin` and spawn it instead of `python -m mdkb`). The dev shell spawns `python3 -m mdkb serve`
(override with `MDKB_PYTHON`, `MDKB_ENGINE_DIR`).

### AI provider setup
Settings → AI provider: choose OpenAI or Gemini, paste your API key (stored in `~/.mdkb/secret.json`, mode 600, sent only to that
provider), **Test connection**, and use **Preview what is sent**. AI is optional and only ever sees short lists of heading terms for
index classification — never full chapters, never as a source of textbook text. Without a key everything deterministic still works.

### OCR setup
OCR is a fallback used **only** for pages with no native text. Install Tesseract (`brew install tesseract` / `apt install tesseract-ocr`).
Without it such pages become `<!-- UNCERTAIN -->` blocks and a HIGH issue (`REVIEW_REQUIRED`). OCR'd pages always raise a
verification warning for any numbers (doses, decimals, tables).

## Search & source jump
Local SQLite FTS5 (porter stemming) over every converted block. Results show snippet, chapter, heading path, source page and type;
**Open section** opens the side-by-side review at that page, **Open source page** shows the original PDF page rendered by the engine.
The FTS DB is a cache: `POST /api/search/reindex` rebuilds it from `rag/chunks.jsonl` after copying a project folder.

## Tests
```bash
cd engine && python -m pytest -q          # 59 tests
cd app && npm run build                   # type-check + production build
```
Covered: reading order, dehyphenation, line joining, heading/list/table/figure/callout output, table serialisation, source-page
mapping, numeric fingerprinting, stable IDs, cross-reference resolution (incl. later re-link), manifest, export package/portability,
resume after interruption (output identical to an uninterrupted run), scanned page (with and without OCR backend), many-figure and
large-table chapters, HTTP API flows, encrypted/corrupt PDF errors. Fixtures are synthetic PDFs generated in `engine/tests/fixtures.py`.

## Known limitations (honest status)
- **Validated on one real chapter only** (Nelson-style Dengue/Yellow Fever extract: 2 columns, shared pages, composite figure, table). Other chapters/books will still need layout tuning (`assemble._heading_style`, `layout.detect_columns`). Conversion memory is recorded but only body font size and running headers are reused today.
- Font-encoding repairs (`Te`→`The`, `A%er`→`After`, `dengue- like`→`dengue-like`, end-of-section `␣`) are deliberately conservative, evidence-based and logged as `ARTIFACT_REPAIRED`; raw text stays in `.mdkb/state`. Figure labels inside diagrams are kept as `<details>` text in best-effort reading order.
- The **Tauri/Rust shell is scaffolded but was not compiled** in the build environment (missing GTK/WebKit system libs). The UI itself builds and was smoke-tested against the engine in a browser.
- Formulas are preserved as extracted Unicode text; **LaTeX reconstruction is not implemented**. Text inside figures is not extracted (no `<details>` labels).
- Tables spanning pages are not merged; rotated pages and RTL scripts are untested; only English is targeted.
- Bold/italic is preserved only for partial-line runs; superscript/subscript depends on font flags/baseline.
- Semantic indexes are lexicon heuristics (suffix patterns/symptom list); AI classification helper exists (`ai.classify_terms`) but is not wired into the UI flow.
- The local API has no auth token (127.0.0.1 only); add one before exposing beyond the desktop.

## Roadmap
Phase 2 – Vietnamese translation from verified Markdown, bilingual output, glossary/terminology memory. Phase 3 – embeddings, local RAG chat, cross-book search.
Phase 4 – study notes, flashcards, MCQs, concept maps (always kept separate from canonical Markdown).

## Dev harness
`.claude/agents` (pipeline-engineer, fidelity-reviewer, ui-builder) and `.claude/skills` (`pdf-md-orchestrator`, `pdf-md-fidelity-rules`) encode the workflow for further development.

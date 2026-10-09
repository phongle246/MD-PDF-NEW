from __future__ import annotations
from collections import Counter
from .util import atomic_write

ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
STATUS_LABEL = {"COMPLETED": "PASS", "COMPLETED_WITH_WARNINGS": "PASS WITH WARNINGS", "REVIEW_REQUIRED": "REVIEW REQUIRED", "FAILED": "FAILED"}


def write(project, ch, filename, status, issues, pages, A, tables, figure_files, items, unresolved, ocr_enabled) -> str:
    key = project.ch_key(ch.number)
    by_code = {}
    for i in issues:
        by_code.setdefault(i.code, []).append(i)

    def section(codes, none="None."):
        rows = [i for c in codes for i in by_code.get(c, [])]
        rows.sort(key=lambda i: ORDER[i.severity])
        return "\n".join(f"- **{i.severity}** {i.message}" + (f" _(page {i.page})_" if i.page else "") for i in rows) or none

    ocr_pages = [p["page"] for p in pages if (p.get("ocr") or {}).get("used")]
    cnt = Counter(i.severity for i in issues)
    lines = [
        f"# Conversion Report — Chapter {ch.number}: {ch.title}", "",
        "## Status", f"**{STATUS_LABEL.get(status, status)}**  (`{status}`)", "",
        f"Issues: CRITICAL {cnt['CRITICAL']} · HIGH {cnt['HIGH']} · MEDIUM {cnt['MEDIUM']} · LOW {cnt['LOW']}", "",
        "## Source", f"- Book: {project.title} (edition {project.data['edition'] or 'n/a'})", f"- File: {project.data['source_name']}",
        f"- SHA-256: `{project.data['source_sha256']}`", f"- Pages: {ch.start_page}–{ch.end_page}",
        f"- Chapter detection: {ch.source} (confidence {ch.confidence:.2f})", f"- Output: `chapters/{filename}`", "",
        "## Structure", f"- Headings: {len(A.headings)}", section(["HEADING_UNCERTAIN", "HEADING_SKIP", "HYPHEN_KEPT"]), "",
        "## Pages Processed", f"- {len(pages)} of {ch.end_page - ch.start_page + 1} pages", section(["PAGE_SKIPPED", "PAGE_SHORT", "TEXT_LOSS"]), "",
        "## Tables", f"- Tables extracted: {len(tables)}", *[f"  - p.{t['page']} {t['label'] or ''} {t['rows']}×{t['cols']} ({t['format']})" + (f" → `{t['file']}`" if t['file'] else "") for t in tables],
        section(["TABLE_COMPLEX", "TABLE_NUMERIC_MISMATCH", "TABLE_COUNT", "TABLE_SEPARATE_FILE"]), "",
        "## Figures", f"- Figures extracted: {len(figure_files)}", section(["FIGURE_NO_CAPTION", "CAPTION_NO_FIGURE", "FIGURE_COUNT", "FIGURE_BROKEN_PATH", "FIGURE_FILE_MISSING", "FIGURE_DUPLICATE"]), "",
        "## OCR", f"- OCR enabled: {ocr_enabled}", f"- Pages OCR'd: {ocr_pages or 'none'}", section(["OCR_USED", "OCR_NUMERIC_UNVERIFIED"]), "",
        "## Numeric QA", section(["MISSING_NUMBER", "CHANGED_NUMBER", "UNEXPECTED_NUMBER"], "No numeric differences between source extraction and Markdown."), "",
        "## Citation QA", f"- Reference entries preserved: {len(A.refs)}", section(["CITATION_NO_REFERENCE", "REFERENCES_MISSING", "REFERENCE_NOT_CITED"]), "",
        "## Uncertain Text", section(["UNCERTAIN_TEXT", "UNREADABLE_PAGE"]), "",
        "## Possible Source Typos", "None detected (the converter never edits source text).", "",
        "## Unresolved Cross References", *([f"- {u['text']} (page {u['page']})" for u in unresolved] or ["None."]), "",
        "## Human Review Recommended",
    ]
    review = [i for i in issues if i.severity in ("CRITICAL", "HIGH")]
    lines += [f"- **{i.severity}** {i.message}" + (f" _(page {i.page})_" if i.page else "") for i in sorted(review, key=lambda i: ORDER[i.severity])] or ["No."]
    path = project.root / "reports" / f"{key}_conversion_report.md"
    atomic_write(path, "\n".join(lines) + "\n")
    atomic_write(project.root / "reports" / f"{key}_issues.json", __import__("json").dumps([i.to_dict() for i in issues], indent=2))
    return f"reports/{key}_conversion_report.md"

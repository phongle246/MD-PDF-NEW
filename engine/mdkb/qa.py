"""Quality assurance: numeric fingerprint, citations, structure, completeness."""
from __future__ import annotations
import re
from collections import Counter
from .models import Issue

NUM_RE = re.compile(r"(?<![\w.])[-+±]?\d+(?:[.,]\d+)*(?:\s?[-–]\s?\d+(?:[.,]\d+)*)?%?")
DOSE_RE = re.compile(r"\d+(?:\.\d+)?\s?(?:mg|mcg|µg|μg|g|kg|mL|ml|L|IU|U|units?|mmol|mEq|mmHg|%|°C|bpm)\b", re.I)


def numeric_tokens(text: str) -> Counter:
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"</?[A-Za-z][^<>]*>", " ", text)               # html tags / attributes
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)               # images (alt text + path)
    text = re.sub(r"\]\([^)]*\)", "]", text)                    # markdown link targets
    text = re.sub(r"\[\^\d+\]", " ", text)                      # footnote markers we add
    toks = []
    for m in re.finditer(r"\d+(?:[.,]\d+)*", text):
        toks.append(m.group(0).replace(",", ""))
    return Counter(toks)


def numeric_qa(source_text: str, markdown_body: str) -> list[Issue]:
    src, out = numeric_tokens(source_text), numeric_tokens(markdown_body)
    issues = []
    doses = {m.group(0) for m in DOSE_RE.finditer(source_text)}
    for tok, n in (src - out).items():
        sev = "HIGH" if any(re.search(rf"(?<![\d.]){re.escape(tok)}", d) for d in doses) else "MEDIUM"
        issues.append(Issue(sev, "MISSING_NUMBER", f"Number '{tok}' appears {n}x in source extraction but not in Markdown"))
    for tok, n in (out - src).items():
        issues.append(Issue("HIGH", "UNEXPECTED_NUMBER", f"Number '{tok}' appears {n}x in Markdown but not in source extraction"))
    if issues:
        # a changed number looks like one missing + one unexpected: summarise
        miss = {i.message.split("'")[1] for i in issues if i.code == "MISSING_NUMBER"}
        unex = {i.message.split("'")[1] for i in issues if i.code == "UNEXPECTED_NUMBER"}
        if miss and unex:
            issues.append(Issue("HIGH", "CHANGED_NUMBER", f"Possible changed numbers: missing {sorted(miss)[:5]} / unexpected {sorted(unex)[:5]}"))
    return issues


CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")


def expand_citation(s: str) -> set[int]:
    nums: set[int] = set()
    for part in re.split(r"\s*,\s*", s):
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        if m and 0 < int(m.group(2)) - int(m.group(1)) < 200:
            nums.update(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.strip().isdigit():
            nums.add(int(part))
    return nums


def citation_qa(body_md: str, ref_numbers: set[int]) -> list[Issue]:
    cited: set[int] = set()
    for m in CITE_RE.finditer(body_md):
        cited |= expand_citation(m.group(1))
    issues = []
    if ref_numbers:
        missing = sorted(cited - ref_numbers)
        if missing:
            issues.append(Issue("MEDIUM", "CITATION_NO_REFERENCE", f"In-text citations with no reference entry: {missing[:15]}"))
        unused = sorted(ref_numbers - cited)
        if unused and len(unused) > len(ref_numbers) * 0.5:
            issues.append(Issue("LOW", "REFERENCE_NOT_CITED", f"{len(unused)} reference entries are never cited in text"))
    elif cited:
        issues.append(Issue("MEDIUM", "REFERENCES_MISSING", "In-text citations found but no reference list detected"))
    return issues


def structure_qa(headings: list[tuple[int, str]]) -> list[Issue]:
    issues, prev = [], 0
    for lvl, text in headings:
        if prev and lvl > prev + 1:
            issues.append(Issue("MEDIUM", "HEADING_SKIP", f"Heading level jumps H{prev} -> H{lvl}: '{text[:60]}'"))
        prev = lvl
    return issues


def completeness_qa(processed_pages: list[int], expected: list[int], text_len_by_page: dict[int, int]) -> list[Issue]:
    issues = []
    missing = sorted(set(expected) - set(processed_pages))
    if missing:
        issues.append(Issue("CRITICAL", "PAGE_SKIPPED", f"Source pages not processed: {missing[:20]}"))
    for p, n in text_len_by_page.items():
        if n < 40:
            issues.append(Issue("LOW", "PAGE_SHORT", f"Page {p} yielded very little text ({n} chars)", page=p))
    return issues


def fidelity_qa(source_words: Counter, md_words: Counter) -> list[Issue]:
    """Word-level recall: every extracted source word must survive into Markdown.
    Words split by line-end hyphenation in the source (pedi|atric) count as present when the joined word is in Markdown."""
    missing = source_words - md_words
    extra = md_words - source_words
    for w in list(extra):
        for i in range(3, len(w) - 2):
            l, r = w[:i], w[i:]
            if missing.get(l, 0) > 0 and missing.get(r, 0) > 0:
                missing[l] -= 1; missing[r] -= 1
                break
    lost = sum(v for v in missing.values() if v > 0)
    total = sum(source_words.values()) or 1
    ratio = lost / total
    if ratio > 0.05:
        return [Issue("CRITICAL", "TEXT_LOSS", f"{ratio:.1%} of source words missing from Markdown")]
    if ratio > 0.01:
        return [Issue("HIGH", "TEXT_LOSS", f"{ratio:.1%} of source words missing from Markdown ({lost} words)")]
    return []


def word_counter(text: str) -> Counter:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"</?[A-Za-z][^<>]*>", " ", text)
    return Counter(w.lower() for w in re.findall(r"[A-Za-z]{3,}", text))


def status_from(issues: list[Issue], tables_low_conf: bool = False) -> str:
    sev = {i.severity for i in issues}
    if "CRITICAL" in sev:
        return "FAILED" if sum(1 for i in issues if i.severity == "CRITICAL") > 3 else "REVIEW_REQUIRED"
    if "HIGH" in sev or tables_low_conf:
        return "REVIEW_REQUIRED"
    if sev - {"LOW"}:
        return "COMPLETED_WITH_WARNINGS"
    return "COMPLETED"

"""Generated navigation indexes (diseases, drugs, signs_symptoms, topics). NOT textbook content.
Deterministic lexicon heuristics by default; optional AI classification of *terms only*."""
from __future__ import annotations
import re
from collections import defaultdict
from .util import atomic_write, read_json

HEADER = "> **Generated navigation index — not part of the original textbook.**\n> Entries link to the chapter and section where the term appears. No factual text is added.\n"
DISEASE_RE = re.compile(r"\b([A-Z]?[a-z]+(?:itis|osis|emia|aemia|pathy|oma|plasia|penia|uria|algia)|[A-Z][a-z]+ (?:syndrome|disease|disorder|deficiency|anemia|fever))\b")
DRUG_RE = re.compile(r"\b([A-Za-z]+(?:mycin|cillin|cycline|azole|vir|statin|pril|sartan|olol|dipine|mab|floxacin|thiazide|prazole|setron|barbital|zepam|caine|parin|sone|solone))\b", re.I)
SYMPTOMS = ["fever", "cough", "vomiting", "diarrhea", "rash", "pallor", "jaundice", "cyanosis", "dyspnea", "wheezing", "seizure", "headache",
            "lethargy", "irritability", "failure to thrive", "abdominal pain", "edema", "hepatomegaly", "splenomegaly", "tachycardia",
            "tachypnea", "hypotension", "fatigue", "dehydration", "apnea", "stridor", "hematuria", "bleeding", "bruising", "weight loss"]
STOP = {"Syndrome", "Disease", "Disorder"}


def _scan(project):
    """term -> {kind: set((chapter, chapter_title, section_id, heading, md_file))}"""
    cats = {"diseases": defaultdict(set), "drugs": defaultdict(set), "signs_symptoms": defaultdict(set), "topics": defaultdict(set)}
    for c in project.chapters():
        pr = project.progress(c.number)
        if not pr.get("markdown_file"):
            continue
        smap = read_json(project.root / "source_maps" / f"{project.ch_key(c.number)}.json", {})
        for s in smap.get("sections", []):
            cats["topics"][s["title"]].add((c.number, c.title, s["section_id"], s["title"], pr["markdown_file"]))
        f = project.state_dir / "state" / project.ch_key(c.number) / "chunks.jsonl"
        if not f.exists():
            continue
        import json
        for line in f.read_text("utf-8").splitlines():
            r = json.loads(line)
            t = r["text"]
            loc = (c.number, c.title, r["section_id"], (r["heading_path"] or [c.title])[-1], pr["markdown_file"])
            for m in DISEASE_RE.finditer(t):
                w = m.group(1)
                if w not in STOP and len(w) > 5:
                    cats["diseases"][w.lower() if w.islower() else w].add(loc)
            for m in DRUG_RE.finditer(t):
                cats["drugs"][m.group(1).lower()].add(loc)
            low = t.lower()
            for s in SYMPTOMS:
                if s in low:
                    cats["signs_symptoms"][s].add(loc)
    return cats


def build(project, ai_terms: dict | None = None):
    """ai_terms (optional): {'diseases':[...],'drugs':[...],'signs_symptoms':[...]} produced by AI from term lists only."""
    cats = _scan(project)
    titles = {"diseases": "Diseases", "drugs": "Drugs", "signs_symptoms": "Signs & Symptoms", "topics": "Topics"}
    for k, terms in cats.items():
        lines = [f"# {titles[k]} Index", "", HEADER, f"_Method: {'AI-assisted term classification + ' if ai_terms else ''}deterministic pattern matching. Verify in the chapter text._", ""]
        for term in sorted(terms, key=str.lower):
            locs = sorted(terms[term])
            lines.append(f"- **{term}** — " + "; ".join(f"[Ch {n}: {h}]({f}#{_anchor(h)})" for n, ct, sid, h, f in locs[:8]) + (" …" if len(locs) > 8 else ""))
        atomic_write(project.root / "indexes" / f"{k}.md", "\n".join(lines) + "\n")
    return {k: len(v) for k, v in cats.items()}


def _anchor(t): return re.sub(r"[^\w\- ]", "", t.lower()).strip().replace(" ", "-")

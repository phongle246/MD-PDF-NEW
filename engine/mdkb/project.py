"""Project model: a textbook = a portable folder. App state lives in hidden .mdkb/ (rebuildable)."""
from __future__ import annotations
import json, os, re, time
from datetime import datetime, timezone
from pathlib import Path
import pymupdf
from . import APP_VERSION, PARSER_VERSION, chapters as chmod
from .models import Chapter
from .util import sha256_file, write_json, read_json, slugify, atomic_write

STATUSES = ("NOT_STARTED", "PROCESSING", "COMPLETED", "COMPLETED_WITH_WARNINGS", "REVIEW_REQUIRED", "FAILED")
SUBDIRS = ["chapters", "assets/images", "tables", "source_maps", "indexes", "rag", "reports", "metadata"]


class PDFError(Exception):
    pass


def open_pdf(path: str | Path):
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        raise PDFError(f"Cannot open PDF (corrupt or unsupported): {e}")
    if doc.needs_pass:
        doc.close()
        raise PDFError("PDF is encrypted/password-protected. Remove the password and retry.")
    if doc.page_count == 0:
        raise PDFError("PDF has no pages.")
    return doc


def inspect_pdf(path: str | Path) -> dict:
    """Pass 1 for the whole book: metadata, outline, chapters. Read-only."""
    doc = open_pdf(path)
    try:
        meta = doc.metadata or {}
        title = (meta.get("title") or "").strip() or Path(path).stem.replace("_", " ")
        chs = chmod.detect(doc)
        if not chs:
            chs = chmod.whole_book_fallback(doc, title)
        text_pages = sum(1 for i in range(min(doc.page_count, 30)) if len(doc[i].get_text().strip()) > 40)
        sampled = min(doc.page_count, 30)
        edition = ""
        m = re.search(r"(\d{1,2})(?:st|nd|rd|th)\s+edition", " ".join(doc[i].get_text() for i in range(min(3, doc.page_count))), re.I)
        if m:
            edition = m.group(1)
        return {"title": title, "author": meta.get("author", ""), "edition": edition, "pages": doc.page_count,
                "has_outline": bool(doc.get_toc()), "native_text_ratio": round(text_pages / max(1, sampled), 2),
                "chapters": [c.to_dict() for c in chs]}
    finally:
        doc.close()


class Project:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.state_dir = self.root / ".mdkb"
        self.data = read_json(self.state_dir / "project.json", {})

    # ---------- creation
    @classmethod
    def create(cls, root: str | Path, pdf_path: str | Path, title: str | None = None, edition: str | None = None) -> "Project":
        pdf_path = Path(pdf_path).resolve()
        info = inspect_pdf(pdf_path)
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        for d in SUBDIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        (root / ".mdkb" / "state").mkdir(parents=True, exist_ok=True)
        title = title or info["title"]
        p = cls(root)
        p.data = {
            "title": title, "edition": edition if edition is not None else info["edition"], "author": info["author"],
            "slug": slugify(title + ("-" + (edition or info["edition"]) if (edition or info["edition"]) else ""), 40),
            "source_file": str(pdf_path), "source_name": pdf_path.name, "source_sha256": sha256_file(pdf_path),
            "pages": info["pages"], "has_outline": info["has_outline"], "native_text_ratio": info["native_text_ratio"],
            "chapters": info["chapters"], "created": _now(), "last_opened": _now(),
            "app_version": APP_VERSION, "parser_version": PARSER_VERSION,
            "settings": {"markdown_mode": "portable", "ocr": True},
        }
        p.save()
        p.write_book_files()
        return p

    def save(self):
        write_json(self.state_dir / "project.json", self.data)

    # ---------- accessors
    @property
    def title(self): return self.data["title"]
    @property
    def pdf_path(self): return Path(self.data["source_file"])
    def chapters(self) -> list[Chapter]:
        return [Chapter(**{k: v for k, v in c.items() if k in Chapter.__dataclass_fields__}) for c in self.data["chapters"]]

    def chapter(self, number: int) -> Chapter:
        for c in self.chapters():
            if c.number == number:
                return c
        raise KeyError(f"No chapter {number}")

    def set_chapters(self, chs: list[dict]):
        """Manual correction of chapter boundaries."""
        norm = chmod.validate([Chapter(**{**c, "source": "manual", "confidence": 1.0}) for c in chs], self.data["pages"])
        self.data["chapters"] = [c.to_dict() for c in norm]
        self.save()

    def verify_source(self) -> bool:
        p = self.pdf_path
        return p.exists() and sha256_file(p) == self.data["source_sha256"]

    # ---------- chapter state (resume)
    def ch_key(self, n: int) -> str: return f"ch_{n:03d}"
    def ch_state_dir(self, n: int) -> Path: return self.state_dir / "state" / self.ch_key(n)

    def progress(self, n: int) -> dict:
        return read_json(self.ch_state_dir(n) / "progress.json", {"status": "NOT_STARTED", "completed_pages": [], "stage": ""})

    def set_progress(self, n: int, **kw):
        pr = self.progress(n)
        pr.update(kw)
        write_json(self.ch_state_dir(n) / "progress.json", pr)
        return pr

    def chapter_status(self, n: int, live: set[int] | None = None) -> str:
        """Display status. PROCESSING on disk without a live job means the app died: report PARTIAL."""
        st = self.progress(n)["status"]
        if st == "PROCESSING" and not (live and n in live):
            return "PARTIAL"
        return st

    def overview(self, live: set[int] | None = None) -> dict:
        chs = []
        done = 0
        for c in self.chapters():
            pr = self.progress(c.number)
            st = self.chapter_status(c.number, live)
            total = c.end_page - c.start_page + 1
            if st in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "REVIEW_REQUIRED"):
                done += 1
            chs.append({**c.to_dict(), "status": st, "pages_done": len(pr.get("completed_pages", [])), "pages_total": total,
                        "stage": pr.get("stage", ""), "issues": pr.get("issue_counts", {}),
                        "markdown_file": pr.get("markdown_file", ""), "needs_review": c.confidence < 0.6})
        pct = round(100 * done / max(1, len(chs)))
        return {"title": self.title, "edition": self.data["edition"], "pages": self.data["pages"], "source_name": self.data["source_name"],
                "source_sha256": self.data["source_sha256"], "source_ok": self.verify_source(), "has_outline": self.data["has_outline"],
                "chapters": chs, "percent": pct, "root": str(self.root), "last_opened": self.data.get("last_opened"), "slug": self.data["slug"],
                "settings": self.data.get("settings", {})}

    # ---------- generated top-level files
    def converted_map(self) -> dict[int, str]:
        """chapter number -> relative md path for already converted chapters."""
        m = {}
        for c in self.chapters():
            pr = self.progress(c.number)
            if pr.get("markdown_file") and pr["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "REVIEW_REQUIRED"):
                m[c.number] = pr["markdown_file"]
        return m

    def write_book_files(self):
        from . import bookfiles
        bookfiles.write_all(self)

    def conversion_memory(self) -> dict:
        return read_json(self.root / "metadata" / "conversion_memory.json", {})

    def save_conversion_memory(self, mem: dict):
        write_json(self.root / "metadata" / "conversion_memory.json", mem)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

from __future__ import annotations
import zipfile
from pathlib import Path

EXCLUDE_DIRS = {".mdkb", "__pycache__"}


def export_zip(project, dest: str | Path, include_pdf: bool = False, rag_only: bool = False, chapter: int | None = None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    root = project.root
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        def add(p: Path, arc: str):
            z.write(p, arc)
        if rag_only:
            for rel in ("rag/chunks.jsonl", "manifest.json", "book.yaml", "README.md"):
                if (root / rel).exists():
                    add(root / rel, rel)
        elif chapter is not None:
            pr = project.progress(chapter)
            key = project.ch_key(chapter)
            if not pr.get("markdown_file"):
                raise RuntimeError(f"Chapter {chapter} has not been converted")
            add(root / pr["markdown_file"], pr["markdown_file"])
            for sub in (f"assets/images/{key}", f"tables/{key}"):
                for f in sorted((root / sub).rglob("*")) if (root / sub).exists() else []:
                    if f.is_file():
                        add(f, str(f.relative_to(root)))
            for rel in (f"source_maps/{key}.json", f"reports/{key}_conversion_report.md"):
                if (root / rel).exists():
                    add(root / rel, rel)
        else:
            for f in sorted(root.rglob("*")):
                rel = f.relative_to(root)
                if f.is_file() and not (set(rel.parts) & EXCLUDE_DIRS) and not f.name.startswith(".tmp_"):
                    add(f, str(rel))
            if include_pdf and project.pdf_path.exists():
                add(project.pdf_path, f"source/{project.pdf_path.name}")
    return dest

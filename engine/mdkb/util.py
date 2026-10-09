from __future__ import annotations
import hashlib, json, os, re, tempfile, unicodedata
from pathlib import Path


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(text: str, max_len: int = 60) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:max_len].strip("-") or "untitled"


def atomic_write(path: str | Path, data: str | bytes) -> None:
    """Write atomically so a crash never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_json(path: str | Path, obj) -> None:
    atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False))


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text("utf-8"))


def fingerprint(text: str) -> str:
    """Short whitespace/case-insensitive fingerprint of text for source maps."""
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return sha256_text(norm)[:16]


def stable_section_id(book_slug: str, chapter_number: int | str, heading_path: list[str], used: set[str]) -> str:
    """Deterministic ID: derived only from book, chapter and heading text."""
    base = f"{book_slug}-ch{chapter_number}"
    if heading_path:
        base += "-" + slugify("-".join(heading_path[-2:]), 70)
    sid, n = base, 2
    while sid in used:
        sid = f"{base}-{n}"
        n += 1
    used.add(sid)
    return sid

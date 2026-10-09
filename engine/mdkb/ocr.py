"""Optional OCR fallback. Used only for pages with no usable native text."""
from __future__ import annotations
import shutil, subprocess, tempfile, os

_backend = None   # injectable for tests: callable(png_bytes) -> (text, confidence)


def set_backend(fn):
    global _backend
    _backend = fn


def available() -> bool:
    if _backend:
        return True
    try:
        import pytesseract  # noqa: F401
        return bool(shutil.which("tesseract"))
    except Exception:
        return bool(shutil.which("tesseract"))


def ocr_png(png: bytes, lang: str = "eng") -> tuple[str, float]:
    """Return (text, mean confidence 0..1)."""
    if _backend:
        return _backend(png)
    exe = shutil.which("tesseract")
    if not exe:
        raise RuntimeError("OCR unavailable: install Tesseract (e.g. `brew install tesseract`)")
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "p.png")
        open(src, "wb").write(png)
        tsv = subprocess.run([exe, src, "stdout", "-l", lang, "tsv"], capture_output=True, text=True, timeout=180)
        words, confs, cur_line, lines = [], [], None, []
        for row in tsv.stdout.splitlines()[1:]:
            c = row.split("\t")
            if len(c) < 12 or not c[11].strip():
                continue
            key = (c[2], c[3], c[4])
            if cur_line is not None and key != cur_line:
                lines.append(" ".join(words)); words = []
            cur_line = key
            words.append(c[11])
            try:
                confs.append(float(c[10]))
            except ValueError:
                pass
        if words:
            lines.append(" ".join(words))
        conf = (sum(confs) / len(confs) / 100) if confs else 0.0
        return "\n".join(lines), conf

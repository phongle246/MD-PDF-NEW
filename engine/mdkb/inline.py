"""Inline formatting: bold/italic, sub/superscript, from span dicts."""
from __future__ import annotations
import re

SUP = str.maketrans("0123456789+-=()ni−", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ⁻")
SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def _sup(t: str) -> str:
    t2 = t.strip()
    if not t2:
        return t
    if re.fullmatch(r"[\d,\s–\-]+", t2) and re.search(r"\d", t2) and not re.fullmatch(r"\d", t2):
        return f"<sup>{t2}</sup>"                           # citation-style: 12, 23-25
    if all(c in "0123456789+-−=()ni" for c in t2):
        return t2.translate(SUP)                            # HCO3-, 10^3 ...
    return f"<sup>{t2}</sup>"


def _sub(t: str) -> str:
    t2 = t.strip()
    if t2 and all(c in "0123456789+-=()" for c in t2):
        return t2.translate(SUB)
    return f"<sub>{t2}</sub>" if t2 else t


def render_spans(spans: list[dict], whole_bold: bool = False, whole_italic: bool = False) -> str:
    """Render one line. Bold/italic markers wrap whole runs only when not the entire block."""
    out, run, run_fmt = [], "", None

    def flush():
        nonlocal run, run_fmt
        if not run:
            return
        b, i = run_fmt
        core = run.strip()
        if core and ((b and not whole_bold) or (i and not whole_italic)):
            lead = run[: len(run) - len(run.lstrip())]
            trail = run[len(run.rstrip()):]
            mark = "***" if (b and not whole_bold and i and not whole_italic) else "**" if (b and not whole_bold) else "*"
            out.append(f"{lead}{mark}{core}{mark}{trail}")
        else:
            out.append(run)
        run = ""

    for s in spans:
        t = s["text"]
        if s.get("sup"):
            flush(); out.append(_sup(t)); run_fmt = None; continue
        if s.get("sub"):
            flush(); out.append(_sub(t)); run_fmt = None; continue
        fmt = (s["bold"] and bool(t.strip()), s["italic"] and bool(t.strip()))
        if not t.strip():
            run += t
            continue
        if run_fmt is None or fmt != run_fmt:
            flush(); run_fmt = fmt
        run += t
    flush()
    return "".join(out)


def strip_markup(md: str) -> str:
    md = re.sub(r"<\/?su[bp]>", "", md)
    md = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", md)
    return md

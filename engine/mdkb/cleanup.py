"""Deterministic text cleanup. Never rewrites sentences."""
from __future__ import annotations
import re
from collections import Counter

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿­"), None)

# Compounds whose hyphen is real even when it falls at a line end.
_KEEP_HYPHEN_PREFIX = {
    "long", "short", "well", "non", "anti", "pre", "post", "self", "cross", "high", "low", "full",
    "half", "semi", "co", "multi", "ex", "re", "inter", "intra", "mid", "near", "over", "under",
    "x", "t", "b", "c", "d", "e", "g", "type", "stage", "grade", "mono", "bi", "tri", "poly",
}


def normalize_chars(text: str) -> str:
    """Fix extraction artefacts only (ligatures, zero-width, nbsp). Greek/units/math untouched."""
    for k, v in LIGATURES.items():
        text = text.replace(k, v)
    text = text.translate(ZERO_WIDTH)
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    return text


def normalize_ws(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


class Vocab:
    """Words and hyphenated compounds seen in the source, used for safe dehyphenation."""

    def __init__(self, words=None, compounds=None):
        self.words = words or set()
        self.compounds = compounds or set()

    @classmethod
    def build(cls, texts: list[str]) -> "Vocab":
        words, comps = set(), set()
        skip_first = False
        for t in texts:
            t2 = t
            if skip_first:                         # first token of a line following a hyphen break is a fragment
                t2 = re.sub(r"^\s*[A-Za-z]+", "", t2, count=1)
            skip_first = bool(re.search(r"[A-Za-z]-\s*$", t))
            if skip_first:
                t2 = re.sub(r"[A-Za-z]+-\s*$", "", t2)
            for w in re.findall(r"[A-Za-z]{2,}", t2):
                words.add(w.lower())
            for m in re.finditer(r"(?<![\w-])([A-Za-z]{2,})-([A-Za-z]{2,})(?![\w-])", t2):
                comps.add((m.group(1) + "-" + m.group(2)).lower())
        return cls(words, comps)


def build_vocab(texts: list[str]) -> Vocab:
    return Vocab.build(texts)


def decide_hyphen(left: str, right: str, vocab: Vocab | None) -> str:
    """Return 'join' (drop hyphen), 'keep' (real hyphen) or 'uncertain' (keep + flag)."""
    if not left or not right or not right[0].islower():
        return "keep"
    l, r = left.lower(), right.lower()
    vocab = vocab or Vocab()
    if (l + r) in vocab.words:
        return "join"
    if f"{l}-{r}" in vocab.compounds:
        return "keep"
    if l in _KEEP_HYPHEN_PREFIX:
        return "keep"
    lw, rw = l in vocab.words, r in vocab.words
    if lw and rw and len(l) > 3 and len(r) > 3:
        return "keep"                     # two real words: long-term, case-control
    if not lw and len(l) >= 3:
        return "join"                      # left is only a fragment (pedi-, thalas-)
    return "uncertain"


def join_lines(lines: list[str], vocab: Vocab | None = None, issues: list | None = None) -> str:
    """Join wrapped PDF lines into one logical paragraph."""
    out = ""
    for ln in lines:
        ln = normalize_ws(ln)
        if not ln:
            continue
        if not out:
            out = ln
            continue
        m = re.search(r"([A-Za-z]+)-$", out)
        if m and re.match(r"[A-Za-z]", ln):
            right = re.match(r"[A-Za-z]+", ln).group(0)
            d = decide_hyphen(m.group(1), right, vocab)
            if d == "join":
                out = out[:-1] + ln
                continue
            out = out + ln                           # hyphen kept, no space
            if d == "uncertain" and issues is not None:
                issues.append(("LOW", "HYPHEN_KEPT", f"Line-end hyphen kept (uncertain): {m.group(1)}-{right}"))
            continue
        out = out + " " + ln
    return out


def find_repeated_margin_lines(pages_lines: dict[int, list[tuple[str, float]]], page_h: dict[int, float],
                               min_ratio: float = 0.4) -> set[str]:
    """Detect running headers/footers: short lines in top/bottom margins repeating across pages.
    pages_lines[p] = [(text, y_center)]. Page numbers are normalised to '#'."""
    cnt: Counter = Counter()
    n = max(1, len(pages_lines))
    for p, lines in pages_lines.items():
        h = page_h.get(p, 800)
        seen = set()
        for text, y in lines:
            if y < h * 0.08 or y > h * 0.92:
                key = normalize_margin(text)
                if key and key not in seen:
                    seen.add(key)
                    cnt[key] += 1
    thresh = max(2, int(n * min_ratio))
    return {k for k, c in cnt.items() if c >= thresh}


def normalize_margin(text: str) -> str:
    t = normalize_ws(normalize_chars(text))
    return re.sub(r"\d+", "#", t).lower()


def is_page_number(text: str) -> bool:
    return bool(re.fullmatch(r"\s*(?:page\s*)?\d{1,5}\s*", text, re.I))

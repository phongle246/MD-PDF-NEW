from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional

BLOCK_TYPES = ("text", "heading", "list", "table", "figure", "caption", "callout", "footnote", "reference")


@dataclass
class Span:
    text: str
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    font: str = ""
    flags: int = 0


@dataclass
class Block:
    """Internal document block. Keeps raw and cleaned text for audit."""
    id: str
    type: str
    page: int                       # 1-based PDF page number
    bbox: tuple[float, float, float, float]
    reading_order: int = 0
    raw_text: str = ""
    cleaned_text: str = ""
    confidence: float = 1.0
    level: int = 0                  # heading level 1..4
    column: int = 0                 # 0 full-width, 1 left, 2 right
    font_size: float = 0.0
    bold: bool = False
    italic: bool = False
    markdown: str = ""              # final rendering
    extra: dict = field(default_factory=dict)
    section_id: str = ""
    heading_path: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["bbox"] = [round(x, 2) for x in self.bbox]
        return d


@dataclass
class Issue:
    severity: str                   # CRITICAL HIGH MEDIUM LOW
    code: str
    message: str
    page: Optional[int] = None
    block_id: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class Chapter:
    number: int
    title: str
    start_page: int                 # 1-based inclusive
    end_page: int
    confidence: float = 1.0
    source: str = "outline"         # outline | toc | heading-pattern | manual
    specialty: str = ""
    status: str = "NOT_STARTED"
    notes: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

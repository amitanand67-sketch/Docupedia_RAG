from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class Chunk:
    """One retrievable passage: a slice of document text, or the description of an image."""

    source: str  # file name or URL, unique per document
    title: str
    content: str
    page: int | None = None
    section: str = ""  # heading path, e.g. "Leave policy > Sick leave"
    kind: str = "text"  # "text" | "image"
    url: str = ""
    groups: list[str] = field(default_factory=lambda: ["all"])
    vector: list[float] | None = None
    score: float = 0.0
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raw = f"{self.source}|{self.page}|{self.kind}|{self.content}"
            self.id = hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def embedding_text(self) -> str:
        """Prefix title and heading path so a chunk like "18 days" still matches "earned leave at Lumora"."""
        path = " > ".join(p for p in (self.title, self.section) if p)
        where = f" (page {self.page})" if self.page else ""
        return f"{path}{where}\n{self.content}"


@dataclass
class User:
    name: str
    groups: list[str]

    def can_see(self) -> list[str]:
        return sorted(set(self.groups) | {"all"})

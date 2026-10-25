"""Ingestion: extract -> describe images -> chunk -> embed -> index.

Re-running on the same file replaces its old chunks, so the index never holds stale copies.
Access control: an optional access.json next to the documents maps file names to the groups
allowed to see them, e.g. {"Lumora_Platform_Architecture.docx": ["engineering"]}.
Files not listed are visible to everyone ("all").
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import Settings
from ..models import Chunk
from ..retrieval.store import VectorStore
from .chunker import chunk_document
from .extract import SUPPORTED, extract, is_url

log = logging.getLogger(__name__)


def expand(locations: list[str]) -> list[str]:
    """Folders become the supported files inside them; URLs pass through."""
    out: list[str] = []
    for loc in locations:
        path = Path(loc)
        if not is_url(loc) and path.is_dir():
            out.extend(str(p) for p in sorted(path.rglob("*")) if p.suffix.lower() in SUPPORTED)
        else:
            out.append(loc)
    return out


def load_access(locations: list[str]) -> dict[str, list[str]]:
    access: dict[str, list[str]] = {}
    for loc in locations:
        path = Path(loc)
        candidate = (path if path.is_dir() else path.parent) / "access.json"
        if not is_url(loc) and candidate.exists():
            access.update(json.loads(candidate.read_text()))
    return access


def ingest(locations: list[str], settings: Settings, llm, store: VectorStore) -> dict[str, int]:
    access = load_access(locations)
    stats: dict[str, int] = {}
    for loc in expand(locations):
        url = loc if is_url(loc) else f"/files/{Path(loc).name}"
        doc = extract(loc, settings, url=url)
        groups = access.get(doc.source, ["all"])
        chunks = chunk_document(doc, groups, settings.chunk_tokens, settings.chunk_overlap_tokens)

        if settings.describe_images:
            # An image takes the heading path of the last text on its page (e.g. the calendar's section).
            page_section = {c.page: c.section for c in chunks if c.page}
            for number, image in enumerate(doc.images, start=1):
                description = llm.describe_image(image.data, image.mime, doc.title)
                where = f" on page {image.page}" if image.page else ""
                chunks.append(
                    Chunk(source=doc.source, title=doc.title, page=image.page, kind="image", url=doc.url,
                          section=page_section.get(image.page, ""),
                          groups=groups, content=f"[Image {number}{where} of '{doc.title}']\n{description}")
                )

        vectors = llm.embed([c.embedding_text() for c in chunks])
        for chunk, vector in zip(chunks, vectors):
            chunk.vector = vector
        store.delete_source(doc.source)
        store.upsert(chunks)
        stats[doc.source] = len(chunks)
        log.info("Indexed %s: %d chunks (%d images), groups=%s", doc.source, len(chunks), len(doc.images), groups)
    return stats

"""Structure-aware chunking: split by headings, size in tokens, keep tables with their header.

1. Parse each document into blocks (paragraph text or a table), remembering the heading path
   above each block, e.g. ("Leave policy", "Sick leave").
2. Paragraphs and tables under the same heading (and page) are packed into chunks of at most
   CHUNK_TOKENS tokens. A paragraph that is too long on its own is split with CHUNK_OVERLAP_TOKENS
   of overlap, breaking at paragraphs, then lines, then sentences.
3. A table is never cut mid-row. If it is too long for one chunk, it is split by rows and every
   piece repeats the header row, so a row is never separated from its column names.
4. Every chunk stores its heading path; it is prefixed to the text when embedding.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..models import Chunk
from .extract import ExtractedDoc

log = logging.getLogger(__name__)

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
TABLE_DIVIDER = re.compile(r"^\|?\s*:?-{3,}")
SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
MIN_CHARS = 20


@lru_cache
def _encoding():
    """The tokenizer of text-embedding-3 models, or None if it can't be loaded (it downloads once)."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        log.warning("tiktoken unavailable (no network?), estimating tokens as characters / 4")
        return None


def count_tokens(text: str) -> int:
    """Tokens as the embedding model counts them."""
    encoding = _encoding()
    return len(encoding.encode(text)) if encoding else len(text) // 4 + 1


@dataclass
class Block:
    path: tuple[str, ...]
    page: int | None
    kind: str  # "text" | "table"
    text: str


def parse_blocks(doc: ExtractedDoc) -> list[Block]:
    """Walk the Markdown-ish text of every section, tracking the heading path."""
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []  # (level, heading), carried across pages
    title_heading = ""  # a level-1 heading before any content is the document's own title
    for section in doc.sections:
        para: list[str] = []
        table: list[str] = []

        def path() -> tuple[str, ...]:
            names = [h for _, h in stack]
            if names and names[0].lower() in {doc.title.lower(), title_heading.lower()}:
                names = names[1:]  # the document title is already on every chunk
            return tuple(names)

        def flush() -> None:
            if para:
                blocks.append(Block(path(), section.page, "text", "\n".join(para).strip()))
                para.clear()
            if table:
                blocks.append(Block(path(), section.page, "table", "\n".join(table)))
                table.clear()

        for raw in section.text.split("\n"):
            line = raw.strip()
            heading = HEADING.match(line)
            if heading:
                flush()
                level = len(heading.group(1))
                if level == 1 and not blocks and not stack:
                    title_heading = heading.group(2)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading.group(2)))
            elif line.startswith("|"):
                if para:
                    flush()
                table.append(line)
            elif not line:
                if para:  # a blank line ends a paragraph; tables end at the next non-table line
                    flush()
            else:
                if table:
                    flush()
                para.append(raw.rstrip())
        flush()
    return [b for b in blocks if b.text]


def split_table(text: str, max_tokens: int, count: Callable[[str], int]) -> list[str]:
    """Keep a table whole if it fits; otherwise split by rows and repeat the header in each piece."""
    if count(text) <= max_tokens:
        return [text]
    rows = text.split("\n")
    header_len = 2 if len(rows) > 1 and TABLE_DIVIDER.match(rows[1]) else 1
    header, body = rows[:header_len], rows[header_len:]
    pieces, current = [], list(header)
    for row in body:
        if len(current) > header_len and count("\n".join([*current, row])) > max_tokens:
            pieces.append("\n".join(current))
            current = list(header)
        current.append(row)
    pieces.append("\n".join(current))
    return pieces


def chunk_document(doc: ExtractedDoc, groups: list[str], max_tokens: int = 512, overlap_tokens: int = 64,
                   count: Callable[[str], int] = count_tokens) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_tokens, chunk_overlap=overlap_tokens, length_function=count, separators=SEPARATORS
    )
    chunks: list[Chunk] = []

    def emit(text: str, block: Block) -> None:
        text = text.strip()
        if len(text) >= MIN_CHARS:
            chunks.append(Chunk(source=doc.source, title=doc.title, content=text, page=block.page,
                                section=" > ".join(block.path), url=doc.url, groups=groups))

    # Pieces under the same heading and page are packed together up to max_tokens, so a short
    # paragraph and the small table it introduces end up in one chunk.
    pending: list[str] = []
    owner: Block | None = None

    def flush() -> None:
        if pending and owner:
            emit("\n\n".join(pending), owner)
        pending.clear()

    for block in parse_blocks(doc):
        if owner and (owner.path, owner.page) != (block.path, block.page):
            flush()
        owner = block if not pending else owner
        if count(block.text) <= max_tokens:
            pieces = [block.text]
        elif block.kind == "table":
            pieces = split_table(block.text, max_tokens, count)
        else:
            pieces = splitter.split_text(block.text)
        for piece in pieces:
            if pending and count("\n\n".join([*pending, piece])) > max_tokens:
                flush()
                owner = block
            pending.append(piece)
    flush()
    return chunks

"""Turn PDFs, Word files and web pages into text sections plus the images inside them.

Two text extractors:
  - "local":    pdfplumber, python-docx and BeautifulSoup. Free, offline, good for clean digital files.
  - "docintel": Azure Document Intelligence layout model. Handles scans, tables and complex layouts,
                and returns Markdown with headings, which makes chunking smarter.

Images are pulled out of the file in both modes and described later by the vision model,
so facts that only appear in a picture (a calendar, a diagram) become searchable.
"""

from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ..config import Settings

SUPPORTED = {".pdf", ".docx", ".html", ".htm", ".md", ".txt"}
MIN_IMAGE_BYTES = 8_000  # skip logos, bullets and spacer images


@dataclass
class Section:
    text: str
    page: int | None = None


@dataclass
class ImageBlob:
    data: bytes
    mime: str
    page: int | None = None


@dataclass
class ExtractedDoc:
    source: str
    title: str
    url: str
    sections: list[Section] = field(default_factory=list)
    images: list[ImageBlob] = field(default_factory=list)


def is_url(location: str) -> bool:
    return urlparse(location).scheme in {"http", "https"}


def load_bytes(location: str) -> tuple[bytes, str]:
    """Return the raw bytes and a file-type suffix for a path or URL."""
    if is_url(location):
        import httpx

        resp = httpx.get(location, follow_redirects=True, timeout=30)
        resp.raise_for_status()
        suffix = Path(urlparse(location).path).suffix.lower()
        if suffix not in SUPPORTED:
            suffix = ".pdf" if "pdf" in resp.headers.get("content-type", "") else ".html"
        return resp.content, suffix
    path = Path(location)
    return path.read_bytes(), path.suffix.lower()


def extract(location: str, settings: Settings, url: str = "") -> ExtractedDoc:
    data, suffix = load_bytes(location)
    source = location if is_url(location) else Path(location).name
    doc = ExtractedDoc(source=source, title=Path(source).stem.replace("_", " "), url=url or location)

    if suffix == ".pdf":
        _pdf(data, doc)
    elif suffix == ".docx":
        _docx(data, doc)
    elif suffix in {".html", ".htm"}:
        _html(data, doc)
    else:
        doc.sections.append(Section(data.decode("utf-8", errors="ignore")))

    if settings.extractor == "docintel" and suffix in {".pdf", ".docx"}:
        doc.sections = _docintel(data, settings)
    return doc


def markdown_table(rows: list[list]) -> str:
    def cell(value) -> str:
        return " ".join(str(value or "").split()).replace("|", "/")

    width = max(len(r) for r in rows)
    lines = ["| " + " | ".join(cell(c) for c in [*r, *[""] * (width - len(r))]) + " |" for r in rows]
    lines.insert(1, "|" + " --- |" * width)
    return "\n".join(lines)


def _pdf(data: bytes, doc: ExtractedDoc) -> None:
    """Text, headings and tables with pdfplumber; images with pypdf.

    Headings are detected by font size: lines clearly larger than the body text become Markdown
    headings (largest size = #, next = ##, ...). Tables become Markdown tables in reading order.
    """
    import pdfplumber
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.metadata and reader.metadata.title:
        doc.title = str(reader.metadata.title)

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = []
        for page in pdf.pages:
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]
            lines = [
                {**line, "size": round(max(c["size"] for c in line["chars"]))}
                for line in page.extract_text_lines(return_chars=True)
                if line["chars"] and not any(_inside(line, box) for box in boxes)
            ]
            pages.append((lines, tables))

        sizes = Counter(line["size"] for lines, _ in pages for line in lines for _ in line["chars"])
        body = sizes.most_common(1)[0][0] if sizes else 10
        heading_sizes = sorted({line["size"] for lines, _ in pages for line in lines if line["size"] >= body * 1.15},
                               reverse=True)

        for number, (lines, tables) in enumerate(pages, start=1):
            items: list[tuple[float, str]] = []
            previous_bottom = None
            for line in lines:
                if line["size"] in heading_sizes:
                    level = min(heading_sizes.index(line["size"]) + 1, 6)
                    items.append((line["top"], "\n" + "#" * level + " " + line["text"].strip() + "\n"))
                else:
                    new_paragraph = previous_bottom is not None and line["top"] - previous_bottom > body * 0.8
                    items.append((line["top"], ("\n" if new_paragraph else "") + line["text"]))
                previous_bottom = line["bottom"]
            for table in tables:
                rows = [r for r in table.extract() if any(r)]
                if rows:
                    items.append((table.bbox[1], "\n" + markdown_table(rows) + "\n"))
            text = "\n".join(t for _, t in sorted(items, key=lambda item: item[0]))
            if text.strip():
                doc.sections.append(Section(text, number))

    for number, page in enumerate(reader.pages, start=1):
        for image in page.images:
            if len(image.data) >= MIN_IMAGE_BYTES:
                mime = "image/png" if image.name.lower().endswith(".png") else "image/jpeg"
                doc.images.append(ImageBlob(image.data, mime, number))


def _inside(line: dict, box: tuple, tolerance: float = 2.0) -> bool:
    x0, top, x1, bottom = box
    return (line["x0"] >= x0 - tolerance and line["x1"] <= x1 + tolerance
            and line["top"] >= top - tolerance and line["bottom"] <= bottom + tolerance)


def _docx(data: bytes, doc: ExtractedDoc) -> None:
    import docx

    d = docx.Document(io.BytesIO(data))
    if d.core_properties.title:
        doc.title = d.core_properties.title
    lines: list[str] = []
    for block in d.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = docx.text.paragraph.Paragraph(block, d)
            text = para.text.strip()
            if not text:
                continue
            style = para.style.name if para.style is not None else ""
            if style.startswith("Heading") or style == "Title":
                level = 1 if style == "Title" else int(style.split()[-1]) + 1 if style.split()[-1].isdigit() else 2
                lines.append("#" * min(level, 6) + " " + text)
            else:
                lines.append(text)
        elif tag == "tbl":
            table = docx.table.Table(block, d)
            lines.append(markdown_table([[c.text for c in row.cells] for row in table.rows]))
    doc.sections.append(Section("\n\n".join(lines)))
    for rel in d.part.rels.values():
        if "image" in rel.reltype:
            blob = rel.target_part.blob
            if len(blob) >= MIN_IMAGE_BYTES:
                doc.images.append(ImageBlob(blob, rel.target_part.content_type))


def _html(data: bytes, doc: ExtractedDoc) -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    if soup.title and soup.title.string:
        doc.title = soup.title.string.strip()
    lines: list[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "table"]):
        if el.find_parent("table"):
            continue
        if el.name == "table":
            rows = [[c.get_text(" ") for c in tr.find_all(["th", "td"])] for tr in el.find_all("tr")]
            if any(rows):
                lines.append(markdown_table([r for r in rows if r]))
            continue
        text = " ".join(el.get_text(" ").split())
        if not text:
            continue
        if el.name.startswith("h"):
            lines.append("#" * int(el.name[1]) + " " + text)
        elif el.name == "li":
            lines.append("- " + text)
        else:
            lines.append(text)
    doc.sections.append(Section("\n\n".join(lines)))


def _docintel(data: bytes, settings: Settings) -> list[Section]:
    """Azure Document Intelligence layout model -> one Markdown section per page."""
    from azure.ai.documentintelligence import DocumentIntelligenceClient

    if settings.azure_docintel_api_key:
        from azure.core.credentials import AzureKeyCredential

        credential = AzureKeyCredential(settings.azure_docintel_api_key)
    else:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
    client = DocumentIntelligenceClient(settings.azure_docintel_endpoint, credential)
    result = client.begin_analyze_document(
        "prebuilt-layout", io.BytesIO(data), output_content_format="markdown"
    ).result()
    sections = []
    for page in result.pages or []:
        text = "".join(result.content[s.offset : s.offset + s.length] for s in page.spans or [])
        if text.strip():
            sections.append(Section(text, page.page_number))
    return sections or [Section(result.content)]

from app.ingestion.chunker import chunk_document
from app.ingestion.extract import ExtractedDoc, Section, extract
from app.ingestion.pipeline import ingest
from app.retrieval.store import LocalStore

from .conftest import ROOT

DOCS = ROOT / "sample_docs"


def test_pdf_text_and_images(settings):
    doc = extract(str(DOCS / "Lumora_Employee_Handbook_2027.pdf"), settings)
    assert doc.title == "Lumora Employee Handbook 2027"
    assert any("Working hours" in s.text for s in doc.sections)
    assert len(doc.images) == 3  # leave flow, holiday calendar, org chart
    assert {i.page for i in doc.images} == {1, 2, 3}


def test_docx_keeps_headings_tables_and_images(settings):
    doc = extract(str(DOCS / "Lumora_Platform_Architecture.docx"), settings)
    text = doc.sections[0].text
    assert "## Service ownership" in text
    assert "| Billing service | Team Ledger | #oncall-ledger |" in text
    assert text.index("| Service | Team | On-call Slack channel |") < text.index("| Billing service")
    assert len(doc.images) == 2


def test_html_page(settings):
    doc = extract(str(DOCS / "lumora_it_helpdesk_faq.html"), settings)
    assert doc.title == "Lumora IT Helpdesk FAQ"
    assert "## How do I reset my password?" in doc.sections[0].text


def words(text):
    return len(text.split())


def test_long_text_is_split_with_size_limit_and_page():
    doc = ExtractedDoc(source="a.pdf", title="A", url="/files/a.pdf",
                       sections=[Section("word " * 1000, page=4)])
    chunks = chunk_document(doc, ["all"], max_tokens=100, overlap_tokens=10, count=words)
    assert len(chunks) >= 10
    assert all(words(c.content) <= 100 for c in chunks)
    assert all(c.page == 4 for c in chunks)


def test_every_chunk_carries_its_heading_path():
    text = "# Handbook\n\n## Leave\n\n### Sick leave\n\nNeeds a certificate after three days.\n\n## Expenses\n\nKeep receipts for every claim."
    doc = ExtractedDoc(source="h.md", title="Handbook", url="", sections=[Section(text)])
    chunks = chunk_document(doc, ["all"], count=words)
    assert [(c.section, c.content) for c in chunks] == [
        ("Leave > Sick leave", "Needs a certificate after three days."),
        ("Expenses", "Keep receipts for every claim."),
    ]
    assert chunks[0].embedding_text().startswith("Handbook > Leave > Sick leave")


def test_long_table_is_split_by_rows_with_header_repeated():
    rows = "\n".join(f"| service {i} | team {i} | #oncall-{i} |" for i in range(30))
    table = "| Service | Team | Channel |\n| --- | --- | --- |\n" + rows
    doc = ExtractedDoc(source="t.docx", title="T", url="", sections=[Section("## Owners\n\n" + table)])
    chunks = chunk_document(doc, ["all"], max_tokens=60, overlap_tokens=6, count=words)
    assert len(chunks) > 1
    for c in chunks:
        lines = c.content.split("\n")
        assert lines[0] == "| Service | Team | Channel |" and lines[1].startswith("| ---")
        assert all(line.startswith("|") and line.endswith("|") for line in lines)  # never cut mid-row
    assert sum(c.content.count("#oncall-") for c in chunks) == 30


def test_pdf_headings_and_table_are_detected(settings):
    doc = extract(str(DOCS / "Lumora_Employee_Handbook_2027.pdf"), settings)
    chunks = chunk_document(doc, ["all"])
    leave = [c for c in chunks if c.section == "2. Leave policy"]
    assert len(leave) == 1  # intro paragraph and its small table stay together
    assert "| Earned leave | 18 | Up to 10 days |" in leave[0].content


def test_ingest_indexes_text_and_images_with_access_groups(settings, llm):
    store = LocalStore(settings.local_index_path)
    stats = ingest([str(DOCS)], settings, llm, store)
    assert set(stats) == {"Lumora_Employee_Handbook_2027.pdf", "Lumora_Platform_Architecture.docx",
                          "lumora_it_helpdesk_faq.html"}
    images = [c for c in store.chunks.values() if c.kind == "image"]
    assert len(images) == 5
    arch = [c for c in store.chunks.values() if c.source == "Lumora_Platform_Architecture.docx"]
    assert all(c.groups == ["engineering", "admin"] for c in arch)

    # Re-ingesting replaces chunks instead of duplicating them, and the store survives a reload.
    before = store.count()
    ingest([str(DOCS / "lumora_it_helpdesk_faq.html")], settings, llm, store)
    assert store.count() == before
    assert LocalStore(settings.local_index_path).count() == before

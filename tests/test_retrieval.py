import pytest

from app.models import Chunk
from app.retrieval.store import LocalStore, rrf


def make_store(tmp_path, llm):
    store = LocalStore(tmp_path / "idx.json")
    chunks = [
        Chunk(source="hr.pdf", title="Handbook", content="Earned leave is 18 days per year."),
        Chunk(source="hr.pdf", title="Handbook", content="Sick leave needs a medical certificate."),
        Chunk(source="it.html", title="IT FAQ", content="Reset your password at the password portal."),
        Chunk(source="arch.docx", title="Architecture", content="The billing service is written in Go.",
              groups=["engineering"]),
    ]
    for c, v in zip(chunks, llm.embed([c.embedding_text() for c in chunks])):
        c.vector = v
    store.upsert(chunks)
    return store


def test_rrf_rewards_agreement():
    scores = rrf([["a", "b", "c"], ["b", "a", "d"]])
    assert scores["a"] == pytest.approx(scores["b"])
    assert scores["a"] > scores["c"] == pytest.approx(scores["d"])


def test_hybrid_search_finds_relevant_chunk(tmp_path, llm):
    store = make_store(tmp_path, llm)
    query = "how many days of earned leave"
    results = store.search(query, llm.embed([query])[0], k=2, groups=["all"])
    assert "18 days" in results[0].content


def test_security_trimming(tmp_path, llm):
    store = make_store(tmp_path, llm)
    query = "billing service language"
    employee = store.search(query, llm.embed([query])[0], k=4, groups=["all", "employee"])
    engineer = store.search(query, llm.embed([query])[0], k=4, groups=["all", "engineering"])
    assert all(c.source != "arch.docx" for c in employee)
    assert engineer[0].source == "arch.docx"

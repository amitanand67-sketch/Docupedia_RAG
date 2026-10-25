import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.auth import parse_client_principal
from app.history import MemoryHistory
from app.ingestion.pipeline import ingest
from app.main import app
from app.rag import RagService
from app.retrieval.store import LocalStore

from .conftest import ROOT


@pytest.fixture
def client(settings, llm):
    store = LocalStore(settings.local_index_path)
    ingest([str(ROOT / "sample_docs")], settings, llm, store)
    app.state.rag = RagService(settings, llm, store, MemoryHistory())
    with TestClient(app) as c:
        yield c
    del app.state.rag


def events(resp):
    return [json.loads(line[6:]) for line in resp.text.split("\n\n") if line.startswith("data: ")]


def test_chat_streams_sources_tokens_and_summary(client):
    resp = client.post("/api/chat", json={"question": "When is Founders' Day?", "session_id": "s1"})
    assert resp.status_code == 200
    evs = events(resp)
    assert evs[0]["type"] == "sources" and evs[0]["sources"]
    assert "".join(e["text"] for e in evs if e["type"] == "token") == "The answer is in the handbook [1]."
    assert evs[-1]["type"] == "done" and evs[-1]["prompt_tokens"] == 100


def test_follow_up_question_is_rewritten(client, llm):
    client.post("/api/chat", json={"question": "What is the leave policy?", "session_id": "s2"})
    resp = client.post("/api/chat", json={"question": "and for sick leave?", "session_id": "s2"})
    first = events(resp)[0]
    assert first["query"] == "and for sick leave? (rewritten)"
    # the model sees the earlier turn as conversation history
    assert any(m["content"] == "What is the leave policy?" for m in llm.prompts[-1])


def test_employee_cannot_retrieve_or_open_restricted_doc(client):
    resp = client.post("/api/chat", json={"question": "OrbitDesk billing service Kong gateway", "session_id": "s3"},
                       headers={"X-Demo-User": "Asha (employee)"})
    assert all(s["source"] != "Lumora_Platform_Architecture.docx" for s in events(resp)[0]["sources"])
    assert client.get("/files/Lumora_Platform_Architecture.docx?as=Asha (employee)").status_code == 403
    assert client.get("/files/Lumora_Platform_Architecture.docx?as=Ravi (engineer)").status_code == 200
    assert client.get("/files/access.json").status_code == 404
    assert client.get("/files/..%2F.env").status_code == 404


def test_feedback_and_admin_analytics(client):
    done = events(client.post("/api/chat", json={"question": "VPN setup", "session_id": "s4"}))[-1]
    ok = client.post("/api/feedback", json={"session_id": "s4", "message_id": done["message_id"], "rating": 1})
    assert ok.status_code == 200
    assert client.get("/api/analytics").status_code == 403
    stats = client.get("/api/analytics", headers={"X-Demo-User": "Admin"}).json()
    assert stats["questions"] == 1 and stats["feedback"]["up"] == 1 and stats["satisfaction"] == 100


def test_entra_client_principal_header():
    principal = {"claims": [{"typ": "name", "val": "Plitt"}, {"typ": "groups", "val": "engineering"}]}
    user = parse_client_principal(base64.b64encode(json.dumps(principal).encode()).decode())
    assert user.name == "Plitt" and "engineering" in user.groups


def test_history_survives_restart_and_page_refresh(client, settings, tmp_path):
    from app.history import MemoryHistory

    path = tmp_path / "history.json"
    app.state.rag.history = MemoryHistory(path)
    client.post("/api/chat", json={"question": "What is the leave policy?", "session_id": "s5"})

    # A restarted server reads the same file, and the page can fetch the conversation again.
    app.state.rag.history = MemoryHistory(path)
    messages = client.get("/api/history", params={"session_id": "s5"}).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "What is the leave policy?"
    # Another user can't read it.
    other = client.get("/api/history", params={"session_id": "s5"}, headers={"X-Demo-User": "Admin"})
    assert other.status_code == 403


def test_rate_limits(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "limiter", main.RateLimiter())
    monkeypatch.setattr(main.get_settings(), "rate_limit_per_hour", 2)
    ask = lambda: client.post("/api/chat", json={"question": "VPN?", "session_id": "s6"})  # noqa: E731
    assert ask().status_code == 200 and ask().status_code == 200
    blocked = ask()
    assert blocked.status_code == 429 and "last hour" in blocked.json()["detail"]

    monkeypatch.setattr(main, "limiter", main.RateLimiter())
    monkeypatch.setattr(main.get_settings(), "rate_limit_per_hour", 0)
    monkeypatch.setattr(main.get_settings(), "daily_question_limit", 1)
    assert ask().status_code == 200
    assert ask().status_code == 429


def test_entra_admin_by_email():
    principal = {"claims": [{"typ": "name", "val": "Plitt"}, {"typ": "preferred_username", "val": "Me@Example.com"}]}
    header = base64.b64encode(json.dumps(principal).encode()).decode()
    assert "admin" in parse_client_principal(header, "me@example.com").groups
    assert "admin" not in parse_client_principal(header, "").groups

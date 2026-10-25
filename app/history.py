"""Conversation memory, answer logs and feedback: a JSON file locally, Cosmos DB in the cloud.

Every answer is logged with its latency, token usage and sources. The analytics page and the
thumbs up/down feedback both read these records.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Protocol

from .config import Settings


class HistoryStore(Protocol):
    def get_turns(self, session_id: str, limit: int) -> list[dict]: ...
    def session_user(self, session_id: str) -> str | None: ...
    def add_turn(self, session_id: str, user: str, role: str, content: str) -> None: ...
    def log_answer(self, record: dict) -> None: ...
    def set_feedback(self, session_id: str, message_id: str, rating: int, comment: str) -> bool: ...
    def recent_answers(self, limit: int) -> list[dict]: ...


class MemoryHistory:
    """Keeps history in memory; with a path it is also saved to a JSON file so it survives restarts."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.turns: dict[str, list[dict]] = {}
        self.answers: dict[str, dict] = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.turns, self.answers = data.get("turns", {}), data.get("answers", {})

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"turns": self.turns, "answers": self.answers}), encoding="utf-8")

    def get_turns(self, session_id: str, limit: int) -> list[dict]:
        return [{"role": t["role"], "content": t["content"]} for t in self.turns.get(session_id, [])[-limit:]]

    def session_user(self, session_id: str) -> str | None:
        turns = self.turns.get(session_id)
        return turns[0].get("user") if turns else None

    def add_turn(self, session_id: str, user: str, role: str, content: str) -> None:
        self.turns.setdefault(session_id, []).append({"role": role, "content": content, "user": user})
        self._save()

    def log_answer(self, record: dict) -> None:
        self.answers[record["id"]] = record
        self._save()

    def set_feedback(self, session_id: str, message_id: str, rating: int, comment: str) -> bool:
        record = self.answers.get(message_id)
        if not record or record["session_id"] != session_id:
            return False
        record.update(rating=rating, comment=comment)
        self._save()
        return True

    def recent_answers(self, limit: int) -> list[dict]:
        return sorted(self.answers.values(), key=lambda r: -r["ts"])[:limit]


class CosmosHistory:
    """One container, partitioned by session_id; documents are typed "turn" or "answer"."""

    def __init__(self, settings: Settings):
        from azure.cosmos import CosmosClient, PartitionKey

        credential = settings.cosmos_key
        if not credential:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        client = CosmosClient(settings.cosmos_endpoint, credential=credential)
        db = client.create_database_if_not_exists(settings.cosmos_database)
        self.container = db.create_container_if_not_exists(
            id=settings.cosmos_container, partition_key=PartitionKey(path="/session_id")
        )

    def get_turns(self, session_id: str, limit: int) -> list[dict]:
        items = self.container.query_items(
            "SELECT TOP @n c.role, c.content, c.ts FROM c WHERE c.session_id = @s AND c.type = 'turn' "
            "ORDER BY c.ts DESC",
            parameters=[{"name": "@n", "value": limit}, {"name": "@s", "value": session_id}],
            partition_key=session_id,
        )
        return [{"role": i["role"], "content": i["content"]} for i in reversed(list(items))]

    def session_user(self, session_id: str) -> str | None:
        items = list(self.container.query_items(
            "SELECT TOP 1 c.user FROM c WHERE c.session_id = @s AND c.type = 'turn'",
            parameters=[{"name": "@s", "value": session_id}],
            partition_key=session_id,
        ))
        return items[0]["user"] if items else None

    def add_turn(self, session_id: str, user: str, role: str, content: str) -> None:
        import uuid

        self.container.create_item({"id": str(uuid.uuid4()), "type": "turn", "session_id": session_id,
                                    "user": user, "role": role, "content": content, "ts": time.time()})

    def log_answer(self, record: dict) -> None:
        self.container.upsert_item({**record, "type": "answer"})

    def set_feedback(self, session_id: str, message_id: str, rating: int, comment: str) -> bool:
        try:
            item = self.container.read_item(message_id, partition_key=session_id)
        except Exception:
            return False
        item.update(rating=rating, comment=comment)
        self.container.replace_item(item, item)
        return True

    def recent_answers(self, limit: int) -> list[dict]:
        return list(self.container.query_items(
            "SELECT TOP @n * FROM c WHERE c.type = 'answer' ORDER BY c.ts DESC",
            parameters=[{"name": "@n", "value": limit}],
            enable_cross_partition_query=True,
        ))


def summarize(records: list[dict]) -> dict:
    """Numbers for the analytics page."""
    if not records:
        return {"questions": 0, "users": 0, "avg_latency_ms": 0, "p95_latency_ms": 0, "avg_tokens": 0,
                "feedback": {"up": 0, "down": 0}, "satisfaction": None, "top_sources": [], "unanswered": 0,
                "recent": []}
    latencies = sorted(r["latency_ms"] for r in records)
    up = sum(1 for r in records if r.get("rating") == 1)
    down = sum(1 for r in records if r.get("rating") == -1)
    sources = Counter(s for r in records for s in {x["source"] for x in r.get("sources", [])})
    return {
        "questions": len(records),
        "users": len({r["user"] for r in records}),
        "avg_latency_ms": round(sum(latencies) / len(latencies)),
        "p95_latency_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
        "avg_tokens": round(sum(r["prompt_tokens"] + r["completion_tokens"] for r in records) / len(records)),
        "feedback": {"up": up, "down": down},
        "satisfaction": round(100 * up / (up + down)) if up + down else None,
        "top_sources": sources.most_common(5),
        "unanswered": sum(1 for r in records if not r.get("sources")),
        "recent": [{k: r.get(k) for k in ("ts", "user", "question", "latency_ms", "rating")} for r in records[:15]],
    }


def make_history(settings: Settings) -> HistoryStore:
    if settings.history_backend == "cosmos":
        return CosmosHistory(settings)
    return MemoryHistory(settings.local_history_path)

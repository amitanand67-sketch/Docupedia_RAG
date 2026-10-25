"""The RAG chain: rewrite the question, retrieve chunks, prompt the model, stream a cited answer."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

from .config import Settings
from .history import HistoryStore
from .llm import StreamResult, Usage
from .models import Chunk, User

REWRITE_PROMPT = """Rewrite the user's latest question as a single standalone search query, \
using the conversation for context (resolve words like "it", "that", "the second one"). \
Return only the rewritten question. If it is already standalone, return it unchanged.

Conversation:
{history}

Latest question: {question}"""

SYSTEM_PROMPT = """You are DocupediaAI, an assistant that answers employee questions using ONLY the \
numbered sources below, which come from internal documents (some are descriptions of images such as \
calendars and diagrams).

Rules:
- Answer only from the sources. If they do not contain the answer, say you could not find it in the \
documents and suggest who or what to check. Never use outside knowledge about the company.
- Cite every fact with its source number in square brackets, like [1] or [2][3].
- Be concise: lead with the direct answer, then short supporting detail or a bullet list.
- If the question is ambiguous, answer the most likely reading and say which one you chose.

Sources:
{sources}"""

NO_RESULTS = "I couldn't find anything about that in the documents you have access to."


def format_sources(chunks: list[Chunk]) -> str:
    parts = []
    for i, c in enumerate(chunks, start=1):
        where = f", page {c.page}" if c.page else ""
        section = f" > {c.section}" if c.section else ""
        parts.append(f"[{i}] {c.title}{section}{where}\n{c.content}")
    return "\n\n".join(parts)


def format_history(turns: list[dict]) -> str:
    return "\n".join(f"{t['role']}: {t['content']}" for t in turns)


class RagService:
    def __init__(self, settings: Settings, llm, store, history: HistoryStore):
        self.settings = settings
        self.llm = llm
        self.store = store
        self.history = history

    def standalone_question(self, question: str, turns: list[dict], usage: Usage) -> str:
        if not turns:
            return question
        text, used = self.llm.chat(
            [{"role": "user", "content": REWRITE_PROMPT.format(history=format_history(turns), question=question)}],
            max_tokens=150,
        )
        usage.add(used)
        return text.strip() or question

    def retrieve(self, query: str, user: User) -> list[Chunk]:
        vector = self.llm.embed([query])[0]
        return self.store.search(query, vector, self.settings.top_k, user.can_see())

    def answer_stream(self, question: str, session_id: str, user: User) -> Iterator[dict]:
        """Yield events for the UI: sources first, then answer tokens, then a summary."""
        started = time.perf_counter()
        message_id = str(uuid.uuid4())
        usage = Usage()
        turns = self.history.get_turns(session_id, self.settings.history_turns)

        query = self.standalone_question(question, turns, usage)
        chunks = self.retrieve(query, user)
        retrieval_ms = round((time.perf_counter() - started) * 1000)
        yield {
            "type": "sources",
            "message_id": message_id,
            "query": query,
            "sources": [{"n": i, "title": c.title, "section": c.section, "source": c.source, "page": c.page, "url": c.url,
                         "kind": c.kind, "score": c.score, "snippet": c.content[:300]}
                        for i, c in enumerate(chunks, start=1)],
        }

        answer = ""
        if not chunks:
            answer = NO_RESULTS
            yield {"type": "token", "text": answer}
        else:
            messages = [{"role": "system", "content": SYSTEM_PROMPT.format(sources=format_sources(chunks))},
                        *turns, {"role": "user", "content": question}]
            result = StreamResult()
            for token in self.llm.stream(messages, result):
                answer += token
                yield {"type": "token", "text": token}
            usage.add(result.usage)

        latency_ms = round((time.perf_counter() - started) * 1000)
        self.history.add_turn(session_id, user.name, "user", question)
        self.history.add_turn(session_id, user.name, "assistant", answer)
        self.history.log_answer({
            "id": message_id, "session_id": session_id, "user": user.name, "ts": time.time(),
            "question": question, "standalone_query": query, "answer": answer,
            "sources": [{"source": c.source, "page": c.page} for c in chunks],
            "latency_ms": latency_ms, "retrieval_ms": retrieval_ms,
            "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
        })
        yield {"type": "done", "message_id": message_id, "latency_ms": latency_ms, "retrieval_ms": retrieval_ms,
               "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens}

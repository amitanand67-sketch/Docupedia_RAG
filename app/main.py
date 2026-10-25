"""FastAPI app: chat API with streaming, feedback, analytics, and the web UI.

Run locally:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import DEMO_USERS, current_user
from .config import ROOT, get_settings
from .history import make_history, summarize
from .models import User
from .rag import RagService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("docupedia")


def build_service() -> RagService:
    from .llm import AzureLLM
    from .retrieval.store import make_store

    settings = get_settings()
    if settings.applicationinsights_connection_string:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=settings.applicationinsights_connection_string)
    return RagService(settings, AzureLLM(settings), make_store(settings), make_history(settings))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "rag"):  # tests inject a fake service
        app.state.rag = build_service()
    log.info("Index holds %d chunks", app.state.rag.store.count())
    yield


app = FastAPI(title="DocupediaAI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(min_length=1, max_length=100)


class FeedbackRequest(BaseModel):
    session_id: str
    message_id: str
    rating: int = Field(ge=-1, le=1)
    comment: str = Field(default="", max_length=1000)


def rag(request: Request) -> RagService:
    return request.app.state.rag


class RateLimiter:
    """Caps questions per visitor per hour and per app per day, so a public link can't run up a bill.

    Counts live in memory, so each app copy keeps its own; the deployment runs a single copy.
    """

    def __init__(self) -> None:
        self.visitors: dict[str, deque] = {}
        self.day = ""
        self.today = 0

    def check(self, visitor: str, per_hour: int, per_day: int) -> str | None:
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if day != self.day:
            self.day, self.today, self.visitors = day, 0, {}
        if per_day and self.today >= per_day:
            return "The demo has reached its daily question limit. Please try again tomorrow."
        recent = self.visitors.setdefault(visitor, deque())
        while recent and now - recent[0] > 3600:
            recent.popleft()
        if per_hour and len(recent) >= per_hour:
            return f"You've asked {per_hour} questions in the last hour. Please wait a little and try again."
        recent.append(now)
        self.today += 1
        return None


limiter = RateLimiter()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/files/{name}")
def source_file(name: str, user: User = Depends(current_user)) -> FileResponse:
    """Serve an original document for citation links, applying the same access rules as search."""
    docs_dir = get_settings().docs_dir.resolve()
    path = (docs_dir / name).resolve()
    if path.parent != docs_dir or not path.is_file() or name == "access.json":
        raise HTTPException(status_code=404, detail="Not found")
    access_file = docs_dir / "access.json"
    access = json.loads(access_file.read_text()) if access_file.exists() else {}
    if not set(access.get(name, ["all"])) & set(user.can_see()):
        raise HTTPException(status_code=403, detail="You don't have access to this document")
    return FileResponse(path)


@app.get("/health")
def health(service: RagService = Depends(rag)) -> dict:
    return {"status": "ok", "chunks": service.store.count()}


@app.get("/api/me")
def me(user: User = Depends(current_user)) -> dict:
    settings = get_settings()
    return {"name": user.name, "groups": user.groups, "is_admin": settings.admin_group in user.groups,
            "auth_mode": settings.auth_mode, "demo_users": list(DEMO_USERS) if settings.auth_mode == "demo" else []}


@app.post("/api/chat")
def chat(body: ChatRequest, request: Request, user: User = Depends(current_user),
         service: RagService = Depends(rag)):
    settings = get_settings()
    visitor = request.client.host if request.client else "unknown"
    refusal = limiter.check(visitor, settings.rate_limit_per_hour, settings.daily_question_limit)
    if refusal:
        raise HTTPException(status_code=429, detail=refusal)

    def events():
        try:
            for event in service.answer_stream(body.question.strip(), body.session_id, user):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # surface errors to the UI instead of a silent broken stream
            log.exception("chat failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/history")
def history(session_id: str, user: User = Depends(current_user), service: RagService = Depends(rag)) -> dict:
    """Earlier messages of a conversation, so a page refresh can show them again."""
    if service.history.session_user(session_id) not in (None, user.name):
        raise HTTPException(status_code=403, detail="Not your conversation")
    return {"messages": service.history.get_turns(session_id, 200)}


@app.post("/api/feedback")
def feedback(body: FeedbackRequest, service: RagService = Depends(rag)) -> dict:
    if not service.history.set_feedback(body.session_id, body.message_id, body.rating, body.comment):
        raise HTTPException(status_code=404, detail="Answer not found")
    return {"ok": True}


@app.get("/api/analytics")
def analytics(user: User = Depends(current_user), service: RagService = Depends(rag)) -> dict:
    if get_settings().admin_group not in user.groups:
        raise HTTPException(status_code=403, detail="Admins only")
    return summarize(service.history.recent_answers(500))

"""Shared fakes so tests run offline, without Azure or API costs."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.llm import StreamResult, Usage  # noqa: E402
from app.retrieval.store import tokenize  # noqa: E402


class FakeLLM:
    """Bag-of-words embeddings and canned answers that cite source [1]."""

    dims = 256

    def __init__(self):
        self.prompts: list[list[dict]] = []

    def embed(self, texts):
        out = []
        for text in texts:
            v = np.zeros(self.dims)
            for tok in tokenize(text):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dims] += 1
            out.append(v.tolist())
        return out

    def chat(self, messages, temperature=0.0, max_tokens=800):
        self.prompts.append(messages)
        content = messages[-1]["content"]
        if "Latest question:" in content:
            return content.rsplit("Latest question:", 1)[1].strip() + " (rewritten)", Usage(10, 5)
        return "ok", Usage(1, 1)

    def stream(self, messages, result: StreamResult, temperature=0.1):
        self.prompts.append(messages)
        result.usage = Usage(100, 20)
        yield from ["The answer ", "is in the handbook [1]."]

    def describe_image(self, image, mime, title):
        return f"Image from {title}: a holiday calendar with Founders' Day on April 16."


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, azure_openai_endpoint="https://fake", local_index_path=tmp_path / "index.json",
                    docs_dir=ROOT / "sample_docs", top_k=4)


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()

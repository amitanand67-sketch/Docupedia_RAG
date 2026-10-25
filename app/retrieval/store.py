"""Search index: a local JSON store for laptop development, Azure AI Search in the cloud.

Both do hybrid retrieval: a vector search (meaning) and a keyword search (exact words) whose
rankings are merged with Reciprocal Rank Fusion, the same fusion Azure AI Search uses.
Both also apply security trimming: a user only retrieves chunks whose groups they belong to.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

import numpy as np

from ..config import Settings
from ..models import Chunk

RRF_K = 60
TOKEN = re.compile(r"[a-z0-9]+")


class VectorStore(Protocol):
    def upsert(self, chunks: list[Chunk]) -> None: ...
    def delete_source(self, source: str) -> None: ...
    def search(self, query: str, vector: list[float], k: int, groups: list[str]) -> list[Chunk]: ...
    def count(self) -> int: ...


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


class LocalStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.chunks: dict[str, Chunk] = {}
        if self.path.exists():
            for raw in json.loads(self.path.read_text(encoding="utf-8")):
                chunk = Chunk(**raw)
                self.chunks[chunk.id] = chunk

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(c) | {"score": 0.0} for c in self.chunks.values()]
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def upsert(self, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            self.chunks[chunk.id] = chunk
        self._save()

    def delete_source(self, source: str) -> None:
        self.chunks = {i: c for i, c in self.chunks.items() if c.source != source}
        self._save()

    def count(self) -> int:
        return len(self.chunks)

    def search(self, query: str, vector: list[float], k: int, groups: list[str]) -> list[Chunk]:
        allowed = set(groups)
        visible = [c for c in self.chunks.values() if allowed & set(c.groups) and c.vector]
        if not visible:
            return []
        candidates = max(k * 4, 20)

        matrix = np.array([c.vector for c in visible], dtype=np.float32)
        q = np.array(vector, dtype=np.float32)
        cosine = matrix @ q / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(q) + 1e-9)
        by_vector = [visible[i].id for i in np.argsort(-cosine)[:candidates]]

        bm25 = self._bm25(query, visible)
        by_keyword = [cid for cid, s in sorted(bm25.items(), key=lambda x: -x[1]) if s > 0][:candidates]

        fused = rrf([by_vector, by_keyword])
        best = sorted(fused, key=lambda cid: -fused[cid])[:k]
        results = []
        for cid in best:
            chunk = self.chunks[cid]
            results.append(Chunk(**{**asdict(chunk), "score": round(fused[cid], 4)}))
        return results

    @staticmethod
    def _bm25(query: str, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> dict[str, float]:
        docs = {c.id: tokenize(c.embedding_text()) for c in chunks}
        avg_len = sum(len(d) for d in docs.values()) / max(len(docs), 1)
        df = Counter(term for d in docs.values() for term in set(d))
        n = len(docs)
        scores = {}
        for cid, tokens in docs.items():
            tf = Counter(tokens)
            score = 0.0
            for term in set(tokenize(query)):
                if term not in tf:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(tokens) / avg_len))
            scores[cid] = score
        return scores


class AzureSearchStore:
    VECTOR_FIELD = "content_vector"
    SEMANTIC_CONFIG = "default"

    def __init__(self, settings: Settings):
        from azure.search.documents import SearchClient
        from azure.search.documents.indexes import SearchIndexClient

        if settings.azure_search_api_key:
            from azure.core.credentials import AzureKeyCredential

            credential = AzureKeyCredential(settings.azure_search_api_key)
        else:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        self.settings = settings
        self.index_client = SearchIndexClient(settings.azure_search_endpoint, credential)
        self.client = SearchClient(settings.azure_search_endpoint, settings.azure_search_index, credential)

    def ensure_index(self) -> None:
        from azure.search.documents.indexes.models import (
            HnswAlgorithmConfiguration,
            SearchableField,
            SearchField,
            SearchFieldDataType,
            SearchIndex,
            SemanticConfiguration,
            SemanticField,
            SemanticPrioritizedFields,
            SemanticSearch,
            SimpleField,
            VectorSearch,
            VectorSearchProfile,
        )

        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="title", type=SearchFieldDataType.String),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SimpleField(name="page", type=SearchFieldDataType.Int32),
            SearchableField(name="section", type=SearchFieldDataType.String),
            SimpleField(name="kind", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="url", type=SearchFieldDataType.String),
            SimpleField(name="groups", type=SearchFieldDataType.Collection(SearchFieldDataType.String),
                        filterable=True),
            SearchField(name=self.VECTOR_FIELD, type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                        searchable=True, vector_search_dimensions=self.settings.embedding_dimensions,
                        vector_search_profile_name="hnsw-profile"),
        ]
        index = SearchIndex(
            name=self.settings.azure_search_index,
            fields=fields,
            vector_search=VectorSearch(
                algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
                profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw")],
            ),
            semantic_search=SemanticSearch(configurations=[SemanticConfiguration(
                name=self.SEMANTIC_CONFIG,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )]),
        )
        self.index_client.create_or_update_index(index)

    def upsert(self, chunks: list[Chunk]) -> None:
        self.ensure_index()
        docs = [{"id": c.id, "source": c.source, "title": c.title, "content": c.content, "page": c.page,
                 "section": c.section,
                 "kind": c.kind, "url": c.url, "groups": c.groups, self.VECTOR_FIELD: c.vector} for c in chunks]
        for i in range(0, len(docs), 500):
            self._upload(docs[i : i + 500])

    def _upload(self, docs: list[dict], attempts: int = 6) -> None:
        """A just-created index can take a few seconds to accept documents, so retry "not found"."""
        import time

        from azure.core.exceptions import ResourceNotFoundError

        for attempt in range(attempts):
            try:
                self.client.upload_documents(docs)
                return
            except ResourceNotFoundError:
                if attempt == attempts - 1:
                    raise
                time.sleep(5)

    def delete_source(self, source: str) -> None:
        try:
            escaped = source.replace("'", "''")
            hits = self.client.search(search_text="*", filter=f"source eq '{escaped}'", select=["id"], top=1000)
            ids = [{"id": h["id"]} for h in hits]
        except Exception:  # index does not exist yet
            return
        if ids:
            self.client.delete_documents(ids)

    def count(self) -> int:
        try:
            return self.client.get_document_count()
        except Exception:
            return 0

    def search(self, query: str, vector: list[float], k: int, groups: list[str]) -> list[Chunk]:
        from azure.search.documents.models import VectorizedQuery

        group_list = ",".join(g.replace(",", "") for g in groups)
        kwargs = {}
        if self.settings.use_semantic_ranker:
            kwargs = {"query_type": "semantic", "semantic_configuration_name": self.SEMANTIC_CONFIG}
        results = self.client.search(
            search_text=query,
            vector_queries=[VectorizedQuery(vector=vector, k_nearest_neighbors=max(k * 4, 20),
                                            fields=self.VECTOR_FIELD)],
            filter=f"groups/any(g: search.in(g, '{group_list}', ','))",
            select=["id", "source", "title", "content", "page", "section", "kind", "url", "groups"],
            top=k,
            **kwargs,
        )
        out = []
        for r in results:
            score = r.get("@search.reranker_score") or r.get("@search.score") or 0.0
            out.append(Chunk(id=r["id"], source=r["source"], title=r["title"], content=r["content"],
                             page=r.get("page"), section=r.get("section") or "", kind=r.get("kind", "text"), url=r.get("url", ""),
                             groups=r.get("groups", ["all"]), score=round(float(score), 4)))
        return out


def make_store(settings: Settings) -> VectorStore:
    if settings.search_backend == "azure":
        return AzureSearchStore(settings)
    return LocalStore(settings.local_index_path)

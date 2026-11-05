"""Semantic index over code units, endpoints, tables, business rules and glossary terms.

Embeddings are stored as float32 blobs in SQLite (table `embeddings`, inside kb.sqlite);
similarity is cosine computed in pure Python (fine up to ~100k fragments; swap for a
vector DB if a project outgrows that). Documents are re-embedded only when their text hash changes.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from typing import Protocol

from devanalyzer.config import Settings, get_settings
from devanalyzer.core.models import BusinessRule, CodeUnitKind, GlossaryTerm, ProjectAnalysis
from devanalyzer.kb.store import KnowledgeBase

SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    project TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT,
    text TEXT,
    file TEXT,
    line INTEGER,
    content_hash TEXT,
    model TEXT,
    dims INTEGER,
    vector BLOB,
    PRIMARY KEY (project, doc_id)
);
"""


class EmbeddingProvider(Protocol):
    name: str
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbeddings:
    name = "ollama"

    def __init__(self, model: str, host: str):
        from ollama import Client

        self.model = model
        self.client = Client(host=host)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            out.extend(self.client.embed(model=self.model, input=texts[i : i + 32])["embeddings"])
        return out


class OpenAIEmbeddings:
    name = "openai"

    def __init__(self, model: str, api_key: str | None):
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        res = self.client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in res.data]


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddings(
            settings.embedding_model if settings.embedding_model != "nomic-embed-text" else "text-embedding-3-small", settings.openai_api_key
        )
    return OllamaEmbeddings(settings.embedding_model, settings.ollama_host)


@dataclass
class Document:
    doc_id: str
    kind: str
    title: str
    text: str
    file: str | None = None
    line: int | None = None


@dataclass
class Hit:
    doc_id: str
    kind: str
    title: str
    score: float
    file: str | None
    line: int | None
    snippet: str


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes, dims: int) -> list[float]:
    return list(struct.unpack(f"{dims}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def build_documents(analysis: ProjectAnalysis, rules: list[BusinessRule] | None = None, glossary: list[GlossaryTerm] | None = None) -> list[Document]:
    docs: list[Document] = []
    stack = list(analysis.units)
    while stack:
        u = stack.pop()
        if u.kind in (CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD, CodeUnitKind.MODULE, CodeUnitKind.FUNCTION):
            members = [c for c in u.children if c.kind in (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION)]
            fields = [c for c in u.children if c.kind == CodeUnitKind.FIELD]
            text = " ".join(
                filter(
                    None,
                    [
                        f"{u.kind.value} {u.name}",
                        f"stereotype {u.stereotype}" if u.stereotype else None,
                        f"package {u.package}" if u.package else None,
                        u.docstring,
                        "methods: " + ", ".join(f"{m.name}({', '.join(p.type or p.name for p in m.parameters)})" for m in members[:20]) if members else None,
                        "fields: " + ", ".join(f"{f.name}:{f.type}" if f.type else f.name for f in fields[:20]) if fields else None,
                        "annotations: " + ", ".join(a.name for a in u.annotations) if u.annotations else None,
                    ],
                )
            )
            docs.append(Document(doc_id=u.fqn, kind=u.kind.value, title=u.fqn, text=text, file=u.file, line=u.line_start))
            for m in members:
                mtext = " ".join(
                    filter(
                        None,
                        [
                            f"method {u.name}.{m.name}",
                            m.docstring,
                            "calls: " + ", ".join(m.calls[:15]) if m.calls else None,
                            "params: " + ", ".join(f"{p.name}:{p.type}" for p in m.parameters) if m.parameters else None,
                        ],
                    )
                )
                docs.append(Document(doc_id=m.fqn, kind="method", title=m.fqn, text=mtext, file=m.file, line=m.line_start))
            stack.extend(c for c in u.children if c.kind in (CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD))
    for ep in analysis.endpoints:
        docs.append(
            Document(
                doc_id=f"endpoint:{ep.http_method} {ep.path}",
                kind="endpoint",
                title=f"{ep.http_method} {ep.path}",
                text=f"HTTP endpoint {ep.http_method} {ep.path} handled by {ep.handler} ({ep.framework})",
                file=ep.file,
                line=ep.line,
            )
        )
    for t in analysis.db_schema.tables:
        docs.append(
            Document(
                doc_id=f"table:{t.name}",
                kind="table",
                title=t.name,
                text=f"database table {t.name} columns: "
                + ", ".join(c.name for c in t.columns)
                + (" references: " + ", ".join(fk.ref_table for fk in t.foreign_keys) if t.foreign_keys else "")
                + (" entities: " + ", ".join(t.mapped_entities) if t.mapped_entities else ""),
                file=t.source_file,
                line=t.line,
            )
        )
    for r in rules or []:
        ev = r.evidence[0] if r.evidence else None
        docs.append(
            Document(
                doc_id=f"rule:{r.id}",
                kind="business_rule",
                title=r.id,
                text=f"business rule ({r.category}): {r.statement} {r.rationale or ''} terms: {', '.join(r.domain_terms)}",
                file=ev.file if ev else None,
                line=ev.line_start if ev else None,
            )
        )
    for g in glossary or []:
        docs.append(Document(doc_id=f"term:{g.term}", kind="glossary_term", title=g.term, text=f"domain term {g.term}: {g.definition}"))
    return docs


class SemanticIndex:
    def __init__(self, kb: KnowledgeBase, provider: EmbeddingProvider, project: str):
        self.kb = kb
        self.provider = provider
        self.project = project
        self.kb.conn.executescript(SCHEMA)

    def index(self, docs: list[Document], force: bool = False) -> dict[str, int]:
        existing = {
            row[0]: row[1]
            for row in self.kb.conn.execute("SELECT doc_id, content_hash FROM embeddings WHERE project=? AND model=?", (self.project, self.provider.model))
        }
        todo = []
        for d in docs:
            h = hashlib.sha1(d.text.encode("utf-8")).hexdigest()
            if force or existing.get(d.doc_id) != h:
                todo.append((d, h))
        vectors = self.provider.embed([d.text for d, _ in todo]) if todo else []
        for (d, h), vec in zip(todo, vectors):
            self.kb.conn.execute(
                "INSERT OR REPLACE INTO embeddings (project, doc_id, kind, title, text, file, line, content_hash, model, dims, vector) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.project, d.doc_id, d.kind, d.title, d.text, d.file, d.line, h, self.provider.model, len(vec), _pack(vec)),
            )
        current = {d.doc_id for d in docs}
        stale = [doc_id for doc_id in existing if doc_id not in current]
        if stale:
            self.kb.conn.executemany("DELETE FROM embeddings WHERE project=? AND doc_id=?", [(self.project, s) for s in stale])
        self.kb.conn.commit()
        return {"indexed": len(todo), "unchanged": len(docs) - len(todo), "removed": len(stale), "total": len(docs)}

    def search(self, query: str, limit: int = 10, kinds: set[str] | None = None, min_score: float = 0.0) -> list[Hit]:
        qvec = self.provider.embed([query])[0]
        rows = self.kb.conn.execute(
            "SELECT doc_id, kind, title, text, file, line, dims, vector FROM embeddings WHERE project=? AND model=?", (self.project, self.provider.model)
        ).fetchall()
        scored: list[Hit] = []
        for doc_id, kind, title, text, file, line, dims, blob in rows:
            if kinds and kind not in kinds:
                continue
            score = _cosine(qvec, _unpack(blob, dims))
            if score >= min_score:
                scored.append(Hit(doc_id=doc_id, kind=kind, title=title, score=round(score, 4), file=file, line=line, snippet=text[:200]))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:limit]

    def count(self) -> int:
        return self.kb.conn.execute("SELECT COUNT(*) FROM embeddings WHERE project=? AND model=?", (self.project, self.provider.model)).fetchone()[0]

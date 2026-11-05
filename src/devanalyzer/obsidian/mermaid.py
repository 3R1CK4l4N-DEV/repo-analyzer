"""Mermaid diagram builders used by the Obsidian vault and reports."""

from __future__ import annotations

import re
from collections import defaultdict

from devanalyzer.core.models import CodeUnit, CodeUnitKind, Endpoint, RelationKind, SchemaInfo
from devanalyzer.graph.knowledge_graph import KnowledgeGraph

STEREOTYPE_STYLE = {
    "controller": "fill:#4f86c6,color:#fff",
    "service": "fill:#5bb974,color:#fff",
    "repository": "fill:#e0a830,color:#fff",
    "entity": "fill:#b86bd1,color:#fff",
}
MAX_CLASS_NODES = 40
MAX_SEQUENCE_DEPTH = 6


def _mid(fqn: str) -> str:
    """Mermaid-safe node id."""
    return re.sub(r"[^A-Za-z0-9_]", "_", fqn)


def _label(fqn: str) -> str:
    return fqn.rsplit(".", 1)[-1].replace("/", "_")


def class_diagram(types: list[CodeUnit], graph: KnowledgeGraph, title: str | None = None, max_nodes: int = MAX_CLASS_NODES) -> str:
    """classDiagram with inheritance, dependencies and stereotypes for the given types."""
    chosen = sorted(types, key=lambda u: (u.stereotype is None, u.name))[:max_nodes]
    ids = {u.fqn for u in chosen}
    lines = ["```mermaid", "classDiagram"]
    if title:
        lines.insert(1, f"%% {title}")
    for u in chosen:
        kind = {"interface": "<<interface>>", "enum": "<<enumeration>>", "record": "<<record>>"}.get(u.kind.value)
        stereo = f"<<{u.stereotype}>>" if u.stereotype else None
        lines.append(f'    class {_mid(u.fqn)}["{_label(u.fqn)}"] {{')
        for tag in (kind, stereo):
            if tag:
                lines.append(f"        {tag}")
        methods = [c for c in u.children if c.kind in (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION) and c.name not in ("constructor", "__init__", u.name)]
        for m in methods[:8]:
            params = ", ".join(p.type or p.name for p in m.parameters)[:40]
            ret = f" {m.type}" if m.type and len(m.type) < 25 else ""
            lines.append(f"        +{m.name}({params}){ret}")
        if len(methods) > 8:
            lines.append(f"        ...{len(methods) - 8} more")
        lines.append("    }")
    seen: set[tuple[str, str, str]] = set()
    for u, v, k in graph.g.edges(keys=True):
        if u not in ids or v not in ids or (u, v, k) in seen:
            continue
        seen.add((u, v, k))
        if k == RelationKind.EXTENDS.value:
            lines.append(f"    {_mid(v)} <|-- {_mid(u)}")
        elif k == RelationKind.IMPLEMENTS.value:
            lines.append(f"    {_mid(v)} <|.. {_mid(u)}")
        elif k == RelationKind.DEPENDS_ON.value:
            lines.append(f"    {_mid(u)} --> {_mid(v)}")
    for u in chosen:
        if u.stereotype in STEREOTYPE_STYLE:
            lines.append(f"    style {_mid(u.fqn)} {STEREOTYPE_STYLE[u.stereotype]}")
    lines.append("```")
    return "\n".join(lines)


def er_diagram(schema: SchemaInfo, max_tables: int = 60) -> str | None:
    """erDiagram with columns and FK relationships."""
    if not schema.tables:
        return None
    lines = ["```mermaid", "erDiagram"]
    tables = schema.tables[:max_tables]
    names = {t.name for t in tables}
    for t in tables:
        lines.append(f"    {_mid(t.name)} {{")
        for c in t.columns[:14]:
            flags = " PK" if c.primary_key else (" FK" if any(fk.column == c.name for fk in t.foreign_keys) else "")
            ctype = re.sub(r"[^A-Za-z0-9_]", "_", (c.type or "unknown").split("(")[0].strip()) or "unknown"
            lines.append(f"        {ctype} {_mid(c.name)}{flags}")
        if len(t.columns) > 14:
            lines.append(f"        more _{len(t.columns) - 14}_columns")
        lines.append("    }")
    for t in tables:
        for fk in t.foreign_keys:
            if fk.ref_table in names:
                lines.append(f'    {_mid(fk.ref_table)} ||--o{{ {_mid(t.name)} : "{fk.column}"')
    lines.append("```")
    return "\n".join(lines)


def _unit_index(types: list[CodeUnit]) -> dict[str, CodeUnit]:
    idx: dict[str, CodeUnit] = {}
    for t in types:
        idx[t.fqn] = t
        for c in t.children:
            if c.kind in (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION):
                idx[c.fqn] = c
    return idx


def sequence_diagram(endpoint: Endpoint, types: list[CodeUnit], graph: KnowledgeGraph, max_depth: int = MAX_SEQUENCE_DEPTH) -> str | None:
    """sequenceDiagram following `calls` edges from the endpoint handler; None when there is nothing to show."""
    calls_by_method: dict[str, list[str]] = defaultdict(list)
    for u, v, k in graph.g.edges(keys=True):
        if k == RelationKind.CALLS.value and not u.startswith("endpoint:"):
            calls_by_method[u].append(v)
    if endpoint.handler not in calls_by_method:
        return None
    lines = [
        "```mermaid",
        "sequenceDiagram",
        "    autonumber",
        "    actor Client",
        f"    Client->>+{_mid(endpoint.handler.rpartition('.')[0])}: {endpoint.http_method} {endpoint.path}",
    ]
    participants: list[str] = [endpoint.handler.rpartition(".")[0]]
    visited: set[str] = set()

    def walk(method_fqn: str, depth: int) -> None:
        if depth > max_depth or method_fqn in visited:
            return
        visited.add(method_fqn)
        caller = method_fqn.rpartition(".")[0]
        for target in sorted(set(calls_by_method.get(method_fqn, []))):
            owner, _, name = target.rpartition(".")
            if owner == caller:
                continue
            if owner not in participants:
                participants.append(owner)
            lines.append(f"    {_mid(caller)}->>+{_mid(owner)}: {name}()")
            walk(target, depth + 1)
            lines.append(f"    {_mid(owner)}-->>-{_mid(caller)}: ")

    walk(endpoint.handler, 1)
    lines.append(f"    {_mid(participants[0])}-->>-Client: response")
    header = [f"    participant {_mid(p)} as {_label(p)}" for p in participants]
    lines[3:3] = header
    lines.append("```")
    return "\n".join(lines) if len(visited) > 1 or any("->>+" in ln for ln in lines[5 + len(header) :]) else None

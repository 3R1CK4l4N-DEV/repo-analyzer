"""MCP server exposing the DevAnalyzer knowledge graph and knowledge base.

Run with: `devanalyzer serve <project>` (stdio) or `devanalyzer serve <project> --sse --port 8765`.
Data is read from `<project>/.devanalyzer/` (run `devanalyzer graph build` and `rules extract` first);
if the graph is missing it is built on the fly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devanalyzer.config import get_settings
from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import ProjectAnalysis
from devanalyzer.graph.knowledge_graph import KnowledgeGraph


class ProjectContext:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.out_dir = self.project / get_settings().output_dir_name
        self._analysis: ProjectAnalysis | None = None
        self._graph: KnowledgeGraph | None = None

    @property
    def analysis(self) -> ProjectAnalysis:
        if self._analysis is None:
            cached = self.out_dir / "analysis.json"
            if cached.is_file():
                self._analysis = ProjectAnalysis.model_validate_json(cached.read_text(encoding="utf-8"))
            else:
                self._analysis = ProjectAnalyzer().run(self.project)
        return self._analysis

    @property
    def graph(self) -> KnowledgeGraph:
        if self._graph is None:
            cached = self.out_dir / "graph.json"
            self._graph = KnowledgeGraph.load(cached) if cached.is_file() else KnowledgeGraph.from_analysis(self.analysis)
        return self._graph

    def kb(self):
        from devanalyzer.kb.store import KnowledgeBase

        return KnowledgeBase(self.out_dir / "kb.sqlite")

    def refresh(self) -> None:
        self._analysis = ProjectAnalyzer().run(self.project)
        self._graph = KnowledgeGraph.from_analysis(self._analysis)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "analysis.json").write_text(self._analysis.model_dump_json(indent=2), encoding="utf-8")
        self._graph.save(self.out_dir)

    def read_source(self, rel_path: str, start: int | None = None, end: int | None = None) -> str:
        target = (self.project / rel_path).resolve()
        if not target.is_relative_to(self.project) or not target.is_file():
            raise FileNotFoundError(rel_path)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        s = max(1, start or 1)
        e = min(len(lines), end or len(lines))
        return "\n".join(f"{i:>5}| {ln}" for i, ln in enumerate(lines[s - 1 : e], start=s))


def create_server(project: Path, name: str = "devanalyzer"):
    from mcp.server.fastmcp import FastMCP

    ctx = ProjectContext(project)
    mcp = FastMCP(
        name,
        instructions=(
            "Tools to explore a software project analyzed by DevAnalyzer: architecture graph, code units, "
            "endpoints, dependencies, AI-inferred business rules and the domain glossary. "
            "Business rules with status 'inferred' have not been reviewed by a human yet."
        ),
    )

    def _dump(obj: Any) -> str:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o))

    @mcp.tool()
    def get_project_summary() -> str:
        """High-level summary: languages, build systems, versions, frameworks, metrics and graph stats."""
        a = ctx.analysis
        return _dump(
            {
                "project": a.project_name,
                "path": a.project_path,
                "languages": [{"language": l.language.value, "confidence": l.confidence, "files": l.files, "loc": l.loc} for l in a.languages],
                "build_systems": [b.value for b in a.build_systems],
                "versions": a.versions,
                "frameworks": [f.model_dump() for f in a.frameworks],
                "metrics": a.metrics.model_dump(exclude={"fan_in", "fan_out"}),
                "endpoints": len(a.endpoints),
                "graph": ctx.graph.stats(),
            }
        )

    @mcp.tool()
    def list_code_units(stereotype: str | None = None, kind: str | None = None, package: str | None = None, limit: int = 200) -> str:
        """List classes/interfaces/enums with optional filters (stereotype: controller|service|repository|entity..., kind: class|interface|enum|record)."""
        out = []
        for n, d in ctx.graph.g.nodes(data=True):
            if d.get("kind") not in ("class", "interface", "enum", "record", "module", "function"):
                continue
            if stereotype and d.get("stereotype") != stereotype:
                continue
            if kind and d.get("kind") != kind:
                continue
            if package and not n.startswith(package):
                continue
            out.append({"fqn": n, "kind": d.get("kind"), "stereotype": d.get("stereotype"), "file": d.get("file"), "line": d.get("line"), "loc": d.get("loc")})
        return _dump(sorted(out, key=lambda x: x["fqn"])[:limit])

    @mcp.tool()
    def get_code_unit(name: str, include_source: bool = False) -> str:
        """Details of a class/method by simple name or FQN: members, relations, endpoints, rules; optionally the source code."""
        matches = ctx.graph.find(name)
        if not matches:
            return _dump({"error": f"no unit matches {name!r}"})
        node = matches[0]
        data = dict(ctx.graph.g.nodes[node])
        unit = next((u for u in _iter_units(ctx.analysis) if u.fqn == node), None)
        result: dict[str, Any] = {"id": node, **data}
        if unit is not None:
            result["methods"] = [
                {
                    "name": m.name,
                    "signature": f"{m.name}({', '.join(p.type or '?' for p in m.parameters)})",
                    "returns": m.type,
                    "line": m.line_start,
                    "annotations": [a.name for a in m.annotations],
                    "calls": m.calls,
                }
                for m in unit.children
                if m.kind.value in ("method", "function")
            ]
            result["fields"] = [
                {"name": f.name, "type": f.type, "annotations": [a.name for a in f.annotations]} for f in unit.children if f.kind.value == "field"
            ]
            result["extends"], result["implements"], result["docstring"] = unit.extends, unit.implements, unit.docstring
        result["outgoing"] = [{"target": t, "kind": k} for t, k in ctx.graph.neighbors(node, direction="out") if k != "contains"]
        result["incoming"] = [{"source": s, "kind": k} for s, k in ctx.graph.neighbors(node, direction="in") if k != "contains"]
        result["endpoints"] = [e.model_dump() for e in ctx.analysis.endpoints if e.handler.rpartition(".")[0] == node or e.handler == node]
        if (ctx.out_dir / "kb.sqlite").is_file():
            with ctx.kb() as kb:
                result["business_rules"] = [r.model_dump(exclude={"evidence"}) for r in kb.rules(unit=node)]
        if include_source and data.get("file"):
            result["source"] = ctx.read_source(data["file"], data.get("line"), data.get("line_end"))
        return _dump(result)

    @mcp.tool()
    def query_graph(node: str, depth: int = 1, relation: str | None = None, direction: str = "both") -> str:
        """Neighbourhood of a node up to `depth` hops. relation filters edge kind (depends_on, calls, extends, implements, imports, exposes, uses_dependency); direction: out|in|both."""
        matches = ctx.graph.find(node)
        if not matches:
            return _dump({"error": f"no node matches {node!r}"})
        start = matches[0]
        sub = ctx.graph.subgraph_for(start, depth)
        edges = [{"source": u, "kind": k, "target": v} for u, v, k in sub.g.edges(keys=True) if not relation or k == relation]
        if depth == 1 and direction in ("out", "in"):
            side = "source" if direction == "out" else "target"
            edges = [e for e in edges if e[side] == start]
        return _dump(
            {
                "start": start,
                "nodes": [{"id": n, **{k: v for k, v in d.items() if k in ("kind", "stereotype", "file", "line")}} for n, d in sub.g.nodes(data=True)],
                "edges": edges,
            }
        )

    @mcp.tool()
    def find_path(source: str, target: str) -> str:
        """Shortest path between two nodes (ignoring edge direction), e.g. from a controller to an entity."""
        a, b = ctx.graph.find(source), ctx.graph.find(target)
        if not a or not b:
            return _dump({"error": "source or target not found"})
        return _dump({"path": ctx.graph.shortest_path(a[0], b[0])})

    @mcp.tool()
    def list_endpoints(http_method: str | None = None, path_contains: str | None = None) -> str:
        """HTTP endpoints exposed by the project with their handlers and source location."""
        eps = [
            e.model_dump()
            for e in ctx.analysis.endpoints
            if (not http_method or e.http_method == http_method.upper()) and (not path_contains or path_contains in e.path)
        ]
        return _dump(eps)

    @mcp.tool()
    def list_dependencies(scope: str | None = None, search: str | None = None) -> str:
        """Declared third-party dependencies per build manifest with versions and scopes."""
        deps = [d.model_dump() for d in ctx.analysis.dependencies if (not scope or d.scope == scope) and (not search or search.lower() in d.name.lower())]
        return _dump(deps)

    @mcp.tool()
    def get_flows(path_contains: str | None = None, writes_only: bool = False) -> str:
        """End-to-end use-case flows per endpoint: layers traversed (controller/service/repository/entity), tables reached and business rules involved."""
        from devanalyzer.graph.flows import build_flows

        rules = []
        if (ctx.out_dir / "kb.sqlite").is_file():
            with ctx.kb() as kb:
                rules = kb.rules()
        flows = build_flows(ctx.analysis, ctx.graph, rules)
        out = [f for f in flows if (not path_contains or path_contains in f.endpoint.path) and (not writes_only or f.writes)]
        return _dump([{"flow": f.title, "handler": f.handler, "writes": f.writes, "layers": f.layers, "tables": f.tables, "rules": f.rule_ids} for f in out])

    @mcp.tool()
    def list_tables(name: str | None = None) -> str:
        """Database tables reconstructed from migrations/schema files, with columns, foreign keys and the ORM entities mapped to them."""
        tables = [t for t in ctx.analysis.db_schema.tables if not name or name.lower() in t.name.lower()]
        return _dump({"sources": ctx.analysis.db_schema.sources, "tables": [t.model_dump() for t in tables]})

    @mcp.tool()
    def get_business_rules(status: str | None = None, category: str | None = None, unit: str | None = None, search: str | None = None) -> str:
        """AI-inferred business rules with evidence. Filters: status (inferred|confirmed|rejected), category, unit (substring of FQN), search (free text)."""
        if not (ctx.out_dir / "kb.sqlite").is_file():
            return _dump({"error": "no knowledge base yet; run `devanalyzer rules extract <project>`"})
        with ctx.kb() as kb:
            rules = kb.rules(status=status, category=category, unit=unit, search=search)
            return _dump({"summary": kb.summary(), "rules": [r.model_dump() for r in rules]})

    @mcp.tool()
    def review_business_rule(rule_id: str, status: str, note: str | None = None) -> str:
        """Record a human decision about a rule: status confirmed|rejected|inferred, optional note. Persists across re-extractions."""
        from devanalyzer.core.models import RuleStatus

        with ctx.kb() as kb:
            ok = kb.set_status(rule_id, RuleStatus(status), note)
        return _dump({"updated": ok, "rule_id": rule_id, "status": status})

    @mcp.tool()
    def search_knowledge(query: str, limit: int = 25) -> str:
        """Free-text search across code units, endpoints, business rules and glossary terms."""
        q = query.lower()
        hits: list[dict[str, Any]] = []
        for n, d in ctx.graph.g.nodes(data=True):
            if q in n.lower() or q in str(d.get("name", "")).lower():
                hits.append({"type": d.get("kind"), "id": n, "file": d.get("file"), "line": d.get("line")})
        if (ctx.out_dir / "kb.sqlite").is_file():
            with ctx.kb() as kb:
                hits += [
                    {"type": "business_rule", "id": r.id, "statement": r.statement, "status": r.status.value, "unit": r.source_unit}
                    for r in kb.rules(search=query)
                ]
                hits += [{"type": "glossary_term", "id": t.term, "definition": t.definition, "units": t.related_units} for t in kb.terms(search=query)]
        return _dump(hits[:limit])

    @mcp.tool()
    def get_glossary(search: str | None = None) -> str:
        """Domain glossary (ubiquitous language) inferred from the code and linked to rules and units."""
        if not (ctx.out_dir / "kb.sqlite").is_file():
            return _dump([])
        with ctx.kb() as kb:
            return _dump([t.model_dump() for t in kb.terms(search=search)])

    @mcp.tool()
    def read_source(file: str, line_start: int | None = None, line_end: int | None = None) -> str:
        """Read a slice of a project source file (path relative to the project root) with line numbers."""
        try:
            return ctx.read_source(file, line_start, line_end)
        except FileNotFoundError:
            return _dump({"error": f"file not found or outside project: {file}"})

    @mcp.tool()
    def refresh_analysis() -> str:
        """Re-run the static analysis and rebuild the graph (does not call the LLM)."""
        ctx.refresh()
        return _dump({"refreshed": True, "graph": ctx.graph.stats()})

    @mcp.resource("devanalyzer://summary")
    def summary_resource() -> str:
        return get_project_summary()

    return mcp


def _iter_units(analysis: ProjectAnalysis):
    stack = list(analysis.units)
    while stack:
        u = stack.pop()
        yield u
        stack.extend(c for c in u.children if c.kind.value in ("class", "interface", "enum", "record", "module"))


def run(project: Path, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
    server = create_server(project)
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.host = host
        server.settings.port = port
        server.run(transport="sse")

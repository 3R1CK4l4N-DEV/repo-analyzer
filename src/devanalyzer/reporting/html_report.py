from __future__ import annotations

import html
from collections import defaultdict
from datetime import UTC, datetime

from devanalyzer.core.models import BusinessRule, CodeUnit, CodeUnitKind, GlossaryTerm, ProjectAnalysis, RelationKind
from devanalyzer.graph.knowledge_graph import KnowledgeGraph

TYPE_KINDS = {CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD, CodeUnitKind.MODULE}
LAYERS = ["controller", "service", "repository", "entity", "other"]
COLORS = {"controller": "#4f86c6", "service": "#5bb974", "repository": "#e0a830", "entity": "#b86bd1", "other": "#9aa0a6"}

CSS = """
body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f7f9;color:#1f2328}
header{background:#1f2328;color:#fff;padding:20px 32px}header h1{margin:0;font-size:22px}header p{margin:4px 0 0;opacity:.8}
main{padding:24px 32px;max-width:1400px;margin:auto}section{background:#fff;border-radius:10px;padding:20px 24px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
h2{margin:0 0 14px;font-size:17px}.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.card{background:#f1f3f5;border-radius:8px;padding:12px}.card b{display:block;font-size:22px}.card span{font-size:12px;color:#57606a}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #e6e8eb;vertical-align:top}th{background:#f1f3f5}
code{font-family:ui-monospace,Consolas,monospace;font-size:12px}.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;color:#fff}
.inferred{background:#e0a830}.confirmed{background:#5bb974}.rejected{background:#d1495b}svg text{font-size:11px;font-family:ui-monospace,Consolas,monospace}
details summary{cursor:pointer;font-weight:600}
"""


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _iter_types(units: list[CodeUnit]):
    for u in units:
        if u.kind in TYPE_KINDS:
            yield u
            yield from _iter_types([c for c in u.children if c.kind in TYPE_KINDS])


def _svg_graph(types: list[CodeUnit], graph: KnowledgeGraph) -> str:
    columns: dict[str, list[CodeUnit]] = defaultdict(list)
    for t in types:
        columns[t.stereotype if t.stereotype in LAYERS else "other"].append(t)
    col_w, row_h, box_w, box_h = 300, 56, 240, 36
    pos: dict[str, tuple[int, int]] = {}
    boxes = []
    max_rows = 1
    for ci, layer in enumerate(LAYERS):
        items = sorted(columns.get(layer, []), key=lambda u: u.name)[:40]
        max_rows = max(max_rows, len(items))
        for ri, unit in enumerate(items):
            x, y = 20 + ci * col_w, 40 + ri * row_h
            pos[unit.fqn] = (x, y)
            boxes.append(
                f'<g><title>{_esc(unit.fqn)}</title><rect x="{x}" y="{y}" rx="6" width="{box_w}" height="{box_h}" fill="{COLORS[layer]}" opacity=".9"/>'
                f'<text x="{x + 10}" y="{y + 22}" fill="#fff">{_esc(unit.name[:30])}</text></g>'
            )
    edges = []
    for u, v, k in graph.g.edges(keys=True):
        if u in pos and v in pos and k in (RelationKind.DEPENDS_ON.value, RelationKind.EXTENDS.value, RelationKind.IMPLEMENTS.value):
            x1, y1 = pos[u][0] + box_w, pos[u][1] + box_h / 2
            x2, y2 = pos[v][0], pos[v][1] + box_h / 2
            if pos[u][0] == pos[v][0]:
                x1 = pos[u][0] + box_w / 2
                x2 = pos[v][0] + box_w / 2
            dash = ' stroke-dasharray="5,4"' if k != RelationKind.DEPENDS_ON.value else ""
            edges.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#7a828a" stroke-width="1.2" marker-end="url(#arrow)"{dash}/>')
    headers = "".join(
        f'<text x="{20 + i * col_w}" y="22" fill="#57606a" font-weight="600">{layer.upper()}</text>' for i, layer in enumerate(LAYERS) if columns.get(layer)
    )
    height = 60 + max_rows * row_h
    return (
        f'<svg width="{20 + len(LAYERS) * col_w}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#7a828a"/></marker></defs>'
        f"{headers}{''.join(edges)}{''.join(boxes)}</svg>"
    )


def render_html(
    analysis: ProjectAnalysis, graph: KnowledgeGraph | None = None, rules: list[BusinessRule] | None = None, glossary: list[GlossaryTerm] | None = None
) -> str:
    graph = graph or KnowledgeGraph.from_analysis(analysis)
    rules = rules or []
    glossary = glossary or []
    types = list(_iter_types(analysis.units))
    stats = graph.stats()
    m = analysis.metrics
    parts = [
        f"<!doctype html><html><head><meta charset='utf-8'><title>DevAnalyzer — {_esc(analysis.project_name)}</title><style>{CSS}</style></head><body>",
        f"<header><h1>{_esc(analysis.project_name)}</h1><p>{_esc(analysis.project_path)} · generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by DevAnalyzer</p></header><main>",
    ]

    cards = [
        ("Files", m.files),
        ("LOC", m.loc),
        ("Types", len(types)),
        ("Endpoints", len(analysis.endpoints)),
        ("Dependencies", len(analysis.dependencies)),
        ("Graph nodes", stats["nodes"]),
        ("Graph edges", stats["edges"]),
        ("Business rules", len(rules)),
        ("Glossary terms", len(glossary)),
    ]
    parts.append("<section><h2>Overview</h2><div class='cards'>" + "".join(f"<div class='card'><b>{v}</b><span>{k}</span></div>" for k, v in cards) + "</div>")
    parts.append("<p>" + " · ".join(f"<b>{_esc(l.language.value)}</b> {l.files} files ({l.confidence:.0%})" for l in analysis.languages) + "</p>")
    parts.append(
        "<table><tr><th>Build</th><th>Manifest</th><th>Versions</th><th>Frameworks</th></tr>"
        + "".join(
            f"<tr><td>{_esc(b.system.value)}</td><td><code>{_esc(b.manifest_path)}</code></td><td>{_esc(', '.join(f'{k}={v}' for k, v in b.versions.items()))}</td><td>{_esc(', '.join(f.name for f in b.frameworks))}</td></tr>"
            for b in analysis.builds
        )
        + "</table></section>"
    )

    parts.append("<section><h2>Architecture</h2><div style='overflow:auto'>" + _svg_graph(types, graph) + "</div>")
    if m.stereotypes:
        parts.append(
            "<p>"
            + " · ".join(
                f"<span class='badge' style='background:{COLORS.get(k, COLORS['other'])}'>{_esc(k)}</span> {v}" for k, v in sorted(m.stereotypes.items())
            )
            + "</p>"
        )
    top = [(n, d) for n, d in graph.top_by_degree(10) if d]
    if top:
        parts.append(
            "<details><summary>Most referenced types</summary><table><tr><th>Type</th><th>In-degree</th></tr>"
            + "".join(f"<tr><td><code>{_esc(n)}</code></td><td>{d}</td></tr>" for n, d in top)
            + "</table></details>"
        )
    parts.append("</section>")

    if analysis.endpoints:
        parts.append(
            "<section><h2>Endpoints</h2><table><tr><th>Method</th><th>Path</th><th>Handler</th><th>Location</th></tr>"
            + "".join(
                f"<tr><td><b>{_esc(e.http_method)}</b></td><td><code>{_esc(e.path)}</code></td><td><code>{_esc(e.handler)}</code></td><td><code>{_esc(e.file)}:{e.line}</code></td></tr>"
                for e in sorted(analysis.endpoints, key=lambda e: (e.path, e.http_method))
            )
            + "</table></section>"
        )

    if rules:
        parts.append(
            "<section><h2>Business rules</h2><table><tr><th>ID</th><th>Status</th><th>Category</th><th>Statement</th><th>Evidence</th><th>Conf.</th></tr>"
        )
        for r in sorted(rules, key=lambda r: (r.status.value, -r.confidence)):
            ev = r.evidence[0] if r.evidence else None
            parts.append(
                f"<tr><td><code>{_esc(r.id)}</code></td><td><span class='badge {r.status.value}'>{r.status.value}</span></td><td>{_esc(r.category)}</td>"
                f"<td>{_esc(r.statement)}<br><small style='color:#57606a'>{_esc(r.rationale or '')}</small></td>"
                f"<td><code>{_esc(ev.file + ':' + str(ev.line_start) + '-' + str(ev.line_end)) if ev else ''}</code></td><td>{r.confidence:.0%}</td></tr>"
            )
        parts.append("</table></section>")
    if glossary:
        parts.append(
            "<section><h2>Glossary</h2><table><tr><th>Term</th><th>Definition</th><th>Units</th></tr>"
            + "".join(
                f"<tr><td><b>{_esc(g.term)}</b></td><td>{_esc(g.definition)}</td><td><code>{_esc(', '.join(u.rsplit('.', 1)[-1] for u in g.related_units))}</code></td></tr>"
                for g in glossary
            )
            + "</table></section>"
        )

    parts.append(
        "<section><h2>Types</h2><table><tr><th>Kind</th><th>FQN</th><th>Stereotype</th><th>Methods</th><th>LOC</th><th>File</th></tr>"
        + "".join(
            f"<tr><td>{_esc(t.kind.value)}</td><td><code>{_esc(t.fqn)}</code></td><td>{_esc(t.stereotype or '')}</td><td>{sum(1 for c in t.children if c.kind in (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION))}</td><td>{t.loc}</td><td><code>{_esc(t.file)}:{t.line_start}</code></td></tr>"
            for t in sorted(types, key=lambda t: t.fqn)
        )
        + "</table></section>"
    )
    if analysis.dependencies:
        parts.append(
            "<section><h2>Dependencies</h2><details><summary>"
            + f"{len(analysis.dependencies)} declared</summary><table><tr><th>Name</th><th>Version</th><th>Scope</th><th>Manifest</th></tr>"
            + "".join(
                f"<tr><td><code>{_esc(d.name)}</code></td><td>{_esc(d.version or 'managed')}</td><td>{_esc(d.scope)}</td><td><code>{_esc(d.source_file)}</code></td></tr>"
                for d in analysis.dependencies
            )
            + "</table></details></section>"
        )
    parts.append("</main></body></html>")
    return "".join(parts)


def render_markdown(
    analysis: ProjectAnalysis, graph: KnowledgeGraph | None = None, rules: list[BusinessRule] | None = None, glossary: list[GlossaryTerm] | None = None
) -> str:
    graph = graph or KnowledgeGraph.from_analysis(analysis)
    rules = rules or []
    types = list(_iter_types(analysis.units))
    lines = [
        f"# {analysis.project_name}",
        "",
        f"- Path: `{analysis.project_path}`",
        f"- Files: {analysis.metrics.files} · LOC: {analysis.metrics.loc} · Types: {len(types)} · Endpoints: {len(analysis.endpoints)}",
        f"- Languages: {', '.join(f'{l.language.value} ({l.files})' for l in analysis.languages)}",
        f"- Build: {', '.join(f'{b.system.value} `{b.manifest_path}`' for b in analysis.builds)}",
        f"- Versions: {', '.join(f'{k}={v}' for k, v in analysis.versions.items()) or '-'}",
        f"- Frameworks: {', '.join(f.name for f in analysis.frameworks) or '-'}",
        "",
    ]
    if analysis.metrics.stereotypes:
        lines += ["## Stereotypes", ""] + [f"- {k}: {v}" for k, v in sorted(analysis.metrics.stereotypes.items())] + [""]
    if types:
        from devanalyzer.obsidian.mermaid import class_diagram

        lines += ["## Architecture", "", class_diagram(types, graph, title=analysis.project_name), ""]
    if analysis.db_schema.tables:
        from devanalyzer.obsidian.mermaid import er_diagram

        lines += ["## Data model", "", er_diagram(analysis.db_schema) or "", ""]
    if analysis.endpoints:
        lines += (
            ["## Endpoints", "", "| Method | Path | Handler |", "|---|---|---|"]
            + [f"| {e.http_method} | `{e.path}` | `{e.handler}` |" for e in sorted(analysis.endpoints, key=lambda e: e.path)]
            + [""]
        )
    if rules:
        lines += ["## Business rules", "", "| ID | Status | Category | Statement | Evidence |", "|---|---|---|---|---|"]
        for r in rules:
            ev = r.evidence[0] if r.evidence else None
            lines.append(
                f"| {r.id} | {r.status.value} | {r.category} | {r.statement} | `{ev.file}:{ev.line_start}-{ev.line_end}` |"
                if ev
                else f"| {r.id} | {r.status.value} | {r.category} | {r.statement} | |"
            )
        lines.append("")
    if glossary:
        lines += ["## Glossary", ""] + [f"- **{g.term}** — {g.definition}" for g in glossary] + [""]
    top = [(n, d) for n, d in graph.top_by_degree(10) if d]
    if top:
        lines += ["## Most referenced types", ""] + [f"- `{n}` ({d})" for n, d in top] + [""]
    lines += (
        ["## Types", "", "| Kind | FQN | Stereotype | File |", "|---|---|---|---|"]
        + [f"| {t.kind.value} | `{t.fqn}` | {t.stereotype or ''} | `{t.file}:{t.line_start}` |" for t in sorted(types, key=lambda t: t.fqn)]
        + [""]
    )
    return "\n".join(lines)

from __future__ import annotations

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from devanalyzer.core.models import BusinessRule, CodeUnit, CodeUnitKind, GlossaryTerm, ProjectAnalysis, RelationKind, RuleGroup
from devanalyzer.core.workspace_lookup import find_workspace, links_for, project_name_in
from devanalyzer.graph.flows import Flow, build_flows, flow_mermaid
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.obsidian.mermaid import class_diagram, er_diagram, sequence_diagram

GENERATED_START = "<!-- devanalyzer:generated:start -->"
GENERATED_END = "<!-- devanalyzer:generated:end -->"
MANUAL_HINT = "\n\n## Notes\n\n<!-- Anything outside the generated block is yours and will be preserved. -->\n"

TYPE_KINDS = {CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD, CodeUnitKind.MODULE}

FOLDERS = {
    "moc": "00 MOC",
    "modules": "10 Modules",
    "classes": "20 Classes",
    "endpoints": "25 Endpoints",
    "rules": "30 Business Rules",
    "glossary": "40 Glossary",
    "decisions": "50 Decisions",
    "domains": "35 Domains",
    "data": "60 Data",
    "flows": "70 Flows",
    "canvas": "canvas",
}


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\[\]#^]', "_", name).strip() or "unnamed"


def _yaml(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_yaml(v) for v in value) + "]"
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return json.dumps(text) if re.search(r'[:#\[\]{}"\n]|^\s|\s$', text) or text == "" else text


def frontmatter(data: dict) -> str:
    return "---\n" + "\n".join(f"{k}: {_yaml(v)}" for k, v in data.items()) + "\n---\n"


class ObsidianVault:
    def __init__(self, vault_dir: str | Path, project_folder: str | None = None):
        self.root = Path(vault_dir)
        self.base = self.root / project_folder if project_folder else self.root
        self.written: list[Path] = []
        self.updated: list[Path] = []
        self._endpoints_by_owner: dict[str, list] = defaultdict(list)
        self._language_by_fqn: dict[str, str] = {}
        self._ensured_dirs: set[Path] = set()
        self._pending: list[tuple[Path, str]] = []
        self._queued: dict[str, Path] = {}
        self._rules_by_id: dict[str, BusinessRule] = {}
        self._types: list[CodeUnit] = []
        self._graph: KnowledgeGraph | None = None
        self._groups_module: dict[str, RuleGroup] = {}
        self._groups_term: dict[str, RuleGroup] = {}
        self._generated_at = datetime.now(UTC).isoformat(timespec="seconds")

    # ------------------------------------------------------------ public
    def generate(
        self,
        analysis: ProjectAnalysis,
        graph: KnowledgeGraph | None = None,
        rules: list[BusinessRule] | None = None,
        glossary: list[GlossaryTerm] | None = None,
        decisions: list[dict] | None = None,
        groups: list[RuleGroup] | None = None,
    ) -> list[Path]:
        self.written = []
        self.updated = []
        self._pending = []
        self._queued = {}
        self._generated_at = datetime.now(UTC).isoformat(timespec="seconds")
        rules = rules or []
        glossary = glossary or []
        self._rules_by_id = {r.id: r for r in rules}
        self._groups_module = {g.key: g for g in (groups or []) if g.kind == "module"}
        self._groups_term = {g.key: g for g in (groups or []) if g.kind == "term"}
        graph = graph or KnowledgeGraph.from_analysis(analysis)
        types = list(self._iter_types(analysis.units))
        self._types = types
        self._graph = graph
        rules_by_unit: dict[str, list[BusinessRule]] = defaultdict(list)
        for r in rules:
            if r.source_unit:
                rules_by_unit[r.source_unit].append(r)
        rules_by_term: dict[str, list[BusinessRule]] = defaultdict(list)
        for r in rules:
            for t in r.domain_terms:
                rules_by_term[t].append(r)
        # Pre-index once; per-note scans over all endpoints/types made generation O(N^2).
        self._endpoints_by_owner = defaultdict(list)
        for ep in analysis.endpoints:
            self._endpoints_by_owner[ep.handler.rpartition(".")[0]].append(ep)
        self._language_by_fqn = {t.fqn: (t.language.value if t.language else "") for t in types}
        self._ensured_dirs: set[Path] = set()

        for unit in types:
            self._write(FOLDERS["classes"], unit.name, self._class_note(unit, analysis, graph, rules_by_unit.get(unit.fqn, [])))
        for ep in analysis.endpoints:
            self._write(FOLDERS["endpoints"], f"{ep.http_method} {ep.path}", self._endpoint_note(ep, analysis))
        for r in rules:
            self._write(FOLDERS["rules"], f"{r.id} {r.statement[:60]}", self._rule_note(r, analysis))
        for g in glossary:
            self._write(FOLDERS["glossary"], g.term, self._term_note(g, rules_by_term.get(g.term, [])))
        for d in decisions or []:
            self._write(FOLDERS["decisions"], f"ADR-{d['id']:03d} {d['title']}", self._decision_note(d))
        for build in analysis.builds:
            self._write(FOLDERS["modules"], build.project_name or build.manifest_path.replace("/", "_"), self._module_note(build, analysis))
        for g in self._groups_module.values():
            self._write(FOLDERS["domains"], g.key, self._domain_note(g))
        for table in analysis.db_schema.tables:
            self._write(FOLDERS["data"], table.name, self._table_note(table, analysis))
        if analysis.db_schema.tables:
            self._write(FOLDERS["moc"], "Data Model", self._moc_data(analysis))
        ws = find_workspace(analysis.project_path)
        if ws and (me := project_name_in(ws, analysis.project_path)):
            self._write(FOLDERS["moc"], "Related Services", self._related_services(ws, me))
        flows = build_flows(analysis, graph, rules)
        for fl in flows:
            self._write(FOLDERS["flows"], fl.title, self._flow_note(fl))
        if flows:
            self._write(FOLDERS["moc"], "Use Case Flows", self._moc_flows(flows))

        self._write(FOLDERS["moc"], "Architecture", self._moc_architecture(analysis, types, graph))
        self._write(FOLDERS["moc"], "Business Rules", self._moc_rules(rules, groups or []))
        self._write(FOLDERS["moc"], "Glossary", self._moc_glossary(glossary))
        self._write("", f"{analysis.project_name} - Home", self._home(analysis, types, rules, glossary))
        self._write_canvas(analysis, types, graph)
        self._flush()
        return self.written

    # ------------------------------------------------------------ notes
    def _class_note(self, unit: CodeUnit, analysis: ProjectAnalysis, graph: KnowledgeGraph, rules: list[BusinessRule]) -> tuple[dict, str]:
        fm = {
            "type": unit.kind.value,
            "stereotype": unit.stereotype or "",
            "fqn": unit.fqn,
            "package": unit.package or "",
            "file": unit.file,
            "lines": f"{unit.line_start}-{unit.line_end}",
            "language": unit.language.value if unit.language else "",
            "tags": ["devanalyzer", unit.kind.value] + ([f"stereotype/{unit.stereotype}"] if unit.stereotype else []),
        }
        lines = [f"# {unit.name}", ""]
        if unit.docstring:
            lines += ["> " + unit.docstring.replace("\n", "\n> "), ""]
        lines += [
            f"**Kind:** {unit.kind.value}" + (f" · **Stereotype:** {unit.stereotype}" if unit.stereotype else ""),
            f"**Source:** `{unit.file}:{unit.line_start}`",
            "",
        ]
        if unit.extends or unit.implements:
            lines.append("## Hierarchy")
            lines += [f"- extends {self._link_fqn(t)}" for t in unit.extends]
            lines += [f"- implements {self._link_fqn(t)}" for t in unit.implements]
            lines.append("")

        out_rel = defaultdict(set)
        in_rel = defaultdict(set)
        if unit.fqn in graph.g:
            for target, kind in graph.neighbors(unit.fqn, direction="out"):
                if kind in (RelationKind.DEPENDS_ON.value, RelationKind.IMPORTS.value, RelationKind.EXPOSES.value):
                    out_rel[kind].add(target)
            for source, kind in graph.neighbors(unit.fqn, direction="in"):
                if kind in (RelationKind.DEPENDS_ON.value, RelationKind.IMPORTS.value):
                    in_rel[kind].add(source)
        if out_rel:
            lines.append("## Depends on")
            for kind, targets in sorted(out_rel.items()):
                for t in sorted(targets):
                    lines.append(f"- {kind}: {self._link_node(t)}")
            lines.append("")
        if in_rel:
            lines.append("## Used by")
            for kind, sources in sorted(in_rel.items()):
                for s in sorted(sources):
                    lines.append(f"- {kind}: {self._link_node(s)}")
            lines.append("")

        methods = [c for c in unit.children if c.kind in (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION)]
        if methods:
            lines += ["## Methods", "", "| Method | Returns | Line | Annotations |", "|---|---|---|---|"]
            for m in methods:
                sig = f"{m.name}({', '.join(p.type or '?' for p in m.parameters)})"
                lines.append(f"| `{sig}` | `{m.type or ''}` | {m.line_start} | {', '.join('@' + a.name for a in m.annotations)} |")
            lines.append("")
        fields = [c for c in unit.children if c.kind == CodeUnitKind.FIELD]
        if fields:
            lines += (
                ["## Fields", ""]
                + [f"- `{f.type or ''} {f.name}`" + (f" {' '.join('@' + a.name for a in f.annotations)}" if f.annotations else "") for f in fields]
                + [""]
            )
        eps = self._endpoints_by_owner.get(unit.fqn, [])
        if eps:
            lines += (
                ["## Endpoints", ""]
                + [f"- [[{safe_name(f'{e.http_method} {e.path}')}|{e.http_method} {e.path}]] → `{e.handler.rsplit('.', 1)[-1]}`" for e in eps]
                + [""]
            )
        if rules:
            lines += ["## Business rules", ""] + [f"- {self._link_rule(r)} ({r.status.value}, {r.confidence:.0%})" for r in rules] + [""]
        tables = [t for t in analysis.db_schema.tables if unit.fqn in t.mapped_entities]
        if tables:
            lines += ["## Persistence", ""] + [f"- Table [[{safe_name(t.name)}]] ({len(t.columns)} columns)" for t in tables] + [""]
        nested = [c for c in unit.children if c.kind in TYPE_KINDS]
        if nested:
            lines += ["## Nested types", ""] + [f"- [[{safe_name(c.name)}]]" for c in nested] + [""]
        return fm, "\n".join(lines)

    def _endpoint_note(self, ep, analysis: ProjectAnalysis) -> tuple[dict, str]:
        owner = ep.handler.rpartition(".")[0]
        fm = {
            "type": "endpoint",
            "http_method": ep.http_method,
            "path": ep.path,
            "handler": ep.handler,
            "file": ep.file,
            "line": ep.line,
            "framework": ep.framework or "",
            "tags": ["devanalyzer", "endpoint", f"http/{ep.http_method.lower()}"],
        }
        body = [
            f"# {ep.http_method} {ep.path}",
            "",
            f"**Handler:** {self._link_fqn(owner)} → `{ep.handler.rsplit('.', 1)[-1]}`",
            f"**Source:** `{ep.file}:{ep.line}`",
            f"**Framework:** {ep.framework or '-'}",
            "",
        ]
        if diagram := sequence_diagram(ep, self._types, self._graph):
            body += ["## Flow", "", diagram, ""]
        return fm, "\n".join(body)

    def _rule_note(self, r: BusinessRule, analysis: ProjectAnalysis) -> tuple[dict, str]:
        fm = {
            "type": "business-rule",
            "rule_id": r.id,
            "status": r.status.value,
            "statement": r.statement,
            "review_note": r.review_note or "",
            "category": r.category,
            "confidence": round(r.confidence, 2),
            "source_unit": r.source_unit or "",
            "domain_terms": r.domain_terms,
            "model": r.model or "",
            "tags": ["devanalyzer", "business-rule", f"status/{r.status.value}", f"category/{r.category}"],
        }
        lines = [
            f"# {r.id}",
            "",
            f"> [!{'tip' if r.status.value == 'confirmed' else 'warning' if r.status.value == 'rejected' else 'question'}] {r.status.value.title()}",
            f"> {r.statement}",
            "",
        ]
        if r.rationale:
            lines += ["## Rationale", "", r.rationale, ""]
        if r.domain_terms:
            lines += ["## Domain terms", ""] + [f"- [[{safe_name(t)}]]" for t in r.domain_terms] + [""]
        lines += ["## Evidence", ""]
        for e in r.evidence:
            lines.append(f"- {self._link_fqn(e.unit) if e.unit else ''} `{e.file}:{e.line_start}-{e.line_end}`")
            if e.snippet:
                lang = self._language_by_fqn.get(r.source_unit or "", "")
                lines += ["", f"```{lang}", e.snippet, "```", ""]
        lines += [
            "",
            f"*Inferred by `{r.model or 'llm'}` with confidence {r.confidence:.0%}. Review with* `devanalyzer rules review <project> {r.id} --status confirmed|rejected`",
        ]
        return fm, "\n".join(lines)

    def _term_note(self, g: GlossaryTerm, rules: list[BusinessRule]) -> tuple[dict, str]:
        fm = {"type": "glossary-term", "term": g.term, "aliases": g.aliases, "tags": ["devanalyzer", "glossary"]}
        lines = [f"# {g.term}", "", g.definition or "_No definition inferred yet._", ""]
        group = self._groups_term.get(g.term)
        if group and (group.summary or group.cross_cutting):
            lines += ["## Business summary", ""] + ([group.summary, ""] if group.summary else [])
            lines += [f"- **{c.statement}** ({', '.join(self._link_rule_short(i) for i in c.covers)})" for c in group.cross_cutting] + [""]
        if g.related_units:
            lines += ["## Appears in", ""] + [f"- {self._link_fqn(u)}" for u in g.related_units] + [""]
        if rules:
            lines += ["## Rules", ""] + [f"- {self._link_rule(r)}" for r in rules] + [""]
        return fm, "\n".join(lines)

    def _domain_note(self, g: RuleGroup) -> tuple[dict, str]:
        fm = {"type": "domain", "module": g.key, "rules": len(g.rule_ids), "tags": ["devanalyzer", "domain"]}
        lines = [f"# {g.key}", "", g.summary or "_No summary._", ""]
        module_types = [t for t in self._types if t.fqn.replace("/", ".").startswith(g.key + ".")]
        if module_types:
            lines += ["## Class diagram", "", class_diagram(module_types, self._graph, title=g.key), ""]
        if g.cross_cutting:
            lines += (
                ["## Cross-cutting rules", ""]
                + [f"- **{c.statement}** `{c.category}` — {', '.join(self._link_rule_short(i) for i in c.covers)}" for c in g.cross_cutting]
                + [""]
            )
        if g.duplicates:
            lines += (
                ["## Merged duplicates", ""]
                + [f"- {self._link_rule_short(d[0])} ⇐ {', '.join(self._link_rule_short(o) for o in d[1:])}" for d in g.duplicates]
                + [""]
            )
        lines += ["## Rules in this module", ""] + [f"- {self._link_rule(self._rules_by_id[i])}" for i in g.rule_ids if i in self._rules_by_id] + [""]
        return fm, "\n".join(lines)

    def _table_note(self, table, analysis: ProjectAnalysis) -> tuple[dict, str]:
        fm = {
            "type": "table",
            "table": table.name,
            "tool": table.tool,
            "file": table.source_file,
            "columns": len(table.columns),
            "tags": ["devanalyzer", "table"],
        }
        lines = [f"# {table.name}", "", f"**Source:** `{table.source_file}:{table.line}` ({table.tool})", ""]
        if table.mapped_entities:
            lines += ["**Persisted entities:** " + ", ".join(self._link_fqn(e) if not e.startswith("prisma.") else f"`{e}`" for e in table.mapped_entities), ""]
        lines += ["## Columns", "", "| Column | Type | Nullable | Key |", "|---|---|---|---|"]
        fk_cols = {fk.column: fk for fk in table.foreign_keys}
        for c in table.columns:
            key = "PK" if c.primary_key else (f"FK → [[{safe_name(fk_cols[c.name].ref_table)}]]" if c.name in fk_cols else "")
            lines.append(f"| `{c.name}` | {c.type or ''} | {'yes' if c.nullable else 'no'} | {key} |")
        lines.append("")
        incoming = [(t, fk) for t in analysis.db_schema.tables for fk in t.foreign_keys if fk.ref_table == table.name]
        if incoming:
            lines += ["## Referenced by", ""] + [f"- [[{safe_name(t.name)}]] via `{fk.column}`" for t, fk in incoming] + [""]
        return fm, "\n".join(lines)

    def _flow_note(self, fl: Flow) -> tuple[dict, str]:
        fm = {
            "type": "flow",
            "http_method": fl.endpoint.http_method,
            "path": fl.endpoint.path,
            "handler": fl.handler,
            "writes": fl.writes,
            "tables": fl.tables,
            "rules": len(fl.rule_ids),
            "tags": ["devanalyzer", "flow", "writes" if fl.writes else "reads"],
        }
        lines = [
            f"# {fl.title}",
            "",
            f"**Entry:** [[{safe_name(fl.title)}|{fl.title}]] → {self._link_fqn(fl.handler.rpartition('.')[0])}.`{fl.handler.rsplit('.', 1)[-1]}`",
            f"**Kind:** {'write (mutates state)' if fl.writes else 'read'}",
            "",
            flow_mermaid(fl),
            "",
        ]
        for layer in ("controller", "service", "component", "repository", "entity", "other"):
            if layer in fl.layers:
                lines += [f"## {layer.title()}", ""] + [f"- {self._link_fqn(f)}" for f in fl.layers[layer]] + [""]
        if fl.tables:
            lines += ["## Tables", ""] + [f"- [[{safe_name(t)}]]" for t in fl.tables] + [""]
        if fl.rule_ids:
            lines += (
                ["## Business rules along this flow", ""] + [f"- {self._link_rule(self._rules_by_id[i])}" for i in fl.rule_ids if i in self._rules_by_id] + [""]
            )
        return fm, "\n".join(lines)

    def _related_services(self, ws, me: str) -> tuple[dict, str]:
        from devanalyzer.core.workspace import workspace_mermaid

        fm = {"type": "moc", "workspace": ws.name, "service": me, "tags": ["devanalyzer", "moc", "workspace"]}
        links = links_for(ws, me)
        lines = [f"# Related Services — {me}", "", f"Workspace **{ws.name}** ({len(ws.projects)} services)", "", workspace_mermaid(ws), ""]
        for title, items, fmt in (
            (
                "Calls / publishes to",
                links["outgoing"],
                lambda x: f"- **{x.target}** · {x.kind} `{x.label}`" + (f" — `{x.evidence_file}:{x.evidence_line}`" if x.evidence_file else ""),
            ),
            (
                "Called / consumed by",
                links["incoming"],
                lambda x: f"- **{x.source}** · {x.kind} `{x.label}`" + (f" — `{x.evidence_file}:{x.evidence_line}`" if x.evidence_file else ""),
            ),
        ):
            lines += [f"## {title}", ""] + ([fmt(x) for x in items] or ["_none detected_"]) + [""]
        return fm, "\n".join(lines)

    def _moc_flows(self, flows: list[Flow]) -> tuple[dict, str]:
        fm = {"type": "moc", "tags": ["devanalyzer", "moc"]}
        lines = ["# Use Case Flows", "", "| Flow | Kind | Services | Repositories | Tables | Rules |", "|---|---|---|---|---|---|"]
        for fl in flows:
            lines.append(
                f"| [[{safe_name(fl.title)}\\|{fl.title}]] | {'write' if fl.writes else 'read'} | "
                f"{', '.join(x.rsplit('.', 1)[-1] for x in fl.layers.get('service', []))} | "
                f"{', '.join(x.rsplit('.', 1)[-1] for x in fl.layers.get('repository', []))} | {', '.join(fl.tables)} | {len(fl.rule_ids)} |"
            )
        lines += ["", "```dataview", 'TABLE http_method, writes, tables, rules FROM "' + self._rel(FOLDERS["flows"]) + '" SORT path', "```", ""]
        return fm, "\n".join(lines)

    def _moc_data(self, analysis: ProjectAnalysis) -> tuple[dict, str]:
        fm = {"type": "moc", "tags": ["devanalyzer", "moc"]}
        lines = ["# Data Model", "", f"{len(analysis.db_schema.tables)} tables from: " + ", ".join(f"`{s}`" for s in analysis.db_schema.sources[:10]), ""]
        if diagram := er_diagram(analysis.db_schema):
            lines += ["## ER diagram", "", diagram, ""]
        lines += (
            ["## Tables", ""]
            + [
                f"- [[{safe_name(t.name)}]] ({len(t.columns)} cols)"
                + (" ← " + ", ".join(self._link_fqn(e) for e in t.mapped_entities if not e.startswith("prisma.")) if t.mapped_entities else "")
                for t in analysis.db_schema.tables
            ]
            + [""]
        )
        return fm, "\n".join(lines)

    def _link_rule_short(self, rule_id: str) -> str:
        r = self._rules_by_id.get(rule_id)
        return f"[[{safe_name(f'{r.id} {r.statement[:60]}')}|{r.id}]]" if r else rule_id

    def _decision_note(self, d: dict) -> tuple[dict, str]:
        fm = {"type": "decision", "created_at": d.get("created_at", ""), "tags": ["devanalyzer", "adr"]}
        return fm, f"# {d['title']}\n\n{d.get('body') or ''}\n"

    def _module_note(self, build, analysis: ProjectAnalysis) -> tuple[dict, str]:
        fm = {
            "type": "module",
            "build_system": build.system.value,
            "manifest": build.manifest_path,
            "tags": ["devanalyzer", "module"],
            **{f"version_{k}": v for k, v in build.versions.items()},
        }
        lines = [
            f"# {build.project_name or build.manifest_path}",
            "",
            f"**Build system:** {build.system.value}  ",
            f"**Manifest:** `{build.manifest_path}`",
            "",
        ]
        if build.versions:
            lines += ["## Versions", ""] + [f"- {k}: `{v}`" for k, v in build.versions.items()] + [""]
        if build.frameworks:
            lines += ["## Frameworks", ""] + [f"- {f.name} `{f.version or 'managed'}`" for f in build.frameworks] + [""]
        if build.dependencies:
            lines += (
                ["## Dependencies", "", "| Name | Version | Scope |", "|---|---|---|"]
                + [f"| `{d.name}` | {d.version or 'managed'} | {d.scope} |" for d in build.dependencies]
                + [""]
            )
        return fm, "\n".join(lines)

    def _moc_architecture(self, analysis: ProjectAnalysis, types: list[CodeUnit], graph: KnowledgeGraph) -> tuple[dict, str]:
        fm = {"type": "moc", "tags": ["devanalyzer", "moc"]}
        lines = [f"# Architecture — {analysis.project_name}", ""]
        by_stereotype: dict[str, list[CodeUnit]] = defaultdict(list)
        for t in types:
            by_stereotype[t.stereotype or "other"].append(t)
        for stereotype in sorted(
            by_stereotype,
            key=lambda s: ("controller", "service", "repository", "entity").index(s) if s in ("controller", "service", "repository", "entity") else 99,
        ):
            lines += (
                [f"## {stereotype.title()} ({len(by_stereotype[stereotype])})", ""]
                + [f"- [[{safe_name(t.name)}]] `{t.package or ''}`" for t in sorted(by_stereotype[stereotype], key=lambda u: u.name)]
                + [""]
            )
        top = graph.top_by_degree(10)
        if top:
            lines += ["## Most referenced types", ""] + [f"- {self._link_node(n)} ({d})" for n, d in top if d] + [""]
        lines += [
            "## Dataview",
            "",
            "```dataview",
            'TABLE stereotype, package, file FROM "' + self._rel(FOLDERS["classes"]) + '" SORT stereotype, file',
            "```",
            "",
        ]
        lines += ["## Class diagram", "", class_diagram(types, graph, title=analysis.project_name), ""]
        lines += ["![[architecture.canvas]]", ""]
        return fm, "\n".join(lines)

    def _moc_rules(self, rules: list[BusinessRule], groups: list[RuleGroup]) -> tuple[dict, str]:
        fm = {"type": "moc", "tags": ["devanalyzer", "moc"]}
        lines = ["# Business Rules", ""]
        cross = [(g, c) for g in groups if g.kind == "module" for c in g.cross_cutting]
        if cross:
            lines += ["## Cross-cutting rules", ""] + [f"- **{c.statement}** — [[{safe_name(g.key)}|{g.key}]]" for g, c in cross] + [""]
        if any(g.kind == "module" for g in groups):
            lines += ["## Domains", ""] + [f"- [[{safe_name(g.key)}|{g.key}]] ({len(g.rule_ids)} rules)" for g in groups if g.kind == "module"] + [""]
        by_cat: dict[str, list[BusinessRule]] = defaultdict(list)
        for r in rules:
            by_cat[r.category].append(r)
        for cat in sorted(by_cat):
            lines += (
                [f"## {cat.replace('_', ' ').title()} ({len(by_cat[cat])})", ""]
                + [f"- {self._link_rule(r)} — {r.status.value} · {self._link_fqn(r.source_unit) if r.source_unit else ''}" for r in by_cat[cat]]
                + [""]
            )
        lines += [
            "## Pending review",
            "",
            "```dataview",
            'TABLE status, category, confidence, source_unit FROM "' + self._rel(FOLDERS["rules"]) + '" WHERE status = "inferred" SORT confidence DESC',
            "```",
            "",
        ]
        return fm, "\n".join(lines)

    def _moc_glossary(self, glossary: list[GlossaryTerm]) -> tuple[dict, str]:
        fm = {"type": "moc", "tags": ["devanalyzer", "moc"]}
        lines = ["# Glossary", ""] + [f"- [[{safe_name(g.term)}]] — {g.definition[:120]}" for g in glossary] + [""]
        return fm, "\n".join(lines)

    def _home(self, analysis: ProjectAnalysis, types, rules, glossary) -> tuple[dict, str]:
        fm = {"type": "project", "project": analysis.project_name, "path": analysis.project_path, "tags": ["devanalyzer", "project"]}
        lines = [
            f"# {analysis.project_name}",
            "",
            f"- Files: {analysis.metrics.files} · LOC: {analysis.metrics.loc}",
            f"- Languages: {', '.join(l.language.value for l in analysis.languages)}",
            f"- Build: {', '.join(f'{b.system.value} ({b.manifest_path})' for b in analysis.builds)}",
            f"- Versions: {', '.join(f'{k}={v}' for k, v in analysis.versions.items()) or '-'}",
            f"- Frameworks: {', '.join(f.name for f in analysis.frameworks) or '-'}",
            "",
            "## Maps of content",
            "",
            "- [[Architecture]]",
            "- [[Business Rules]]",
            "- [[Glossary]]",
            *(["- [[Data Model]]"] if analysis.db_schema.tables else []),
            *(["- [[Use Case Flows]]"] if analysis.endpoints else []),
            *(["- [[Related Services]]"] if find_workspace(analysis.project_path) else []),
            "",
            "## Stats",
            "",
            f"- Types: {len(types)} · Endpoints: {len(analysis.endpoints)} · Rules: {len(rules)} · Terms: {len(glossary)}",
            "",
        ]
        return fm, "\n".join(lines)

    # ------------------------------------------------------------ canvas
    def _write_canvas(self, analysis: ProjectAnalysis, types: list[CodeUnit], graph: KnowledgeGraph) -> None:
        order = ["controller", "service", "repository", "entity"]
        columns: dict[str, list[CodeUnit]] = defaultdict(list)
        for t in types:
            columns[t.stereotype if t.stereotype in order else "other"].append(t)
        nodes, edges = [], []
        pos: dict[str, tuple[int, int]] = {}
        for col, key in enumerate(order + ["other"]):
            for row, unit in enumerate(sorted(columns.get(key, []), key=lambda u: u.name)):
                x, y = col * 420, row * 140
                pos[unit.fqn] = (x, y)
                nodes.append(
                    {
                        "id": unit.fqn,
                        "type": "file",
                        "file": self._rel(f"{FOLDERS['classes']}/{safe_name(unit.name)}.md"),
                        "x": x,
                        "y": y,
                        "width": 360,
                        "height": 100,
                        "color": {"controller": "4", "service": "3", "repository": "5", "entity": "6"}.get(key, "1"),
                    }
                )
        for u, v, k in graph.g.edges(keys=True):
            if u in pos and v in pos and k in (RelationKind.DEPENDS_ON.value, RelationKind.EXTENDS.value, RelationKind.IMPLEMENTS.value):
                edges.append({"id": f"{u}->{v}:{k}", "fromNode": u, "toNode": v, "fromSide": "right", "toSide": "left", "label": k})
        canvas_dir = self.base / FOLDERS["canvas"]
        canvas_dir.mkdir(parents=True, exist_ok=True)
        path = canvas_dir / "architecture.canvas"
        content = json.dumps({"nodes": nodes, "edges": edges}, indent=2)
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            self.updated.append(path)
        self.written.append(path)

    # ------------------------------------------------------------ helpers
    def _rel(self, folder_or_file: str) -> str:
        return (self.base.relative_to(self.root) / folder_or_file).as_posix() if self.base != self.root else folder_or_file

    @staticmethod
    def _iter_types(units: list[CodeUnit]):
        for u in units:
            if u.kind in TYPE_KINDS:
                yield u
                yield from ObsidianVault._iter_types([c for c in u.children if c.kind in TYPE_KINDS])

    @staticmethod
    def _link_fqn(fqn: str | None) -> str:
        if not fqn:
            return ""
        return f"[[{safe_name(fqn.rsplit('.', 1)[-1])}|{fqn.rsplit('.', 1)[-1]}]]"

    def _link_node(self, node_id: str) -> str:
        if node_id.startswith("endpoint:"):
            return f"[[{safe_name(node_id[9:])}|{node_id[9:]}]]"
        if ":" in node_id:
            return f"`{node_id}`"
        return self._link_fqn(node_id)

    @staticmethod
    def _link_rule(r: BusinessRule) -> str:
        return f"[[{safe_name(f'{r.id} {r.statement[:60]}')}|{r.id}]] {r.statement}"

    def _write(self, folder: str, title: str, content: tuple[dict, str]) -> Path:
        """Queue a note; actual disk I/O happens in parallel in `_flush` (Windows file I/O dominates otherwise)."""
        fm, body = content
        fm = {**fm, "generated_by": "devanalyzer", "generated_at": self._generated_at}
        folder_path = self.base / folder if folder else self.base
        if folder_path not in self._ensured_dirs:
            folder_path.mkdir(parents=True, exist_ok=True)
            self._ensured_dirs.add(folder_path)
        path = folder_path / f"{safe_name(title)}.md"
        # Titles that only differ in case are one file on Windows/macOS; the first note wins deterministically
        # instead of two threads racing over the same path in `_flush`.
        key = str(path).casefold()
        if key in self._queued:
            return self._queued[key]
        self._queued[key] = path
        self._pending.append((path, frontmatter(fm) + f"{GENERATED_START}\n{body.rstrip()}\n{GENERATED_END}"))
        self.written.append(path)
        return path

    def _flush(self) -> None:
        def write_one(item: tuple[Path, str]) -> Path | None:
            path, generated = item
            try:
                existing = path.read_text(encoding="utf-8")
                before, after = self._manual_sections(existing)
            except FileNotFoundError:
                existing, before, after = "", "", MANUAL_HINT
            header, marker, body = generated.partition(GENERATED_START)
            content = header + before + marker + body + after
            # Regeneration time alone must not dirty every note in an Obsidian vault.
            if re.sub(r"(?m)^generated_at:.*\n", "", content, count=1) == re.sub(r"(?m)^generated_at:.*\n", "", existing, count=1):
                return None
            path.write_text(content, encoding="utf-8")
            return path

        pending, self._pending = self._pending, []
        if len(pending) < 64:
            self.updated.extend(p for item in pending if (p := write_one(item)) is not None)
            return
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.updated.extend(p for p in pool.map(write_one, pending) if p is not None)

    @staticmethod
    def _manual_sections(existing: str) -> tuple[str, str]:
        stripped = re.sub(r"^---\n.*?\n---\n", "", existing, count=1, flags=re.S)
        start, end = stripped.find(GENERATED_START), stripped.find(GENERATED_END)
        if start == -1 or end < start:
            # Legacy/unknown note: keep everything (minus frontmatter) as manual content.
            return "", "\n\n" + stripped.strip() + "\n" if stripped.strip() else MANUAL_HINT
        return stripped[:start], stripped[end + len(GENERATED_END) :] or MANUAL_HINT

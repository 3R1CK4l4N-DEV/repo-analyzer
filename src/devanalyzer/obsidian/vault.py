from __future__ import annotations

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from devanalyzer.core.models import BusinessRule, CodeUnit, CodeUnitKind, GlossaryTerm, ProjectAnalysis, RelationKind
from devanalyzer.graph.knowledge_graph import KnowledgeGraph

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
        self._endpoints_by_owner: dict[str, list] = defaultdict(list)
        self._language_by_fqn: dict[str, str] = {}
        self._ensured_dirs: set[Path] = set()
        self._pending: list[tuple[Path, str]] = []
        self._generated_at = datetime.now(UTC).isoformat(timespec="seconds")

    # ------------------------------------------------------------ public
    def generate(
        self,
        analysis: ProjectAnalysis,
        graph: KnowledgeGraph | None = None,
        rules: list[BusinessRule] | None = None,
        glossary: list[GlossaryTerm] | None = None,
        decisions: list[dict] | None = None,
    ) -> list[Path]:
        rules = rules or []
        glossary = glossary or []
        graph = graph or KnowledgeGraph.from_analysis(analysis)
        types = list(self._iter_types(analysis.units))
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

        self._write(FOLDERS["moc"], "Architecture", self._moc_architecture(analysis, types, graph))
        self._write(FOLDERS["moc"], "Business Rules", self._moc_rules(rules))
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
        return fm, "\n".join(body)

    def _rule_note(self, r: BusinessRule, analysis: ProjectAnalysis) -> tuple[dict, str]:
        fm = {
            "type": "business-rule",
            "rule_id": r.id,
            "status": r.status.value,
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
        if g.related_units:
            lines += ["## Appears in", ""] + [f"- {self._link_fqn(u)}" for u in g.related_units] + [""]
        if rules:
            lines += ["## Rules", ""] + [f"- {self._link_rule(r)}" for r in rules] + [""]
        return fm, "\n".join(lines)

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
        lines += ["![[architecture.canvas]]", ""]
        return fm, "\n".join(lines)

    def _moc_rules(self, rules: list[BusinessRule]) -> tuple[dict, str]:
        fm = {"type": "moc", "tags": ["devanalyzer", "moc"]}
        lines = ["# Business Rules", ""]
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
        path.write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=2), encoding="utf-8")
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
        self._pending.append((path, frontmatter(fm) + f"{GENERATED_START}\n{body.rstrip()}\n{GENERATED_END}"))
        self.written.append(path)
        return path

    def _flush(self) -> None:
        def write_one(item: tuple[Path, str]) -> None:
            path, generated = item
            try:
                manual = self._manual_part(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                manual = MANUAL_HINT
            path.write_text(generated + manual, encoding="utf-8")

        pending, self._pending = self._pending, []
        if len(pending) < 64:
            for item in pending:
                write_one(item)
            return
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write_one, pending))

    @staticmethod
    def _manual_part(existing: str) -> str:
        end = existing.find(GENERATED_END)
        if end == -1:
            # Legacy/unknown note: keep everything (minus frontmatter) as manual content.
            stripped = re.sub(r"^---\n.*?\n---\n", "", existing, count=1, flags=re.S)
            return "\n\n" + stripped.strip() + "\n" if stripped.strip() else MANUAL_HINT
        return existing[end + len(GENERATED_END) :] or MANUAL_HINT

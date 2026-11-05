from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import networkx as nx

from devanalyzer.core.models import CodeUnit, CodeUnitKind, ProjectAnalysis, RelationKind

TYPE_KINDS = {CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD}


class KnowledgeGraph:
    """Typed project graph on top of a NetworkX MultiDiGraph.

    Node ids: project:<name>, module:<manifest>, package:<name>, <fqn> for code units,
    endpoint:<METHOD path>, dep:<name>, framework:<name>.
    """

    def __init__(self, graph: nx.MultiDiGraph | None = None):
        self.g = graph or nx.MultiDiGraph()

    # ------------------------------------------------------------ build
    @classmethod
    def from_analysis(cls, analysis: ProjectAnalysis) -> KnowledgeGraph:
        kg = cls()
        g = kg.g
        project_id = f"project:{analysis.project_name}"
        g.add_node(project_id, kind="project", name=analysis.project_name, path=analysis.project_path)

        for build in analysis.builds:
            module_id = f"module:{build.manifest_path}"
            g.add_node(
                module_id,
                kind="module",
                name=build.project_name or build.manifest_path,
                build_system=build.system.value,
                **{f"version_{k}": v for k, v in build.versions.items()},
            )
            kg._edge(project_id, module_id, RelationKind.CONTAINS)
            for fw in build.frameworks:
                fw_id = f"framework:{fw.name}"
                g.add_node(fw_id, kind="framework", name=fw.name, version=fw.version)
                kg._edge(module_id, fw_id, RelationKind.USES_DEPENDENCY)
            for dep in build.dependencies:
                dep_id = f"dep:{dep.name}"
                g.add_node(dep_id, kind="dependency", name=dep.name, version=dep.version, scope=dep.scope)
                kg._edge(module_id, dep_id, RelationKind.USES_DEPENDENCY, scope=dep.scope)

        for lang in analysis.languages:
            for unit in lang.units:
                kg._add_unit(unit, parent_id=kg._package_node(unit.package, project_id, lang.language.value))
            for ep in lang.endpoints:
                ep_id = f"endpoint:{ep.http_method} {ep.path}"
                g.add_node(
                    ep_id,
                    kind="endpoint",
                    name=f"{ep.http_method} {ep.path}",
                    http_method=ep.http_method,
                    path=ep.path,
                    file=ep.file,
                    line=ep.line,
                    framework=ep.framework,
                )
                owner = ep.handler.rpartition(".")[0]
                if owner in g:
                    kg._edge(owner, ep_id, RelationKind.EXPOSES)
                if ep.handler in g:
                    kg._edge(ep_id, ep.handler, RelationKind.CALLS)
            for rel in lang.relations:
                if rel.kind == RelationKind.CONTAINS:
                    continue
                if rel.source in g and rel.target in g:
                    kg._edge(rel.source, rel.target, rel.kind)

        for table in analysis.db_schema.tables:
            tid = f"table:{table.name}"
            g.add_node(
                tid,
                kind="table",
                name=table.name,
                file=table.source_file,
                line=table.line,
                tool=table.tool,
                columns=len(table.columns),
                primary_key=",".join(c.name for c in table.columns if c.primary_key),
            )
            kg._edge(project_id, tid, RelationKind.CONTAINS)
            for entity in table.mapped_entities:
                if entity in g:
                    kg._edge(entity, tid, RelationKind.MAPS_TO)
        for table in analysis.db_schema.tables:
            for fk in table.foreign_keys:
                target = f"table:{fk.ref_table}"
                if target in g:
                    kg._edge(f"table:{table.name}", target, RelationKind.REFERENCES, column=fk.column)
        return kg

    def _package_node(self, package: str | None, project_id: str, language: str) -> str:
        if not package:
            return project_id
        pkg_id = f"package:{package}"
        if pkg_id not in self.g:
            self.g.add_node(pkg_id, kind="package", name=package, language=language)
            parent = package.rpartition(".")[0]
            self._edge(self._package_node(parent, project_id, language) if parent else project_id, pkg_id, RelationKind.CONTAINS)
        return pkg_id

    def _add_unit(self, unit: CodeUnit, parent_id: str) -> None:
        self.g.add_node(
            unit.fqn,
            kind=unit.kind.value,
            name=unit.name,
            fqn=unit.fqn,
            file=unit.file,
            line=unit.line_start,
            line_end=unit.line_end,
            loc=unit.loc,
            language=unit.language.value if unit.language else None,
            stereotype=unit.stereotype,
            annotations=",".join(a.name for a in unit.annotations),
            type=unit.type,
        )
        self._edge(parent_id, unit.fqn, RelationKind.CONTAINS)
        for child in unit.children:
            if child.kind in TYPE_KINDS or child.kind == CodeUnitKind.METHOD:
                self._add_unit(child, unit.fqn)

    def _edge(self, source: str, target: str, kind: RelationKind, **attrs: Any) -> None:
        if not self.g.has_edge(source, target, key=kind.value):
            self.g.add_edge(source, target, key=kind.value, kind=kind.value, **attrs)

    # ------------------------------------------------------------ queries
    def nodes_of_kind(self, kind: str) -> list[str]:
        return [n for n, d in self.g.nodes(data=True) if d.get("kind") == kind]

    def find(self, name: str) -> list[str]:
        lowered = name.lower()
        return [n for n, d in self.g.nodes(data=True) if lowered == n.lower() or lowered == str(d.get("name", "")).lower()] or [
            n for n in self.g.nodes if lowered in n.lower()
        ]

    def neighbors(self, node: str, kind: str | None = None, direction: str = "out") -> list[tuple[str, str]]:
        edges: Iterable = self.g.out_edges(node, keys=True) if direction == "out" else self.g.in_edges(node, keys=True)
        out = []
        for u, v, k in edges:
            if kind is None or k == kind:
                out.append((v if direction == "out" else u, k))
        return out

    def subgraph_for(self, node: str, depth: int = 1) -> KnowledgeGraph:
        undirected = self.g.to_undirected(as_view=True)
        nodes = nx.single_source_shortest_path_length(undirected, node, cutoff=depth).keys()
        return KnowledgeGraph(self.g.subgraph(nodes).copy())

    def shortest_path(self, source: str, target: str) -> list[str] | None:
        try:
            return nx.shortest_path(self.g.to_undirected(as_view=True), source, target)
        except nx.NetworkXNoPath:
            return None

    def top_by_degree(self, n: int = 10, kinds: set[str] | None = None) -> list[tuple[str, int]]:
        kinds = kinds or {"class", "interface", "enum", "record"}
        candidates = [(node, self.g.in_degree(node)) for node, d in self.g.nodes(data=True) if d.get("kind") in kinds]
        return sorted(candidates, key=lambda x: x[1], reverse=True)[:n]

    def pagerank(self, n: int = 10, kinds: set[str] | None = None) -> list[tuple[str, float]]:
        kinds = kinds or {"class", "interface", "enum", "record"}
        simple = nx.DiGraph(self.g)
        # Pure-python variant avoids a hard dependency on numpy/scipy.
        scores = nx.algorithms.link_analysis.pagerank_alg._pagerank_python(simple) if simple.number_of_nodes() else {}
        ranked = [(node, s) for node, s in scores.items() if self.g.nodes[node].get("kind") in kinds]
        return sorted(ranked, key=lambda x: x[1], reverse=True)[:n]

    def stats(self) -> dict[str, Any]:
        return {
            "nodes": self.g.number_of_nodes(),
            "edges": self.g.number_of_edges(),
            "nodes_by_kind": dict(Counter(d.get("kind") for _, d in self.g.nodes(data=True))),
            "edges_by_kind": dict(Counter(k for _, _, k in self.g.edges(keys=True))),
            "stereotypes": dict(Counter(d.get("stereotype") for _, d in self.g.nodes(data=True) if d.get("stereotype"))),
        }

    # ------------------------------------------------------------ persistence
    def save(self, out_dir: str | Path, graphml: bool = True) -> dict[str, Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        json_path = out / "graph.json"
        json_path.write_text(json.dumps(nx.node_link_data(self.g, edges="edges"), separators=(",", ":"), default=str), encoding="utf-8")
        paths = {"json": json_path}
        if graphml:
            graphml_path = out / "graph.graphml"
            self._write_graphml(graphml_path)
            paths["graphml"] = graphml_path
        return paths

    def _write_graphml(self, path: Path) -> None:
        """Streaming GraphML writer (~10x faster than nx.write_graphml for our flat string/int attributes)."""
        from xml.sax.saxutils import escape, quoteattr

        def gml_type(v: Any) -> str:
            return "boolean" if isinstance(v, bool) else "int" if isinstance(v, int) else "double" if isinstance(v, float) else "string"

        node_keys: dict[str, str] = {}
        for _, data in self.g.nodes(data=True):
            for k, v in data.items():
                if v is not None:
                    node_keys.setdefault(k, gml_type(v))
        edge_keys: dict[str, str] = {}
        for _, _, data in self.g.edges(data=True):
            for k, v in data.items():
                if v is not None:
                    edge_keys.setdefault(k, gml_type(v))

        with path.open("w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="utf-8"?>\n<graphml xmlns="http://graphml.graphdrawing.org/xmlns">\n')
            for k, t in node_keys.items():
                f.write(f'  <key id={quoteattr("n_" + k)} for="node" attr.name={quoteattr(k)} attr.type="{t}"/>\n')
            for k, t in edge_keys.items():
                f.write(f'  <key id={quoteattr("e_" + k)} for="edge" attr.name={quoteattr(k)} attr.type="{t}"/>\n')
            f.write('  <graph edgedefault="directed">\n')
            for node, data in self.g.nodes(data=True):
                f.write(f"    <node id={quoteattr(str(node))}>")
                for k, v in data.items():
                    if v is not None and v != "":
                        f.write(f"<data key={quoteattr('n_' + k)}>{escape(str(v).lower() if isinstance(v, bool) else str(v))}</data>")
                f.write("</node>\n")
            for i, (u, v, data) in enumerate(self.g.edges(data=True)):
                f.write(f'    <edge id="e{i}" source={quoteattr(str(u))} target={quoteattr(str(v))}>')
                for k, val in data.items():
                    if val is not None and val != "":
                        f.write(f"<data key={quoteattr('e_' + k)}>{escape(str(val))}</data>")
                f.write("</edge>\n")
            f.write("  </graph>\n</graphml>\n")

    @classmethod
    def load(cls, path: str | Path) -> KnowledgeGraph:
        p = Path(path)
        if p.is_dir():
            p = p / "graph.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(nx.node_link_graph(data, directed=True, multigraph=True, edges="edges"))

"""End-to-end use-case flows: endpoint -> handler -> services -> repositories -> tables.

Built from the knowledge graph: `calls` edges give the precise path when available,
`depends_on` edges fill the gaps (e.g. repository methods inherited from JpaRepository
have no node, so the repository type is reached through the field dependency).
"""

from __future__ import annotations

from collections import deque

from pydantic import BaseModel, Field

from devanalyzer.core.models import BusinessRule, Endpoint, ProjectAnalysis, RelationKind
from devanalyzer.graph.knowledge_graph import KnowledgeGraph

TYPE_KINDS = {"class", "interface", "enum", "record", "module", "function"}
LAYER_ORDER = ["controller", "service", "component", "repository", "entity", "other"]


class FlowStep(BaseModel):
    fqn: str
    name: str
    stereotype: str | None = None
    kind: str | None = None
    via: str  # relation kind that reached this node
    depth: int


class Flow(BaseModel):
    id: str
    endpoint: Endpoint
    handler: str
    steps: list[FlowStep] = Field(default_factory=list)
    layers: dict[str, list[str]] = Field(default_factory=dict)  # stereotype -> fqns
    tables: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    writes: bool = False  # heuristic: any save/update/delete/persist call along the path

    @property
    def title(self) -> str:
        return f"{self.endpoint.http_method} {self.endpoint.path}"


WRITE_HINTS = ("save", "update", "delete", "remove", "persist", "insert", "create", "merge", "flush", "withdraw", "deposit", "approve", "cancel", "close")


def _owner(fqn: str) -> str:
    return fqn.rpartition(".")[0]


def build_flows(analysis: ProjectAnalysis, graph: KnowledgeGraph, rules: list[BusinessRule] | None = None, max_depth: int = 8) -> list[Flow]:
    g = graph.g
    rules_by_unit: dict[str, list[str]] = {}
    for r in rules or []:
        if r.source_unit:
            rules_by_unit.setdefault(r.source_unit, []).append(r.id)
    table_by_entity: dict[str, list[str]] = {}
    for t in analysis.db_schema.tables:
        for e in t.mapped_entities:
            table_by_entity.setdefault(e, []).append(t.name)
    # raw call names per method: inherited repository methods (save/delete on JpaRepository) have no graph node
    raw_calls: dict[str, list[str]] = {}
    stack = list(analysis.units)
    while stack:
        u = stack.pop()
        for c in u.children:
            if c.kind.value in ("method", "function"):
                raw_calls[c.fqn] = c.calls
            elif c.kind.value in TYPE_KINDS:
                stack.append(c)
        if u.kind.value == "function":
            raw_calls[u.fqn] = u.calls
    flows: list[Flow] = []
    for ep in sorted(analysis.endpoints, key=lambda e: (e.path, e.http_method)):
        handler = ep.handler
        owner = _owner(handler)
        flow = Flow(id=f"flow:{ep.http_method} {ep.path}", endpoint=ep, handler=handler)
        visited_types: dict[str, FlowStep] = {}
        queue: deque[tuple[str, int, str]] = deque()
        if owner in g:
            visited_types[owner] = FlowStep(
                fqn=owner, name=owner.rsplit(".", 1)[-1], stereotype=g.nodes[owner].get("stereotype"), kind=g.nodes[owner].get("kind"), via="exposes", depth=0
            )
        # seed with the handler method and its owner type
        queue.append((handler if handler in g else owner, 1, "calls"))
        queue.append((owner, 1, "depends_on"))
        seen_nodes: set[str] = set()
        while queue:
            node, depth, via = queue.popleft()
            if node in seen_nodes or depth > max_depth or node not in g:
                continue
            seen_nodes.add(node)
            data = g.nodes[node]
            kind = data.get("kind")
            type_fqn = node if kind in TYPE_KINDS else _owner(node)
            if kind == "method":
                names = [node.rsplit(".", 1)[-1].lower()] + [c.rsplit(".", 1)[-1].lower() for c in raw_calls.get(node, [])]
                if any(any(h in n for h in WRITE_HINTS) for n in names):
                    flow.writes = True
            if type_fqn in g and type_fqn not in visited_types and type_fqn != owner:
                tdata = g.nodes[type_fqn]
                visited_types[type_fqn] = FlowStep(
                    fqn=type_fqn, name=type_fqn.rsplit(".", 1)[-1], stereotype=tdata.get("stereotype"), kind=tdata.get("kind"), via=via, depth=depth
                )
            for _, target, k in g.out_edges(node, keys=True):
                if k == RelationKind.CALLS.value:
                    queue.append((target, depth + 1, "calls"))
                    if _owner(target) in g and _owner(target) not in seen_nodes:
                        queue.append((_owner(target), depth + 1, "calls"))
                elif k == RelationKind.DEPENDS_ON.value and kind in TYPE_KINDS:
                    tgt_kind = g.nodes[target].get("kind")
                    if tgt_kind in TYPE_KINDS and g.nodes[target].get("stereotype") != "entity":
                        queue.append((target, depth + 1, "depends_on"))
                    elif tgt_kind in TYPE_KINDS:
                        # entities are leaves: record them but do not traverse their dependencies
                        if target not in visited_types:
                            visited_types[target] = FlowStep(
                                fqn=target, name=target.rsplit(".", 1)[-1], stereotype="entity", kind=tgt_kind, via="depends_on", depth=depth + 1
                            )
                elif k == RelationKind.MAPS_TO.value:
                    flow.tables.append(target.removeprefix("table:"))
        # repositories reach tables through their managed entity (generic argument / Panache entity)
        for step in list(visited_types.values()):
            if step.stereotype == "repository":
                for _, target, k in g.out_edges(step.fqn, keys=True):
                    if k in (RelationKind.DEPENDS_ON.value, RelationKind.IMPORTS.value) and g.nodes[target].get("stereotype") == "entity":
                        if target not in visited_types:
                            visited_types[target] = FlowStep(
                                fqn=target,
                                name=target.rsplit(".", 1)[-1],
                                stereotype="entity",
                                kind=g.nodes[target].get("kind"),
                                via="manages",
                                depth=step.depth + 1,
                            )
        for step in visited_types.values():
            flow.tables += table_by_entity.get(step.fqn, [])
            flow.rule_ids += rules_by_unit.get(step.fqn, [])
        flow.tables = sorted(set(flow.tables))
        flow.rule_ids = sorted(set(flow.rule_ids))
        flow.steps = sorted(visited_types.values(), key=lambda s: (s.depth, LAYER_ORDER.index(s.stereotype) if s.stereotype in LAYER_ORDER else 99, s.name))
        for s in flow.steps:
            flow.layers.setdefault(s.stereotype or "other", []).append(s.fqn)
        flows.append(flow)
    return flows


def flow_mermaid(flow: Flow) -> str:
    """Compact left-to-right flowchart: Client -> layers -> tables."""
    lines = ["```mermaid", "flowchart LR", f"    client([Client]) -->|{flow.endpoint.http_method} {flow.endpoint.path}| h_{_mid(_owner(flow.handler))}"]
    ordered = list(flow.steps)
    for s in ordered:
        shape = {"controller": ("[", "]"), "service": ("([", "])"), "repository": ("[(", ")]"), "entity": ("{{", "}}")}.get(s.stereotype or "", ("[", "]"))
        lines.append(f'    h_{_mid(s.fqn)}{shape[0]}"{s.name}"{shape[1]}')
    by_depth: dict[int, list[FlowStep]] = {}
    for s in ordered:
        by_depth.setdefault(s.depth, []).append(s)
    depths = sorted(by_depth)
    for i in range(len(depths) - 1):
        for a in by_depth[depths[i]]:
            for b in by_depth[depths[i + 1]]:
                if a.stereotype != "entity":
                    lines.append(f"    h_{_mid(a.fqn)} --> h_{_mid(b.fqn)}")
    for t in flow.tables:
        lines.append(f'    t_{_mid(t)}[("{t}")]:::table')
        repos = [s for s in ordered if s.stereotype == "repository"] or [s for s in ordered if s.stereotype == "entity"]
        for r in repos[:3]:
            lines.append(f"    h_{_mid(r.fqn)} -.-> t_{_mid(t)}")
    lines.append("    classDef table fill:#e0a830,color:#fff")
    lines.append("```")
    return "\n".join(lines)


def _mid(text: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_]", "_", text)

from __future__ import annotations

from collections import Counter

from devanalyzer.core.models import CodeUnit, CodeUnitKind, ProjectMetrics, Relation, RelationKind, SourceFile

TYPE_KINDS = (CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD, CodeUnitKind.MODULE)
CALLABLE_KINDS = (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION)
COUPLING_KINDS = {RelationKind.IMPORTS, RelationKind.DEPENDS_ON, RelationKind.EXTENDS, RelationKind.IMPLEMENTS}


def iter_types(units: list[CodeUnit]):
    for u in units:
        if u.kind in TYPE_KINDS:
            yield u
            yield from iter_types(u.children)


def compute_metrics(files: list[SourceFile], units: list[CodeUnit], relations: list[Relation]) -> ProjectMetrics:
    types = list(iter_types(units))
    stereotypes = Counter(t.stereotype for t in types if t.stereotype)
    by_kind: Counter[str] = Counter()
    for t in types:
        by_kind[t.kind.value] += 1
        by_kind["method"] += sum(1 for c in t.children if c.kind in CALLABLE_KINDS)
        by_kind["field"] += sum(1 for c in t.children if c.kind == CodeUnitKind.FIELD)
    by_kind["function"] += sum(1 for u in units if u.kind == CodeUnitKind.FUNCTION)
    if by_kind["function"] == 0:
        del by_kind["function"]

    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    type_names = {t.fqn for t in types}
    seen: set[tuple[str, str]] = set()
    for r in relations:
        if r.kind in COUPLING_KINDS and r.source in type_names and r.target in type_names and r.source != r.target:
            if (r.source, r.target) in seen:
                continue
            seen.add((r.source, r.target))
            fan_out[r.source] += 1
            fan_in[r.target] += 1

    return ProjectMetrics(
        files=len(files),
        loc=sum(f.loc for f in files),
        stereotypes=dict(stereotypes),
        units_by_kind=dict(by_kind),
        fan_in=dict(fan_in),
        fan_out=dict(fan_out),
    )

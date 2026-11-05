from __future__ import annotations

from pydantic import BaseModel, Field

from devanalyzer.core.models import CodeUnitKind, ProjectAnalysis

TYPE_KINDS = {CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD, CodeUnitKind.MODULE, CodeUnitKind.FUNCTION}


class AnalysisDiff(BaseModel):
    units_added: list[str] = Field(default_factory=list)
    units_removed: list[str] = Field(default_factory=list)
    methods_added: list[str] = Field(default_factory=list)
    methods_removed: list[str] = Field(default_factory=list)
    endpoints_added: list[str] = Field(default_factory=list)
    endpoints_removed: list[str] = Field(default_factory=list)
    dependencies_added: list[str] = Field(default_factory=list)
    dependencies_removed: list[str] = Field(default_factory=list)
    dependencies_changed: list[str] = Field(default_factory=list)
    versions_changed: list[str] = Field(default_factory=list)
    relations_added: int = 0
    relations_removed: int = 0
    loc_before: int = 0
    loc_after: int = 0

    @property
    def is_empty(self) -> bool:
        return not any(getattr(self, f) for f in self.model_fields if f.endswith(("_added", "_removed", "_changed"))) and self.loc_before == self.loc_after


def _types(analysis: ProjectAnalysis) -> dict[str, str]:
    out = {}
    stack = list(analysis.units)
    while stack:
        u = stack.pop()
        if u.kind in TYPE_KINDS:
            out[u.fqn] = u.kind.value
            stack.extend(c for c in u.children if c.kind in TYPE_KINDS)
    return out


def _methods(analysis: ProjectAnalysis) -> set[str]:
    out = set()
    stack = list(analysis.units)
    while stack:
        u = stack.pop()
        for c in u.children:
            if c.kind in (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION):
                out.add(f"{c.fqn}({', '.join(p.type or '?' for p in c.parameters)})")
            elif c.kind in TYPE_KINDS:
                stack.append(c)
    return out


def diff_analyses(before: ProjectAnalysis, after: ProjectAnalysis) -> AnalysisDiff:
    t_before, t_after = _types(before), _types(after)
    m_before, m_after = _methods(before), _methods(after)
    e_before = {f"{e.http_method} {e.path}" for e in before.endpoints}
    e_after = {f"{e.http_method} {e.path}" for e in after.endpoints}
    d_before = {d.name: d.version for d in before.dependencies}
    d_after = {d.name: d.version for d in after.dependencies}
    r_before = {(r.source, r.target, r.kind) for r in before.relations}
    r_after = {(r.source, r.target, r.kind) for r in after.relations}
    v_before, v_after = before.versions, after.versions
    return AnalysisDiff(
        units_added=sorted(set(t_after) - set(t_before)),
        units_removed=sorted(set(t_before) - set(t_after)),
        methods_added=sorted(m_after - m_before),
        methods_removed=sorted(m_before - m_after),
        endpoints_added=sorted(e_after - e_before),
        endpoints_removed=sorted(e_before - e_after),
        dependencies_added=sorted(f"{n} {d_after[n] or 'managed'}" for n in set(d_after) - set(d_before)),
        dependencies_removed=sorted(set(d_before) - set(d_after)),
        dependencies_changed=sorted(
            f"{n}: {d_before[n] or 'managed'} -> {d_after[n] or 'managed'}" for n in set(d_before) & set(d_after) if d_before[n] != d_after[n]
        ),
        versions_changed=sorted(
            f"{k}: {v_before.get(k, '-')} -> {v_after.get(k, '-')}" for k in set(v_before) | set(v_after) if v_before.get(k) != v_after.get(k)
        ),
        relations_added=len(r_after - r_before),
        relations_removed=len(r_before - r_after),
        loc_before=before.metrics.loc,
        loc_after=after.metrics.loc,
    )


def render_diff_markdown(diff: AnalysisDiff) -> str:
    if diff.is_empty:
        return "No structural changes detected."
    lines = ["# Analysis diff", "", f"LOC: {diff.loc_before} -> {diff.loc_after}", f"Relations: +{diff.relations_added} / -{diff.relations_removed}", ""]
    sections = [
        ("Types added", diff.units_added),
        ("Types removed", diff.units_removed),
        ("Methods added", diff.methods_added),
        ("Methods removed", diff.methods_removed),
        ("Endpoints added", diff.endpoints_added),
        ("Endpoints removed", diff.endpoints_removed),
        ("Dependencies added", diff.dependencies_added),
        ("Dependencies removed", diff.dependencies_removed),
        ("Dependencies changed", diff.dependencies_changed),
        ("Versions changed", diff.versions_changed),
    ]
    for title, items in sections:
        if items:
            lines += [f"## {title} ({len(items)})", ""] + [f"- `{i}`" for i in items] + [""]
    return "\n".join(lines)

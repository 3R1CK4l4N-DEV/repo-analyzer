from __future__ import annotations

import re

from devanalyzer.core.models import CodeUnit, CodeUnitKind, Endpoint, Relation, RelationKind
from devanalyzer.languages.node.parser import NEST_HTTP, NodeFile

STEREOTYPES: dict[str, str] = {
    "Controller": "controller",
    "Injectable": "service",
    "Entity": "entity",
    "Schema": "entity",
    "Module": "configuration",
    "Resolver": "controller",
    "Guard": "component",
    "Pipe": "component",
    "Interceptor": "component",
    "Middleware": "component",
    "EntityRepository": "repository",
}
NAME_HINTS = [
    (re.compile(r"(Controller)$"), "controller"),
    (re.compile(r"(Service|UseCase|Handler)$"), "service"),
    (re.compile(r"(Repository|Repo|Dao)$"), "repository"),
    (re.compile(r"(Entity|Model|Schema)$"), "entity"),
    (re.compile(r"(Module)$"), "configuration"),
]
TYPE_KINDS = (CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM)


def _arg_path(args: str | None) -> str:
    if not args:
        return ""
    m = re.search(r"""['"`]([^'"`]*)['"`]""", args)
    return m.group(1) if m else ""


def _join(base: str, path: str) -> str:
    joined = f"{base.rstrip('/')}/{path.lstrip('/')}" if path else base
    joined = joined or "/"
    return joined if joined.startswith("/") else "/" + joined


def classify(unit: CodeUnit) -> str | None:
    for ann in unit.annotations:
        if ann.name in STEREOTYPES:
            if ann.name == "Injectable" and re.search(r"(Repository|Repo)$", unit.name):
                return "repository"
            return STEREOTYPES[ann.name]
    if unit.kind == CodeUnitKind.CLASS:
        for rx, stereotype in NAME_HINTS:
            if rx.search(unit.name):
                return stereotype
        if any(base.endswith("Repository") for base in unit.extends):
            return "repository"
    return None


def apply_stereotypes(units: list[CodeUnit]) -> None:
    for unit in units:
        if unit.kind in TYPE_KINDS:
            unit.stereotype = classify(unit)


def extract_nest_endpoints(unit: CodeUnit) -> list[Endpoint]:
    if unit.stereotype != "controller":
        return []
    base = ""
    for ann in unit.annotations:
        if ann.name == "Controller":
            base = _arg_path(ann.arguments)
    endpoints = []
    for method in unit.children:
        if method.kind != CodeUnitKind.METHOD:
            continue
        for ann in method.annotations:
            if ann.name in NEST_HTTP:
                endpoints.append(Endpoint(
                    http_method=NEST_HTTP[ann.name],
                    path=_join(base, _arg_path(ann.arguments)),
                    handler=method.fqn,
                    file=method.file,
                    line=method.line_start,
                    framework="NestJS",
                ))
    return endpoints


def extract_relations(nf: NodeFile, project_modules: set[str], project_types: set[str]) -> list[Relation]:
    relations: list[Relation] = []
    local_types = {u.name: u.fqn for u in nf.units if u.kind in TYPE_KINDS}

    def resolve(name: str) -> str | None:
        base = name.split("<")[0].split("[")[0].strip()
        if base in local_types:
            return local_types[base]
        resolved = nf.resolve_import(base, project_modules)
        return resolved if resolved in project_types else None

    for unit in nf.units:
        for imp in nf.imports:
            for name in imp.names + ([imp.default] if imp.default else []):
                target = nf.resolve_import(name, project_modules)
                if target and target in project_types:
                    relations.append(Relation(source=unit.fqn, target=target, kind=RelationKind.IMPORTS))
        for base in unit.extends:
            if t := resolve(base):
                relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.EXTENDS))
        for base in unit.implements:
            if t := resolve(base):
                relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.IMPLEMENTS))
        field_types: dict[str, str] = {}
        for child in unit.children:
            if child.kind == CodeUnitKind.FIELD and child.type:
                field_types[child.name] = child.type
                if t := resolve(child.type):
                    relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.DEPENDS_ON))
            elif child.kind == CodeUnitKind.METHOD:
                relations.append(Relation(source=unit.fqn, target=child.fqn, kind=RelationKind.CONTAINS))
                for p in child.parameters:
                    if p.type and (t := resolve(p.type)) and t != unit.fqn:
                        relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.DEPENDS_ON))
        for child in unit.children:
            if child.kind != CodeUnitKind.METHOD:
                continue
            for call in child.calls:
                receiver, _, method_name = call.rpartition(".")
                if receiver in field_types and (t := resolve(field_types[receiver])):
                    relations.append(Relation(source=child.fqn, target=f"{t}.{method_name}", kind=RelationKind.CALLS))
    seen: set[tuple] = set()
    unique = []
    for r in relations:
        key = (r.source, r.target, r.kind)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique

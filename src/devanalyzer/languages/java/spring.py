from __future__ import annotations

import re

from devanalyzer.core.models import CodeUnit, CodeUnitKind, Endpoint, Relation, RelationKind
from devanalyzer.languages.java.parser import JavaFile

STEREOTYPES: dict[str, str] = {
    "RestController": "controller",
    "Controller": "controller",
    "Service": "service",
    "Repository": "repository",
    "Entity": "entity",
    "Table": "entity",
    "Document": "entity",
    "Configuration": "configuration",
    "Component": "component",
    "ControllerAdvice": "advice",
    "RestControllerAdvice": "advice",
    "SpringBootApplication": "application",
    "Mapper": "mapper",
}

MAPPING_METHODS: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

REPOSITORY_BASES = ("Repository", "CrudRepository", "JpaRepository", "PagingAndSortingRepository", "MongoRepository")
PRIMITIVES = {"int", "long", "double", "float", "boolean", "char", "byte", "short", "void", "var"}
JDK_PREFIXES = ("java.", "javax.", "jakarta.", "org.springframework.", "lombok.", "org.slf4j.")


def _path_from_args(args: str | None) -> str:
    if not args:
        return ""
    m = re.search(r"""(?:value|path)\s*=\s*\{?\s*"([^"]*)""", args) or re.match(r"""\s*"([^"]*)""", args)
    return m.group(1) if m else ""


def _request_mapping_method(args: str | None) -> str:
    if args and (m := re.search(r"RequestMethod\.(\w+)", args)):
        return m.group(1)
    return "ANY"


def _join(base: str, path: str) -> str:
    joined = f"{base.rstrip('/')}/{path.lstrip('/')}" if path else base
    return joined if joined.startswith("/") else "/" + joined


def classify(unit: CodeUnit) -> str | None:
    for ann in unit.annotations:
        if ann.name in STEREOTYPES:
            return STEREOTYPES[ann.name]
    if unit.kind == CodeUnitKind.INTERFACE and any(base.split(".")[-1].split("<")[0] in REPOSITORY_BASES for base in unit.extends):
        return "repository"
    return None


def apply_stereotypes(units: list[CodeUnit]) -> None:
    for unit in units:
        unit.stereotype = classify(unit)
        apply_stereotypes([c for c in unit.children if c.kind in (CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD)])


def extract_endpoints(unit: CodeUnit) -> list[Endpoint]:
    if unit.stereotype != "controller":
        return []
    base = ""
    for ann in unit.annotations:
        if ann.name == "RequestMapping":
            base = _path_from_args(ann.arguments)
    endpoints = []
    for method in unit.children:
        if method.kind != CodeUnitKind.METHOD:
            continue
        for ann in method.annotations:
            if ann.name in MAPPING_METHODS:
                http = MAPPING_METHODS[ann.name]
            elif ann.name == "RequestMapping":
                http = _request_mapping_method(ann.arguments)
            else:
                continue
            endpoints.append(
                Endpoint(
                    http_method=http,
                    path=_join(base, _path_from_args(ann.arguments)),
                    handler=method.fqn,
                    file=method.file,
                    line=method.line_start,
                    framework="Spring Web",
                )
            )
    return endpoints


def _is_project_type(fqn: str, project_types: set[str]) -> bool:
    return fqn in project_types


def extract_relations(jf: JavaFile, project_types: set[str]) -> list[Relation]:
    relations: list[Relation] = []
    for imp in jf.imports:
        for unit in jf.units:
            if imp in project_types and not imp.startswith(JDK_PREFIXES):
                relations.append(Relation(source=unit.fqn, target=imp, kind=RelationKind.IMPORTS))

    def walk(unit: CodeUnit) -> None:
        for target in unit.extends:
            relations.append(Relation(source=unit.fqn, target=target, kind=RelationKind.EXTENDS))
        for target in unit.implements:
            relations.append(Relation(source=unit.fqn, target=target, kind=RelationKind.IMPLEMENTS))
        for child in unit.children:
            if child.kind == CodeUnitKind.FIELD and child.type:
                resolved = _resolve_generic_types(child.type, jf, project_types)
                for t in resolved:
                    relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.DEPENDS_ON))
            elif child.kind == CodeUnitKind.METHOD:
                relations.append(Relation(source=unit.fqn, target=child.fqn, kind=RelationKind.CONTAINS))
                for p in child.parameters:
                    if p.type:
                        for t in _resolve_generic_types(p.type, jf, project_types):
                            if t != unit.fqn:
                                relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.DEPENDS_ON))
                field_types = {c.name: c.type for c in unit.children if c.kind == CodeUnitKind.FIELD and c.type}
                for call in child.calls:
                    if "." not in call:
                        continue
                    receiver, _, method_name = call.rpartition(".")
                    receiver = receiver.replace("this.", "")
                    if receiver in field_types:
                        for t in _resolve_generic_types(field_types[receiver], jf, project_types):
                            relations.append(Relation(source=child.fqn, target=f"{t}.{method_name}", kind=RelationKind.CALLS))
            elif child.kind in (CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD):
                relations.append(Relation(source=unit.fqn, target=child.fqn, kind=RelationKind.CONTAINS))
                walk(child)

    for unit in jf.units:
        walk(unit)
    seen: set[tuple[str, str, RelationKind]] = set()
    unique = []
    for r in relations:
        key = (r.source, r.target, r.kind)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _resolve_generic_types(type_text: str, jf: JavaFile, project_types: set[str]) -> list[str]:
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", type_text)
    out = []
    for n in names:
        if n in PRIMITIVES:
            continue
        resolved = jf.resolve(n)
        if resolved and resolved in project_types:
            out.append(resolved)
    return out

from __future__ import annotations

import re

from devanalyzer.core.models import CodeUnit, CodeUnitKind, Relation, RelationKind
from devanalyzer.languages.python.parser import HTTP_METHODS, ROUTE_DECORATORS, PyFile

BASE_STEREOTYPES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(^|\.)(Base|DeclarativeBase|Model|Document|BaseModel|Schema|TypedDict|Table)$"), "entity"),
    (re.compile(r"(^|\.)(ViewSet|APIView|View|HTTPEndpoint|Resource|Controller)$"), "controller"),
    (re.compile(r"(^|\.)(Repository|AbstractRepository|BaseRepository)$"), "repository"),
    (re.compile(r"(^|\.)(Enum|StrEnum|IntEnum)$"), "enum"),
]
NAME_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(Controller|Router|View|ViewSet|Handler)$"), "controller"),
    (re.compile(r"(Service|UseCase|Interactor|Manager)$"), "service"),
    (re.compile(r"(Repository|Repo|Dao|Store|Gateway)$"), "repository"),
    (re.compile(r"(Entity|Model|Schema|DTO|Dto)$"), "entity"),
    (re.compile(r"(Config|Settings)$"), "configuration"),
]
TYPE_KINDS = (CodeUnitKind.CLASS, CodeUnitKind.MODULE)
BUILTIN_TYPES = {"str", "int", "float", "bool", "list", "dict", "set", "tuple", "None", "Any", "Optional", "List", "Dict"}


def classify(unit: CodeUnit) -> str | None:
    if unit.kind == CodeUnitKind.MODULE:
        return None
    if any(a.name == "dataclass" or a.name.endswith(".dataclass") for a in unit.annotations) and not any(rx.search(unit.name) for rx, _ in NAME_HINTS[:3]):
        return "entity"
    for base in unit.extends:
        for rx, stereotype in BASE_STEREOTYPES:
            if rx.search(base):
                return "entity" if stereotype == "enum" else stereotype
    for rx, stereotype in NAME_HINTS:
        if rx.search(unit.name):
            return stereotype
    return None


def apply_stereotypes(units: list[CodeUnit]) -> None:
    for unit in units:
        if unit.kind in TYPE_KINDS:
            unit.stereotype = classify(unit)
            apply_stereotypes([c for c in unit.children if c.kind == CodeUnitKind.CLASS])
        elif unit.kind == CodeUnitKind.FUNCTION and any(
            a.name.rpartition(".")[2] in HTTP_METHODS | ROUTE_DECORATORS for a in unit.annotations
        ):
            unit.stereotype = "controller"


def _type_names(type_text: str | None) -> list[str]:
    if not type_text:
        return []
    return [n for n in re.findall(r"[A-Za-z_][\w.]*", type_text) if n not in BUILTIN_TYPES]


def extract_relations(pf: PyFile, project_modules: set[str], project_types: set[str]) -> list[Relation]:
    relations: list[Relation] = []
    local = {u.name: u.fqn for u in pf.units if u.kind == CodeUnitKind.CLASS}

    def resolve(name: str) -> str | None:
        if name in local:
            return local[name]
        resolved = pf.resolve_name(name, project_modules)
        if resolved in project_types:
            return resolved
        # `module.Class` style when the module itself was imported
        if "." in name:
            mod_alias, _, cls = name.rpartition(".")
            resolved_mod = pf.resolve_name(mod_alias, project_modules)
            if resolved_mod and f"{resolved_mod}.{cls}" in project_types:
                return f"{resolved_mod}.{cls}"
        return None

    imported_targets: set[str] = set()
    for imp in pf.imports:
        for alias in imp.names:
            target = pf.resolve_name(alias, project_modules)
            if target and target in project_types:
                imported_targets.add(target)

    def walk(unit: CodeUnit) -> None:
        for target in imported_targets:
            if target != unit.fqn:
                relations.append(Relation(source=unit.fqn, target=target, kind=RelationKind.IMPORTS))
        for base in unit.extends:
            if t := resolve(base):
                relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.EXTENDS))
        field_types: dict[str, str] = {}
        for child in unit.children:
            if child.kind == CodeUnitKind.FIELD and child.type:
                field_types[child.name] = child.type
                for n in _type_names(child.type):
                    if t := resolve(n):
                        relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.DEPENDS_ON))
            elif child.kind == CodeUnitKind.METHOD:
                relations.append(Relation(source=unit.fqn, target=child.fqn, kind=RelationKind.CONTAINS))
                for p in child.parameters:
                    for n in _type_names(p.type):
                        if (t := resolve(n)) and t != unit.fqn:
                            relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.DEPENDS_ON))
            elif child.kind == CodeUnitKind.CLASS:
                relations.append(Relation(source=unit.fqn, target=child.fqn, kind=RelationKind.CONTAINS))
                walk(child)
        for child in unit.children:
            if child.kind != CodeUnitKind.METHOD:
                continue
            for call in child.calls:
                receiver, _, method = call.rpartition(".")
                if receiver in field_types:
                    for n in _type_names(field_types[receiver]):
                        if t := resolve(n):
                            relations.append(Relation(source=child.fqn, target=f"{t}.{method}", kind=RelationKind.CALLS))
                elif (t := resolve(call)) and t in project_types:
                    relations.append(Relation(source=child.fqn, target=t, kind=RelationKind.CALLS))

    for unit in pf.units:
        if unit.kind == CodeUnitKind.FUNCTION:
            for target in imported_targets:
                relations.append(Relation(source=unit.fqn, target=target, kind=RelationKind.IMPORTS))
            for p in unit.parameters:
                for n in _type_names(p.type):
                    if t := resolve(n):
                        relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.DEPENDS_ON))
            for call in unit.calls:
                if (t := resolve(call)) and t in project_types:
                    relations.append(Relation(source=unit.fqn, target=t, kind=RelationKind.CALLS))
        else:
            walk(unit)
    seen: set[tuple] = set()
    unique = []
    for r in relations:
        key = (r.source, r.target, r.kind)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique

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
    # Jakarta EE / CDI / JAX-RS
    "Path": "controller",
    "ApplicationScoped": "service",
    "RequestScoped": "service",
    "Singleton": "service",
    "Stateless": "service",
    "Stateful": "service",
    "Dependent": "component",
    "Embeddable": "entity",
    "MappedSuperclass": "entity",
    "Provider": "component",
    "ApplicationPath": "application",
    # Quarkus
    "QuarkusMain": "application",
    "RegisterRestClient": "client",
    # Micronaut
    "Client": "client",
    "Factory": "configuration",
    "Prototype": "component",
    "Introspected": "entity",
    "Serdeable": "entity",
}

# Framework tags for endpoints
JAXRS_METHODS: dict[str, str] = {"GET": "GET", "POST": "POST", "PUT": "PUT", "DELETE": "DELETE", "PATCH": "PATCH", "HEAD": "HEAD", "OPTIONS": "OPTIONS"}
MICRONAUT_METHODS: dict[str, str] = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "Head": "HEAD", "Options": "OPTIONS"}
PANACHE_BASES = ("PanacheRepository", "PanacheRepositoryBase", "PanacheMongoRepository", "PanacheEntity", "PanacheEntityBase", "PanacheMongoEntity")

MAPPING_METHODS: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

REPOSITORY_BASES = (
    "Repository",
    "CrudRepository",
    "JpaRepository",
    "PagingAndSortingRepository",
    "MongoRepository",
    "ReactiveCrudRepository",
    "PanacheRepository",
    "PanacheRepositoryBase",
    "PanacheMongoRepository",
    "GenericRepository",
    "CoroutineCrudRepository",
)
ENTITY_BASES = ("PanacheEntity", "PanacheEntityBase", "PanacheMongoEntity", "PanacheMongoEntityBase")
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


def _bases(unit: CodeUnit) -> set[str]:
    return {b.split(".")[-1].split("<")[0] for b in unit.extends + unit.implements}


def classify(unit: CodeUnit) -> str | None:
    names = unit.annotation_names()
    # Micronaut @Controller and Spring @Controller share a name; JAX-RS @Path marks resources.
    if "Controller" in names or "RestController" in names or "Path" in names:
        return "controller"
    # Base classes are more specific than CDI scopes (@ApplicationScoped class implementing PanacheRepository is a repository).
    bases = _bases(unit)
    if bases & set(REPOSITORY_BASES):
        return "repository"
    if bases & set(ENTITY_BASES):
        return "entity"
    for ann in unit.annotations:
        if ann.name in STEREOTYPES:
            return STEREOTYPES[ann.name]
    if unit.name.endswith(("Repository", "Repo", "Dao")) and names & {"ApplicationScoped", "Singleton", "JdbcRepository", "R2dbcRepository"}:
        return "repository"
    return None


def apply_stereotypes(units: list[CodeUnit]) -> None:
    for unit in units:
        unit.stereotype = classify(unit)
        apply_stereotypes([c for c in unit.children if c.kind in (CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD)])


def _framework_of(unit: CodeUnit) -> str:
    names = unit.annotation_names()
    if "Path" in names or "ApplicationPath" in names:
        return "JAX-RS"
    if (
        "Controller" in names
        and "RequestMapping" not in names
        and any(m.has_annotation(*MICRONAUT_METHODS) for m in unit.children if m.kind == CodeUnitKind.METHOD)
    ):
        return "Micronaut"
    return "Spring Web"


def extract_endpoints(unit: CodeUnit) -> list[Endpoint]:
    if unit.stereotype != "controller":
        return []
    framework = _framework_of(unit)
    base = ""
    for ann in unit.annotations:
        if ann.name in ("RequestMapping", "Path") or (framework == "Micronaut" and ann.name == "Controller"):
            base = _path_from_args(ann.arguments)
    endpoints = []
    for method in unit.children:
        if method.kind != CodeUnitKind.METHOD:
            continue
        # JAX-RS: HTTP verb and @Path are separate annotations on the same method
        method_path = next((_path_from_args(a.arguments) for a in method.annotations if a.name == "Path"), "")
        for ann in method.annotations:
            if framework == "JAX-RS" and ann.name in JAXRS_METHODS:
                http, path = JAXRS_METHODS[ann.name], method_path
            elif framework == "Micronaut" and ann.name in MICRONAUT_METHODS:
                http, path = MICRONAUT_METHODS[ann.name], _path_from_args(ann.arguments) or _path_from_args(_kw(ann.arguments, "uri"))
            elif ann.name in MAPPING_METHODS:
                http, path = MAPPING_METHODS[ann.name], _path_from_args(ann.arguments)
            elif ann.name == "RequestMapping":
                http, path = _request_mapping_method(ann.arguments), _path_from_args(ann.arguments)
            else:
                continue
            endpoints.append(
                Endpoint(http_method=http, path=_join(base, path), handler=method.fqn, file=method.file, line=method.line_start, framework=framework)
            )
    return endpoints


def _kw(args: str | None, key: str) -> str | None:
    if not args:
        return None
    m = re.search(key + r'\s*=\s*"([^"]*)"', args)
    return f'"{m.group(1)}"' if m else None


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

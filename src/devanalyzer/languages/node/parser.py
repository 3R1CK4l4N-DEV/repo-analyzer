from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from devanalyzer.core.models import Annotation, CodeUnit, CodeUnitKind, Endpoint, Language, Parameter, SourceFile

TYPE_DECLARATIONS = {
    "class_declaration": CodeUnitKind.CLASS,
    "abstract_class_declaration": CodeUnitKind.CLASS,
    "interface_declaration": CodeUnitKind.INTERFACE,
    "enum_declaration": CodeUnitKind.ENUM,
}
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head", "all"}
NEST_HTTP = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "Options": "OPTIONS", "Head": "HEAD", "All": "ANY"}
ROUTER_NAMES = {"app", "router", "server", "api", "fastify", "r"}


@dataclass
class ImportSpec:
    source: str
    names: list[str]
    default: str | None = None
    namespace: str | None = None


@dataclass
class NodeFile:
    source: SourceFile
    module: str  # module id derived from path, e.g. src/orders/order.controller
    imports: list[ImportSpec] = field(default_factory=list)
    units: list[CodeUnit] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)

    def resolve_import(self, name: str, project_modules: set[str], aliases: tuple[tuple[str, str], ...] | list[tuple[str, str]] = ()) -> str | None:
        """Map an imported identifier to `<module>.<name>` when it points to a project file (relative path or tsconfig alias)."""
        for imp in self.imports:
            if name in imp.names or name == imp.default or name == imp.namespace:
                if imp.source.startswith("."):
                    targets = [str(PurePosixPath(self.module).parent / imp.source)]
                else:
                    targets = [directory + imp.source[len(prefix) :] for prefix, directory in aliases if imp.source.startswith(prefix)]
                for target in targets:
                    normalized = _normalize_module(target)
                    for candidate in (normalized, f"{normalized}/index"):
                        if candidate in project_modules:
                            return f"{candidate}.{name}"
                return None
        return None


def _normalize_module(path: str) -> str:
    parts: list[str] = []
    for p in path.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
            continue
        parts.append(p)
    joined = "/".join(parts)
    return re.sub(r"\.(m|c)?(j|t)sx?$", "", joined)


def module_id(rel_path: str) -> str:
    return _normalize_module(rel_path)


class NodeParser:
    def __init__(self):
        self.parsers = {
            Language.TYPESCRIPT: get_parser("typescript"),
            Language.JAVASCRIPT: get_parser("javascript"),
        }
        self.tsx = get_parser("tsx")

    def parse(self, source: SourceFile) -> NodeFile:
        parser = self.tsx if source.extension == ".tsx" else self.parsers.get(source.language or Language.JAVASCRIPT, self.parsers[Language.JAVASCRIPT])
        tree = parser.parse(source.content.encode("utf-8"))
        nf = NodeFile(source=source, module=module_id(source.rel_path))
        self._walk_top(tree.root_node, nf)
        return nf

    # ------------------------------------------------------------ traversal
    def _walk_top(self, root: Node, nf: NodeFile) -> None:
        pending_decorators: list[Annotation] = []
        for child in root.children:
            before = len(nf.units)
            if child.type == "import_statement":
                nf.imports.append(self._import(child, nf.source))
            elif child.type == "decorator":
                pending_decorators.append(self._decorator(child, nf.source))
                continue
            elif child.type == "export_statement":
                decorators = pending_decorators + [self._decorator(c, nf.source) for c in child.children if c.type == "decorator"]
                for inner in child.children:
                    self._declaration(inner, nf, decorators, exported=True)
            else:
                self._declaration(child, nf, pending_decorators, exported=False)
            # Declaration span starts at the statement (export keyword / first decorator), like Java annotations.
            for unit in nf.units[before:]:
                unit.line_start = min(unit.line_start, child.start_point[0] + 1)
            pending_decorators = []
            if child.type == "expression_statement":
                self._express_routes(child, nf)
            if child.type in ("lexical_declaration", "variable_declaration"):
                self._require_imports(child, nf)

    def _declaration(self, node: Node, nf: NodeFile, decorators: list[Annotation], exported: bool) -> None:
        src = nf.source
        if node.type in TYPE_DECLARATIONS:
            nf.units.append(self._type_unit(node, nf, decorators, exported))
        elif node.type == "function_declaration":
            nf.units.append(self._function_unit(node, nf, decorators, exported, nf.module))
        elif node.type in ("lexical_declaration", "variable_declaration"):
            for decl in node.children:
                if decl.type != "variable_declarator":
                    continue
                value = decl.child_by_field_name("value")
                if value is not None and value.type in ("arrow_function", "function_expression", "function"):
                    name = self._text(decl.child_by_field_name("name"), src)
                    nf.units.append(self._function_unit(value, nf, decorators, exported, nf.module, name=name, span=decl))

    # ------------------------------------------------------------ units
    def _type_unit(self, node: Node, nf: NodeFile, decorators: list[Annotation], exported: bool) -> CodeUnit:
        src = nf.source
        name = self._text(node.child_by_field_name("name"), src)
        fqn = f"{nf.module}.{name}"
        extends: list[str] = []
        implements: list[str] = []
        for child in node.children:
            if child.type == "class_heritage":
                for clause in child.children:
                    targets = [
                        self._text(c, src).split("<")[0]
                        for c in clause.children
                        if c.type in ("identifier", "type_identifier", "generic_type", "member_expression")
                    ]
                    (extends if clause.type == "extends_clause" else implements).extend(targets)
                if node.type == "class_declaration" and not any(c.type in ("extends_clause", "implements_clause") for c in child.children):
                    extends += [self._text(c, src).split("<")[0] for c in child.children if c.type in ("identifier", "member_expression")]
            elif child.type == "extends_type_clause":
                extends += [self._text(c, src).split("<")[0] for c in child.children if c.type in ("type_identifier", "generic_type", "nested_type_identifier")]
        modifiers = (["export"] if exported else []) + (["abstract"] if node.type == "abstract_class_declaration" else [])
        unit = CodeUnit(
            kind=TYPE_DECLARATIONS[node.type],
            name=name,
            fqn=fqn,
            file=src.rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=src.language,
            package=nf.module.rpartition("/")[0] or None,
            modifiers=modifiers,
            annotations=decorators,
            extends=extends,
            implements=implements,
            docstring=self._docstring(node, src),
        )
        body = node.child_by_field_name("body")
        if body is None:
            return unit
        pending: list[Annotation] = []
        pending_line: int | None = None
        for member in body.children:
            if member.type == "decorator":
                pending.append(self._decorator(member, src))
                pending_line = pending_line or member.start_point[0] + 1
                continue
            child: CodeUnit | None = None
            if member.type in ("method_definition", "method_signature", "abstract_method_signature"):
                child = self._method_unit(member, nf, fqn, pending)
            elif member.type in ("public_field_definition", "property_signature", "field_definition"):
                child = self._field_unit(member, nf, fqn, pending)
            if child is not None:
                if pending_line:
                    child.line_start = min(child.line_start, pending_line)
                unit.children.append(child)
            pending, pending_line = [], None
        # NestJS constructor injection: parameters with accessibility modifiers become fields
        ctor = next((m for m in unit.children if m.kind == CodeUnitKind.METHOD and m.name == "constructor"), None)
        if ctor is not None:
            for p in ctor.parameters:
                if p.name.startswith(("private ", "public ", "protected ", "readonly ")):
                    clean = p.name.split()[-1]
                    unit.children.append(
                        CodeUnit(
                            kind=CodeUnitKind.FIELD,
                            name=clean,
                            fqn=f"{fqn}.{clean}",
                            file=src.rel_path,
                            line_start=ctor.line_start,
                            line_end=ctor.line_start,
                            language=src.language,
                            type=p.type,
                            modifiers=["injected"],
                        )
                    )
                    p.name = clean
        return unit

    def _method_unit(self, node: Node, nf: NodeFile, parent_fqn: str, decorators: list[Annotation]) -> CodeUnit:
        src = nf.source
        name = self._text(node.child_by_field_name("name"), src)
        params = self._params(node.child_by_field_name("parameters"), src)
        ret = node.child_by_field_name("return_type")
        mods = [self._text(c, src) for c in node.children if c.type in ("accessibility_modifier", "async", "static", "readonly", "override")]
        calls: list[str] = []
        body = node.child_by_field_name("body")
        if body is not None:
            self._collect_calls(body, src, calls)
        return CodeUnit(
            kind=CodeUnitKind.METHOD,
            name=name,
            fqn=f"{parent_fqn}.{name}",
            file=src.rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=src.language,
            modifiers=mods,
            annotations=decorators,
            type=self._text(ret, src).lstrip(": ").strip() if ret else None,
            parameters=params,
            docstring=self._docstring(node, src),
            calls=sorted(set(calls)),
        )

    def _field_unit(self, node: Node, nf: NodeFile, parent_fqn: str, decorators: list[Annotation]) -> CodeUnit:
        src = nf.source
        name_node = node.child_by_field_name("name") or next((c for c in node.children if c.type in ("property_identifier", "identifier")), None)
        name = self._text(name_node, src) if name_node else "?"
        type_node = node.child_by_field_name("type") or next((c for c in node.children if c.type == "type_annotation"), None)
        return CodeUnit(
            kind=CodeUnitKind.FIELD,
            name=name,
            fqn=f"{parent_fqn}.{name}",
            file=src.rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=src.language,
            annotations=decorators,
            type=self._text(type_node, src).lstrip(": ").strip() if type_node else None,
            modifiers=[self._text(c, src) for c in node.children if c.type in ("accessibility_modifier", "static", "readonly")],
        )

    def _function_unit(
        self, node: Node, nf: NodeFile, decorators: list[Annotation], exported: bool, module: str, name: str | None = None, span: Node | None = None
    ) -> CodeUnit:
        src = nf.source
        name = name or (self._text(node.child_by_field_name("name"), src) if node.child_by_field_name("name") else "anonymous")
        params = self._params(node.child_by_field_name("parameters"), src)
        ret = node.child_by_field_name("return_type")
        calls: list[str] = []
        body = node.child_by_field_name("body")
        if body is not None:
            self._collect_calls(body, src, calls)
        span = span or node
        return CodeUnit(
            kind=CodeUnitKind.FUNCTION,
            name=name,
            fqn=f"{module}.{name}",
            file=src.rel_path,
            line_start=span.start_point[0] + 1,
            line_end=span.end_point[0] + 1,
            language=src.language,
            package=module.rpartition("/")[0] or None,
            modifiers=(["export"] if exported else []) + (["async"] if any(c.type == "async" for c in node.children) else []),
            annotations=decorators,
            type=self._text(ret, src).lstrip(": ").strip() if ret else None,
            parameters=params,
            docstring=self._docstring(span, src),
            calls=sorted(set(calls)),
        )

    # ------------------------------------------------------------ express / fastify routes
    def _express_routes(self, stmt: Node, nf: NodeFile) -> None:
        src = nf.source
        call = stmt.children[0] if stmt.children else None
        if call is None or call.type != "call_expression":
            return
        fn = call.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return
        obj = fn.child_by_field_name("object")
        prop = fn.child_by_field_name("property")
        if obj is None or prop is None:
            return
        method = self._text(prop, src)
        receiver = self._text(obj, src)
        if method not in HTTP_METHODS or (receiver not in ROUTER_NAMES and not receiver.endswith(("Router", "router", "app"))):
            return
        args = call.child_by_field_name("arguments")
        if args is None:
            return
        arg_nodes = [a for a in args.children if a.type not in ("(", ")", ",")]
        if not arg_nodes or arg_nodes[0].type not in ("string", "template_string"):
            return
        path = self._text(arg_nodes[0], src).strip("'\"`")
        handler = (
            self._text(arg_nodes[-1], src) if len(arg_nodes) > 1 and arg_nodes[-1].type in ("identifier", "member_expression") else f"{nf.module}.<inline>"
        )
        nf.endpoints.append(
            Endpoint(
                http_method=method.upper() if method != "all" else "ANY",
                path=path if path.startswith("/") else "/" + path,
                handler=handler if "." in handler else f"{nf.module}.{handler}",
                file=src.rel_path,
                line=stmt.start_point[0] + 1,
                framework="Express",
            )
        )

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _text(node: Node | None, source: SourceFile) -> str:
        if node is None:
            return ""
        return source.content.encode("utf-8")[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _decorator(self, node: Node, src: SourceFile) -> Annotation:
        expr = node.children[1] if len(node.children) > 1 else node
        if expr.type == "call_expression":
            fn = expr.child_by_field_name("function")
            args = expr.child_by_field_name("arguments")
            return Annotation(name=self._text(fn, src).split(".")[-1], arguments=self._text(args, src).strip("()") if args else None)
        return Annotation(name=self._text(expr, src).split(".")[-1])

    def _params(self, node: Node | None, src: SourceFile) -> list[Parameter]:
        if node is None:
            return []
        out = []
        for p in node.children:
            if p.type not in ("required_parameter", "optional_parameter", "identifier", "rest_parameter", "assignment_pattern"):
                continue
            if p.type == "identifier":
                out.append(Parameter(name=self._text(p, src)))
                continue
            pattern = p.child_by_field_name("pattern") or next((c for c in p.children if c.type in ("identifier", "object_pattern", "array_pattern")), None)
            type_node = p.child_by_field_name("type") or next((c for c in p.children if c.type == "type_annotation"), None)
            mods = [self._text(c, src) for c in p.children if c.type in ("accessibility_modifier", "readonly")]
            name = self._text(pattern, src) if pattern else self._text(p, src)
            out.append(Parameter(name=(" ".join(mods) + " " + name).strip(), type=self._text(type_node, src).lstrip(": ").strip() if type_node else None))
        return out

    def _import(self, node: Node, src: SourceFile) -> ImportSpec:
        source = ""
        names: list[str] = []
        default = namespace = None
        for child in node.children:
            if child.type == "string":
                source = self._text(child, src).strip("'\"")
            elif child.type == "import_clause":
                for c in child.children:
                    if c.type == "identifier":
                        default = self._text(c, src)
                    elif c.type == "namespace_import":
                        namespace = self._text(c, src).split("as")[-1].strip()
                    elif c.type == "named_imports":
                        for spec in c.children:
                            if spec.type == "import_specifier":
                                alias = spec.child_by_field_name("alias") or spec.child_by_field_name("name")
                                names.append(self._text(alias, src))
        return ImportSpec(source=source, names=names, default=default, namespace=namespace)

    def _require_imports(self, node: Node, nf: NodeFile) -> None:
        src = nf.source
        for decl in node.children:
            if decl.type != "variable_declarator":
                continue
            value = decl.child_by_field_name("value")
            if value is None or value.type != "call_expression" or self._text(value.child_by_field_name("function"), src) != "require":
                continue
            args = value.child_by_field_name("arguments")
            source = next((self._text(a, src).strip("'\"") for a in args.children if a.type == "string"), "") if args else ""
            name_node = decl.child_by_field_name("name")
            if name_node is None:
                continue
            if name_node.type == "object_pattern":
                names = [
                    self._text(c, src).split(":")[-1].strip() for c in name_node.children if c.type in ("shorthand_property_identifier_pattern", "pair_pattern")
                ]
                nf.imports.append(ImportSpec(source=source, names=names))
            else:
                nf.imports.append(ImportSpec(source=source, names=[], default=self._text(name_node, src)))

    def _docstring(self, node: Node, src: SourceFile) -> str | None:
        prev = node.prev_sibling
        while prev is not None and prev.type in ("decorator", "export"):
            prev = prev.prev_sibling
        if prev is None and node.parent is not None and node.parent.type == "export_statement":
            return self._docstring(node.parent, src)
        if prev is not None and prev.type == "comment":
            text = self._text(prev, src)
            if text.startswith("/**"):
                lines = [ln.strip().lstrip("*").strip() for ln in text.strip("/*").splitlines()]
                return "\n".join(ln for ln in lines if ln) or None
        return None

    def _collect_calls(self, node: Node, src: SourceFile, out: list[str]) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    if fn.type == "member_expression":
                        obj = fn.child_by_field_name("object")
                        prop = fn.child_by_field_name("property")
                        if obj is not None and prop is not None:
                            receiver = self._text(obj, src)
                            if receiver.startswith("this."):
                                receiver = receiver[5:]
                            if re.fullmatch(r"[\w$.]+", receiver):
                                out.append(f"{receiver}.{self._text(prop, src)}")
                    elif fn.type == "identifier":
                        out.append(self._text(fn, src))
            stack.extend(n.children)

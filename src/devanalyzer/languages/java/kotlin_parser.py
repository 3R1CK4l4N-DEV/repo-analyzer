"""Kotlin parser producing the same CodeUnit shapes as JavaParser, so Spring/Jakarta
heuristics and relation extraction in `spring.py` work unchanged."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from devanalyzer.core.models import Annotation, CodeUnit, CodeUnitKind, Language, Parameter, SourceFile

KOTLIN_BUILTINS = {
    "String",
    "Int",
    "Long",
    "Double",
    "Float",
    "Boolean",
    "Unit",
    "Any",
    "List",
    "Map",
    "Set",
    "MutableList",
    "Nothing",
    "Char",
    "Byte",
    "Short",
}


@dataclass
class KotlinFile:
    source: SourceFile
    package: str | None
    imports: list[str]
    units: list[CodeUnit] = field(default_factory=list)

    def resolve(self, simple_name: str) -> str | None:
        base = simple_name.split("<")[0].split("?")[0].strip()
        if "." in base:
            return base
        for imp in self.imports:
            if imp.endswith("." + base):
                return imp
        return f"{self.package}.{base}" if self.package else base


class KotlinParser:
    def __init__(self):
        self.parser = get_parser("kotlin")

    def parse(self, source: SourceFile) -> KotlinFile:
        tree = self.parser.parse(source.content.encode("utf-8"))
        root = tree.root_node
        package = None
        imports: list[str] = []
        for child in root.children:
            if child.type == "package_header":
                package = self._text(child, source).replace("package", "").strip()
            elif child.type == "import_list":
                for imp in child.children:
                    if imp.type == "import_header":
                        text = self._text(imp, source).replace("import", "").strip()
                        imports.append(re.sub(r"\s+as\s+\w+$", "", text).rstrip(".*"))
        kf = KotlinFile(source=source, package=package, imports=imports)
        for child in root.children:
            if child.type in ("class_declaration", "object_declaration"):
                kf.units.append(self._type_unit(child, kf, package))
            elif child.type == "function_declaration":
                kf.units.append(self._function_unit(child, kf, package or "", CodeUnitKind.FUNCTION))
        return kf

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _text(node: Node | None, source: SourceFile) -> str:
        if node is None:
            return ""
        return source.content.encode("utf-8")[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _modifiers(self, node: Node, source: SourceFile) -> tuple[list[str], list[Annotation]]:
        mods: list[str] = []
        annotations: list[Annotation] = []
        for child in node.children:
            if child.type != "modifiers":
                continue
            for m in child.children:
                if m.type == "annotation":
                    text = self._text(m, source).lstrip("@")
                    text = re.sub(r"^(?:field|get|set|param|file|property):", "", text)
                    name, _, rest = text.partition("(")
                    annotations.append(Annotation(name=name.strip().split(".")[-1], arguments=rest[:-1] if rest.endswith(")") else (rest or None)))
                else:
                    mods.append(self._text(m, source))
        return mods, annotations

    def _docstring(self, node: Node, source: SourceFile) -> str | None:
        prev = node.prev_sibling
        if prev is not None and prev.type == "multiline_comment":
            text = self._text(prev, source)
            if text.startswith("/**"):
                lines = [ln.strip().lstrip("*").strip() for ln in text.strip("/*").splitlines()]
                return "\n".join(ln for ln in lines if ln) or None
        return None

    def _first(self, node: Node, *types: str) -> Node | None:
        return next((c for c in node.children if c.type in types), None)

    def _type_unit(self, node: Node, kf: KotlinFile, parent_fqn: str | None) -> CodeUnit:
        src = kf.source
        name = self._text(self._first(node, "type_identifier"), src)
        fqn = f"{parent_fqn}.{name}" if parent_fqn else name
        mods, annotations = self._modifiers(node, src)
        is_interface = any(c.type == "interface" for c in node.children)
        is_enum = any(c.type == "enum" for c in node.children) or self._first(node, "enum_class_body") is not None
        is_data = "data" in mods
        kind = CodeUnitKind.INTERFACE if is_interface else CodeUnitKind.ENUM if is_enum else CodeUnitKind.RECORD if is_data else CodeUnitKind.CLASS
        if node.type == "object_declaration":
            mods.append("object")

        extends: list[str] = []
        implements: list[str] = []
        for child in node.children:
            if child.type == "delegation_specifier":
                inv = self._first(child, "constructor_invocation")
                type_text = self._text(self._first(inv, "user_type") if inv else self._first(child, "user_type"), src)
                target = kf.resolve(type_text) or type_text
                (extends if inv is not None or is_interface else implements).append(target)

        unit = CodeUnit(
            kind=kind,
            name=name,
            fqn=fqn,
            file=src.rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=Language.KOTLIN,
            package=kf.package,
            modifiers=mods,
            annotations=annotations,
            extends=extends,
            implements=implements,
            docstring=self._docstring(node, src),
        )
        # primary constructor properties behave like injected fields
        ctor = self._first(node, "primary_constructor")
        if ctor is not None:
            for p in ctor.children:
                if p.type != "class_parameter":
                    continue
                pname = self._text(self._first(p, "simple_identifier"), src)
                ptype = self._text(self._first(p, "user_type", "nullable_type", "function_type"), src)
                pmods, panns = self._modifiers(p, src)
                is_property = self._first(p, "binding_pattern_kind") is not None
                unit.children.append(
                    CodeUnit(
                        kind=CodeUnitKind.FIELD,
                        name=pname,
                        fqn=f"{fqn}.{pname}",
                        file=src.rel_path,
                        line_start=p.start_point[0] + 1,
                        line_end=p.end_point[0] + 1,
                        language=Language.KOTLIN,
                        package=kf.package,
                        type=ptype or None,
                        annotations=panns,
                        modifiers=pmods + (["injected"] if is_property and not is_data else []),
                    )
                )
        body = self._first(node, "class_body", "enum_class_body")
        if body is None:
            return unit
        for member in body.children:
            if member.type == "property_declaration":
                unit.children.append(self._property_unit(member, kf, fqn))
            elif member.type == "function_declaration":
                unit.children.append(self._function_unit(member, kf, fqn, CodeUnitKind.METHOD))
            elif member.type in ("class_declaration", "object_declaration", "companion_object"):
                if member.type == "companion_object":
                    inner_body = self._first(member, "class_body")
                    for inner in inner_body.children if inner_body else []:
                        if inner.type == "function_declaration":
                            unit.children.append(self._function_unit(inner, kf, fqn, CodeUnitKind.METHOD))
                        elif inner.type == "property_declaration":
                            unit.children.append(self._property_unit(inner, kf, fqn))
                else:
                    unit.children.append(self._type_unit(member, kf, fqn))
        return unit

    def _property_unit(self, node: Node, kf: KotlinFile, parent_fqn: str) -> CodeUnit:
        src = kf.source
        mods, anns = self._modifiers(node, src)
        decl = self._first(node, "variable_declaration")
        name = self._text(self._first(decl, "simple_identifier"), src) if decl else "?"
        type_node = self._first(decl, "user_type", "nullable_type", "function_type") if decl else None
        type_text = self._text(type_node, src) if type_node else None
        if type_text is None:
            # infer from constructor call: `val x = Foo()`
            call = self._first(node, "call_expression")
            if call is not None:
                type_text = self._text(call, src).split("(")[0].strip() or None
        return CodeUnit(
            kind=CodeUnitKind.FIELD,
            name=name,
            fqn=f"{parent_fqn}.{name}",
            file=src.rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=Language.KOTLIN,
            package=kf.package,
            modifiers=mods,
            annotations=anns,
            type=type_text,
        )

    def _function_unit(self, node: Node, kf: KotlinFile, parent_fqn: str, kind: CodeUnitKind) -> CodeUnit:
        src = kf.source
        name = self._text(self._first(node, "simple_identifier"), src)
        mods, anns = self._modifiers(node, src)
        params: list[Parameter] = []
        params_node = self._first(node, "function_value_parameters")
        if params_node is not None:
            for p in params_node.children:
                if p.type == "parameter":
                    params.append(
                        Parameter(
                            name=self._text(self._first(p, "simple_identifier"), src),
                            type=self._text(self._first(p, "user_type", "nullable_type", "function_type"), src) or None,
                        )
                    )
                elif p.type == "parameter_with_optional_type" or p.type == "function_value_parameter":
                    inner = self._first(p, "parameter") or p
                    params.append(
                        Parameter(
                            name=self._text(self._first(inner, "simple_identifier"), src),
                            type=self._text(self._first(inner, "user_type", "nullable_type", "function_type"), src) or None,
                        )
                    )
        ret = None
        saw_params = False
        for c in node.children:
            if c.type == "function_value_parameters":
                saw_params = True
            elif saw_params and c.type in ("user_type", "nullable_type", "function_type"):
                ret = self._text(c, src)
                break
        calls: list[str] = []
        body = self._first(node, "function_body")
        if body is not None:
            self._collect_calls(body, src, calls)
        return CodeUnit(
            kind=kind,
            name=name,
            fqn=f"{parent_fqn}.{name}" if parent_fqn else name,
            file=src.rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=Language.KOTLIN,
            package=kf.package,
            modifiers=mods,
            annotations=anns,
            type=ret,
            parameters=params,
            docstring=self._docstring(node, src),
            calls=sorted(set(calls)),
        )

    def _collect_calls(self, node: Node, src: SourceFile, out: list[str]) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "call_expression" and n.children:
                callee = n.children[0]
                text = self._text(callee, src)
                if callee.type == "navigation_expression":
                    parts = re.split(r"[?!]?\.", text)
                    if len(parts) >= 2 and re.fullmatch(r"[\w.]+", parts[-2].replace("this.", "")):
                        receiver = parts[-2].replace("this.", "")
                        out.append(f"{receiver}.{parts[-1]}")
                    elif parts:
                        out.append(parts[-1])
                elif callee.type == "simple_identifier":
                    out.append(text)
            stack.extend(n.children)

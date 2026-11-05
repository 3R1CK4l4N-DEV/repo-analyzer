from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from devanalyzer.core.models import Annotation, CodeUnit, CodeUnitKind, Language, Parameter, SourceFile

TYPE_DECLARATIONS = {
    "class_declaration": CodeUnitKind.CLASS,
    "interface_declaration": CodeUnitKind.INTERFACE,
    "enum_declaration": CodeUnitKind.ENUM,
    "record_declaration": CodeUnitKind.RECORD,
}


@dataclass
class JavaFile:
    source: SourceFile
    package: str | None
    imports: list[str]
    units: list[CodeUnit] = field(default_factory=list)

    def resolve(self, simple_name: str) -> str | None:
        """Resolve a simple type name to a FQN via explicit imports or same-package fallback."""
        base = simple_name.split("<")[0].split("[")[0].strip()
        if "." in base:
            return base
        for imp in self.imports:
            if imp.endswith("." + base):
                return imp
        return f"{self.package}.{base}" if self.package else base


class JavaParser:
    def __init__(self):
        self.parser = get_parser("java")

    def parse(self, source: SourceFile) -> JavaFile:
        tree = self.parser.parse(source.content.encode("utf-8"))
        root = tree.root_node
        package = None
        imports: list[str] = []
        for child in root.children:
            if child.type == "package_declaration":
                package = self._text(child, source).replace("package", "").strip(" ;")
            elif child.type == "import_declaration":
                imports.append(self._text(child, source).replace("import", "").replace("static", "").strip(" ;"))
        jf = JavaFile(source=source, package=package, imports=imports)
        for child in root.children:
            if child.type in TYPE_DECLARATIONS:
                jf.units.append(self._type_unit(child, jf, parent_fqn=package))
        return jf

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _text(node: Node, source: SourceFile) -> str:
        return source.content.encode("utf-8")[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _modifiers_and_annotations(self, node: Node, source: SourceFile) -> tuple[list[str], list[Annotation]]:
        mods: list[str] = []
        annotations: list[Annotation] = []
        for child in node.children:
            if child.type != "modifiers":
                continue
            for m in child.children:
                if m.type in ("marker_annotation", "annotation"):
                    name_node = m.child_by_field_name("name")
                    args_node = m.child_by_field_name("arguments")
                    annotations.append(
                        Annotation(
                            name=self._text(name_node, source).split(".")[-1] if name_node else self._text(m, source),
                            arguments=self._text(args_node, source).strip("()") if args_node else None,
                        )
                    )
                elif m.type not in ("line_comment", "block_comment"):
                    mods.append(self._text(m, source))
        return mods, annotations

    def _docstring(self, node: Node, source: SourceFile) -> str | None:
        prev = node.prev_sibling
        if prev is not None and prev.type == "block_comment":
            text = self._text(prev, source)
            if text.startswith("/**"):
                lines = [ln.strip().lstrip("*").strip() for ln in text.strip("/*").splitlines()]
                return "\n".join(ln for ln in lines if ln) or None
        return None

    def _type_list(self, node: Node | None, source: SourceFile, jf: JavaFile) -> list[str]:
        if node is None:
            return []
        names = []
        for child in node.children:
            if child.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                names.append(jf.resolve(self._text(child, source)) or self._text(child, source))
            elif child.type == "type_list":
                names.extend(self._type_list(child, source, jf))
        if not names and node.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
            names.append(jf.resolve(self._text(node, source)) or self._text(node, source))
        return names

    def _type_unit(self, node: Node, jf: JavaFile, parent_fqn: str | None) -> CodeUnit:
        source = jf.source
        name = self._text(node.child_by_field_name("name"), source)
        fqn = f"{parent_fqn}.{name}" if parent_fqn else name
        mods, annotations = self._modifiers_and_annotations(node, source)

        extends: list[str] = []
        implements: list[str] = []
        for child in node.children:
            if child.type == "superclass":
                extends += self._type_list(child, source, jf)
            elif child.type == "super_interfaces":
                implements += self._type_list(child, source, jf)
            elif child.type == "extends_interfaces":
                extends += self._type_list(child, source, jf)

        unit = CodeUnit(
            kind=TYPE_DECLARATIONS[node.type],
            name=name,
            fqn=fqn,
            file=source.rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=Language.JAVA,
            package=jf.package,
            modifiers=mods,
            annotations=annotations,
            extends=extends,
            implements=implements,
            docstring=self._docstring(node, source),
        )

        body = node.child_by_field_name("body")
        if body is None:
            return unit
        for member in body.children:
            if member.type in TYPE_DECLARATIONS:
                unit.children.append(self._type_unit(member, jf, fqn))
            elif member.type in ("method_declaration", "constructor_declaration"):
                unit.children.append(self._method_unit(member, jf, fqn))
            elif member.type == "field_declaration":
                unit.children.extend(self._field_units(member, jf, fqn))
            elif member.type == "enum_body_declarations":
                for inner in member.children:
                    if inner.type in ("method_declaration", "constructor_declaration"):
                        unit.children.append(self._method_unit(inner, jf, fqn))
                    elif inner.type == "field_declaration":
                        unit.children.extend(self._field_units(inner, jf, fqn))
        if node.type == "record_declaration":
            params = node.child_by_field_name("parameters")
            if params is not None:
                for p in params.children:
                    if p.type == "formal_parameter":
                        unit.children.append(self._record_component(p, jf, fqn))
        return unit

    def _record_component(self, node: Node, jf: JavaFile, parent_fqn: str) -> CodeUnit:
        source = jf.source
        name = self._text(node.child_by_field_name("name"), source)
        type_node = node.child_by_field_name("type")
        return CodeUnit(
            kind=CodeUnitKind.FIELD,
            name=name,
            fqn=f"{parent_fqn}.{name}",
            file=source.rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=Language.JAVA,
            package=jf.package,
            type=self._text(type_node, source) if type_node else None,
        )

    def _field_units(self, node: Node, jf: JavaFile, parent_fqn: str) -> list[CodeUnit]:
        source = jf.source
        mods, annotations = self._modifiers_and_annotations(node, source)
        type_node = node.child_by_field_name("type")
        type_text = self._text(type_node, source) if type_node else None
        units = []
        for decl in node.children:
            if decl.type != "variable_declarator":
                continue
            name = self._text(decl.child_by_field_name("name"), source)
            units.append(
                CodeUnit(
                    kind=CodeUnitKind.FIELD,
                    name=name,
                    fqn=f"{parent_fqn}.{name}",
                    file=source.rel_path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    language=Language.JAVA,
                    package=jf.package,
                    modifiers=mods,
                    annotations=annotations,
                    type=type_text,
                )
            )
        return units

    def _method_unit(self, node: Node, jf: JavaFile, parent_fqn: str) -> CodeUnit:
        source = jf.source
        name = self._text(node.child_by_field_name("name"), source)
        mods, annotations = self._modifiers_and_annotations(node, source)
        type_node = node.child_by_field_name("type")
        params: list[Parameter] = []
        params_node = node.child_by_field_name("parameters")
        if params_node is not None:
            for p in params_node.children:
                if p.type in ("formal_parameter", "spread_parameter"):
                    pname = p.child_by_field_name("name")
                    ptype = p.child_by_field_name("type")
                    params.append(
                        Parameter(
                            name=self._text(pname, source) if pname else "",
                            type=self._text(ptype, source) if ptype else None,
                        )
                    )
        calls: list[str] = []
        body = node.child_by_field_name("body")
        if body is not None:
            self._collect_calls(body, source, calls)
        return CodeUnit(
            kind=CodeUnitKind.METHOD,
            name=name,
            fqn=f"{parent_fqn}.{name}",
            file=source.rel_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=Language.JAVA,
            package=jf.package,
            modifiers=mods,
            annotations=annotations,
            type=self._text(type_node, source) if type_node else None,
            parameters=params,
            docstring=self._docstring(node, source),
            calls=sorted(set(calls)),
        )

    def _collect_calls(self, node: Node, source: SourceFile, out: list[str]) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "method_invocation":
                obj = n.child_by_field_name("object")
                name = n.child_by_field_name("name")
                if name is not None:
                    receiver = self._text(obj, source) if obj is not None and obj.type in ("identifier", "field_access", "this") else None
                    out.append(f"{receiver}.{self._text(name, source)}" if receiver else self._text(name, source))
            stack.extend(n.children)

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from devanalyzer.core.models import Annotation, CodeUnit, CodeUnitKind, Endpoint, Language, Parameter, SourceFile

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}
ROUTE_DECORATORS = {"route", "api_route", "websocket"}


@dataclass
class ImportSpec:
    module: str  # absolute or relative module ("" for `from . import x`)
    names: dict[str, str]  # local alias -> original name
    level: int = 0


@dataclass
class PyFile:
    source: SourceFile
    module: str
    imports: list[ImportSpec] = field(default_factory=list)
    units: list[CodeUnit] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    router_prefixes: dict[str, str] = field(default_factory=dict)
    router_frameworks: dict[str, str] = field(default_factory=dict)

    def resolve_name(self, name: str, project_modules: set[str]) -> str | None:
        """Resolve a local identifier to `<module>.<name>` if it is imported from a project module."""
        base = name.split(".")[0]
        for imp in self.imports:
            if base not in imp.names:
                continue
            original = imp.names[base]
            target_module = self._absolute(imp)
            candidates = [f"{target_module}.{original}" if target_module else original]
            # `import pkg.mod` / `from pkg import mod` styles
            if target_module and f"{target_module}.{original}" in project_modules:
                candidates.append(f"{target_module}.{original}.{'.'.join(name.split('.')[1:])}" if "." in name else "")
            for c in candidates:
                if not c:
                    continue
                mod, _, attr = c.rpartition(".")
                if mod in project_modules:
                    return c
        return None

    def _absolute(self, imp: ImportSpec) -> str:
        if imp.level == 0:
            return imp.module
        parts = self.module.split(".")
        # relative imports resolve from the package (strip module name; __init__ keeps package)
        base = parts if parts[-1] == "__init__" else parts[:-1]
        base = base[: len(base) - (imp.level - 1)] if imp.level > 1 else base
        return ".".join(base + ([imp.module] if imp.module else []))


def package_dirs(rel_paths: list[str]) -> set[str]:
    """Directories (posix, '' for root) that contain an __init__.py."""
    return {p.rpartition("/")[0] for p in rel_paths if p.rsplit("/", 1)[-1] == "__init__.py"}


def module_name(rel_path: str, packages: set[str] | None = None) -> str:
    path = re.sub(r"\.py$", "", rel_path.replace("\\", "/"))
    parts = [p for p in path.split("/") if p]
    if packages is None:
        return ".".join(parts)
    # Walk up from the file's directory while directories are packages; the module is relative to the top package's parent.
    dirs = parts[:-1]
    top = len(dirs)
    while top > 0 and "/".join(dirs[:top]) in packages:
        top -= 1
    return ".".join(parts[top:])


class PythonParser:
    def __init__(self, packages: set[str] | None = None):
        self.packages = packages

    def parse(self, source: SourceFile) -> PyFile:
        pf = PyFile(source=source, module=module_name(source.rel_path, self.packages))
        try:
            tree = ast.parse(source.content, filename=source.rel_path)
        except SyntaxError:
            return pf
        lines = source.content.splitlines()
        self._collect_imports(tree, pf)
        self._collect_routers(tree, pf)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                pf.units.append(self._class_unit(node, pf, pf.module))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                unit = self._function_unit(node, pf, pf.module, CodeUnitKind.FUNCTION)
                pf.units.append(unit)
                pf.endpoints.extend(self._endpoints_from_decorators(unit, pf))
        if not pf.units and lines:
            pf.units.append(
                CodeUnit(
                    kind=CodeUnitKind.MODULE,
                    name=pf.module.rsplit(".", 1)[-1],
                    fqn=pf.module,
                    file=source.rel_path,
                    line_start=1,
                    line_end=max(1, len(lines)),
                    language=Language.PYTHON,
                    package=pf.module.rpartition(".")[0] or None,
                    docstring=ast.get_docstring(tree),
                )
            )
        return pf

    # ------------------------------------------------------------ imports / routers
    def _collect_imports(self, tree: ast.Module, pf: PyFile) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    pf.imports.append(ImportSpec(module=alias.name.rpartition(".")[0], names={local: alias.name.rpartition(".")[2]}))
            elif isinstance(node, ast.ImportFrom):
                pf.imports.append(ImportSpec(module=node.module or "", names={(a.asname or a.name): a.name for a in node.names}, level=node.level))

    def _collect_routers(self, tree: ast.Module, pf: PyFile) -> None:
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            callee = _dotted(node.value.func)
            if callee and callee.split(".")[-1] in ("APIRouter", "FastAPI", "Flask", "Blueprint", "Router"):
                prefix = ""
                for kw in node.value.keywords:
                    if kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant):
                        prefix = str(kw.value.value)
                framework = "Flask" if callee.split(".")[-1] in ("Flask", "Blueprint") else "FastAPI"
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        pf.router_prefixes[target.id] = prefix
                        pf.router_frameworks[target.id] = framework

    # ------------------------------------------------------------ units
    def _class_unit(self, node: ast.ClassDef, pf: PyFile, parent: str) -> CodeUnit:
        fqn = f"{parent}.{node.name}"
        bases = [_dotted(b) or ast.unparse(b) for b in node.bases]
        unit = CodeUnit(
            kind=CodeUnitKind.CLASS,
            name=node.name,
            fqn=fqn,
            file=pf.source.rel_path,
            line_start=min([node.lineno] + [d.lineno for d in node.decorator_list]),
            line_end=node.end_lineno or node.lineno,
            language=Language.PYTHON,
            package=pf.module,
            annotations=[self._decorator(d) for d in node.decorator_list],
            extends=bases,
            docstring=ast.get_docstring(node),
        )
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method = self._function_unit(member, pf, fqn, CodeUnitKind.METHOD)
                unit.children.append(method)
                pf.endpoints.extend(self._endpoints_from_decorators(method, pf))
            elif isinstance(member, ast.ClassDef):
                unit.children.append(self._class_unit(member, pf, fqn))
            elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                unit.children.append(
                    CodeUnit(
                        kind=CodeUnitKind.FIELD,
                        name=member.target.id,
                        fqn=f"{fqn}.{member.target.id}",
                        file=pf.source.rel_path,
                        line_start=member.lineno,
                        line_end=member.end_lineno or member.lineno,
                        language=Language.PYTHON,
                        type=ast.unparse(member.annotation),
                    )
                )
            elif isinstance(member, ast.Assign):
                for t in member.targets:
                    if isinstance(t, ast.Name):
                        unit.children.append(
                            CodeUnit(
                                kind=CodeUnitKind.FIELD,
                                name=t.id,
                                fqn=f"{fqn}.{t.id}",
                                file=pf.source.rel_path,
                                line_start=member.lineno,
                                line_end=member.end_lineno or member.lineno,
                                language=Language.PYTHON,
                                type=_dotted(member.value.func) if isinstance(member.value, ast.Call) else None,
                            )
                        )
        # attributes assigned in __init__ (self.x: T = ... / self.x = Dep())
        init = next((m for m in node.body if isinstance(m, ast.FunctionDef) and m.name == "__init__"), None)
        if init is not None:
            known = {c.name for c in unit.children if c.kind == CodeUnitKind.FIELD}
            param_types = {a.arg: ast.unparse(a.annotation) for a in init.args.args + init.args.kwonlyargs if a.annotation}
            for stmt in ast.walk(init):
                target = None
                type_text = None
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Attribute) and _is_self(stmt.target):
                    target, type_text = stmt.target.attr, ast.unparse(stmt.annotation)
                elif isinstance(stmt, ast.Assign):
                    for t in stmt.targets:
                        if isinstance(t, ast.Attribute) and _is_self(t):
                            target = t.attr
                            if isinstance(stmt.value, ast.Name):
                                type_text = param_types.get(stmt.value.id)
                            elif isinstance(stmt.value, ast.Call):
                                type_text = _dotted(stmt.value.func)
                if target and target not in known:
                    known.add(target)
                    unit.children.append(
                        CodeUnit(
                            kind=CodeUnitKind.FIELD,
                            name=target,
                            fqn=f"{fqn}.{target}",
                            file=pf.source.rel_path,
                            line_start=stmt.lineno,
                            line_end=stmt.end_lineno or stmt.lineno,
                            language=Language.PYTHON,
                            type=type_text,
                            modifiers=["injected"] if type_text in param_types.values() else [],
                        )
                    )
        return unit

    def _function_unit(self, node: ast.FunctionDef | ast.AsyncFunctionDef, pf: PyFile, parent: str, kind: CodeUnitKind) -> CodeUnit:
        params = []
        args = node.args
        all_args = args.posonlyargs + args.args + args.kwonlyargs
        for a in all_args:
            if a.arg in ("self", "cls"):
                continue
            params.append(Parameter(name=a.arg, type=ast.unparse(a.annotation) if a.annotation else None))
        if args.vararg:
            params.append(Parameter(name=f"*{args.vararg.arg}", type=ast.unparse(args.vararg.annotation) if args.vararg.annotation else None))
        if args.kwarg:
            params.append(Parameter(name=f"**{args.kwarg.arg}", type=ast.unparse(args.kwarg.annotation) if args.kwarg.annotation else None))
        calls: list[str] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = _dotted(sub.func)
                if name:
                    calls.append(name[5:] if name.startswith("self.") else name)
        mods = ["async"] if isinstance(node, ast.AsyncFunctionDef) else []
        for d in node.decorator_list:
            dn = _dotted(d if not isinstance(d, ast.Call) else d.func)
            if dn in ("staticmethod", "classmethod", "property", "abstractmethod"):
                mods.append(dn)
        return CodeUnit(
            kind=kind,
            name=node.name,
            fqn=f"{parent}.{node.name}",
            file=pf.source.rel_path,
            line_start=min([node.lineno] + [d.lineno for d in node.decorator_list]),
            line_end=node.end_lineno or node.lineno,
            language=Language.PYTHON,
            package=pf.module if kind == CodeUnitKind.FUNCTION else None,
            modifiers=mods,
            annotations=[self._decorator(d) for d in node.decorator_list],
            type=ast.unparse(node.returns) if node.returns else None,
            parameters=params,
            docstring=ast.get_docstring(node),
            calls=sorted(set(calls)),
        )

    # ------------------------------------------------------------ endpoints
    def _endpoints_from_decorators(self, unit: CodeUnit, pf: PyFile) -> list[Endpoint]:
        endpoints = []
        for ann in unit.annotations:
            receiver, _, attr = ann.name.rpartition(".")
            if attr in HTTP_METHODS or attr in ROUTE_DECORATORS:
                path = _first_string(ann.arguments)
                if path is None:
                    continue
                methods = [attr.upper()] if attr in HTTP_METHODS else _methods_kwarg(ann.arguments) or ["GET"]
                prefix = pf.router_prefixes.get(receiver, "")
                framework = pf.router_frameworks.get(receiver, "FastAPI" if attr == "api_route" else "Flask")
                for m in methods:
                    endpoints.append(
                        Endpoint(http_method=m, path=_join(prefix, path), handler=unit.fqn, file=unit.file, line=unit.line_start, framework=framework)
                    )
        return endpoints

    @staticmethod
    def _decorator(node: ast.expr) -> Annotation:
        if isinstance(node, ast.Call):
            return Annotation(
                name=_dotted(node.func) or ast.unparse(node.func),
                arguments=", ".join([ast.unparse(a) for a in node.args] + [f"{k.arg}={ast.unparse(k.value)}" for k in node.keywords]),
            )
        return Annotation(name=_dotted(node) or ast.unparse(node))


# ---------------------------------------------------------------- helpers
def _dotted(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Subscript):
        return _dotted(node.value)
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return None


def _is_self(node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "self"


def _first_string(args: str | None) -> str | None:
    if not args:
        return None
    m = re.match(r"""\s*(?:path\s*=\s*)?['"]([^'"]*)['"]""", args)
    return m.group(1) if m else None


def _methods_kwarg(args: str | None) -> list[str]:
    if not args:
        return []
    m = re.search(r"methods\s*=\s*[\[(]([^\])]*)[\])]", args)
    return [s.strip().strip("'\"").upper() for s in m.group(1).split(",") if s.strip()] if m else []


def _join(prefix: str, path: str) -> str:
    joined = f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix else path
    return joined if joined.startswith("/") else "/" + joined

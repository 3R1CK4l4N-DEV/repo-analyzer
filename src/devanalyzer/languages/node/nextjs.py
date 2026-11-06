"""Next.js file-system routing.

App Router:  app/**/route.ts exports GET/POST/...; app/**/page.tsx renders a page (GET).
Pages Router: pages/api/**/*.ts default export handler (method via req.method checks); pages/**/*.tsx pages.
Dynamic segments: [id] -> {id}, [...slug] -> {slug*}, route groups (group) are dropped.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from tree_sitter_language_pack import get_parser

from devanalyzer.core.models import Endpoint, SourceFile
from devanalyzer.core.source_text import without_comments
from devanalyzer.languages.node.parser import module_id

HTTP = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
METHOD_CHECK_RE = re.compile(
    r"""req(?:uest)?\.method\s*(?:===?|!==?)\s*["'](GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)["']|case\s+["'](GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)["']"""
)
CONFIG_NAMES = {"next.config.js", "next.config.mjs", "next.config.ts"}
IGNORED_DIRS = {"node_modules", ".next", "dist", "build", "out"}


def _project_roots(files: list[SourceFile]) -> tuple[set[PurePosixPath], set[PurePosixPath]]:
    roots, packages = set(), set()
    for f in files:
        path = PurePosixPath(f.rel_path)
        if IGNORED_DIRS.intersection(path.parts):
            continue
        if f.name in CONFIG_NAMES:
            roots.add(path.parent)
        if f.name != "package.json":
            continue
        packages.add(path.parent)
        try:
            data = json.loads(f.content)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and any(
            isinstance(data.get(key), dict) and "next" in data[key] for key in ("dependencies", "devDependencies", "peerDependencies")
        ):
            roots.add(path.parent)
    return roots, packages


def is_next_project(files: list[SourceFile]) -> bool:
    return bool(_project_roots(files)[0])


def _route_from_path(rel: str, root: str) -> str:
    parts = rel.split("/")
    idx = parts.index(root)
    segs = parts[idx + 1 : -1]  # drop file name
    out = []
    for s in segs:
        if s.startswith("(") and s.endswith(")"):
            continue  # route group
        if s.startswith("@"):
            continue  # parallel route slot
        s = re.sub(r"^\[\[\.\.\.(\w+)\]\]$", r"{\1*}", s)
        s = re.sub(r"^\[\.\.\.(\w+)\]$", r"{\1*}", s)
        s = re.sub(r"^\[(\w+)\]$", r"{\1}", s)
        out.append(s)
    return "/" + "/".join(out) if out else "/"


def _exports(source: SourceFile) -> dict[str, tuple[str, int]]:
    """Exported name -> local handler and export line. Comments/strings are never exports."""
    grammar = "tsx" if source.extension == ".tsx" else "typescript" if source.extension == ".ts" else "javascript"
    raw = source.content.encode("utf-8")
    tree = get_parser(grammar).parse(raw)

    def text(node):
        return raw[node.start_byte : node.end_byte].decode("utf-8") if node is not None else ""

    exports = {}
    for node in tree.root_node.named_children:
        if node.type != "export_statement":
            continue
        line = node.start_point[0] + 1
        declaration = node.child_by_field_name("declaration")
        if any(c.type == "default" for c in node.children):
            value = declaration or node.child_by_field_name("value")
            name = text(value.child_by_field_name("name")) if value else ""
            if value and value.type == "identifier":
                name = text(value)
            exports["default"] = (name or "default", line)
        elif declaration is not None:
            if declaration.type in ("function_declaration", "generator_function_declaration"):
                name = text(declaration.child_by_field_name("name"))
                exports[name] = (name, line)
            elif declaration.type in ("lexical_declaration", "variable_declaration"):
                for child in declaration.named_children:
                    name = child.child_by_field_name("name")
                    if name is not None and name.type == "identifier":
                        exports[text(name)] = (text(name), line)
        else:
            clause = next((c for c in node.named_children if c.type == "export_clause"), None)
            for spec in clause.named_children if clause else []:
                name = text(spec.child_by_field_name("name"))
                alias = text(spec.child_by_field_name("alias")) or name
                exports[alias] = (name, line)
    return exports


def extract_next_endpoints(files: list[SourceFile]) -> list[Endpoint]:
    roots, packages = _project_roots(files)
    if not roots:
        return []
    endpoints: list[Endpoint] = []
    for f in files:
        rel = f.rel_path
        path_obj = PurePosixPath(rel)
        if IGNORED_DIRS.intersection(path_obj.parts) or f.extension not in (".js", ".jsx", ".ts", ".tsx") or f.name.endswith(".d.ts"):
            continue
        # Routing belongs to the nearest package/config, not any directory named "app".
        project = next((p for p in path_obj.parents if p in roots or p in packages), None)
        if project not in roots:
            continue
        local = path_obj.relative_to(project).as_posix()
        if local.startswith("src/"):
            local = local[4:]
        parts = local.split("/")
        if len(parts) < 2 or parts[0] not in ("app", "pages"):
            continue
        stem = f.name.rsplit(".", 1)[0]
        exports = _exports(f)
        if parts[0] == "app" and stem in ("route", "page"):
            if any(p.startswith("_") for p in parts[1:-1]):
                continue
            path = _route_from_path(local, "app")
            if stem == "route":
                for m in sorted(set(exports) & set(HTTP)):
                    name, line = exports[m]
                    endpoints.append(Endpoint(http_method=m, path=path, handler=f"{module_id(rel)}.{name}", file=rel, line=line, framework="Next.js (route)"))
            elif "default" in exports:
                name, line = exports["default"]
                endpoints.append(Endpoint(http_method="GET", path=path, handler=f"{module_id(rel)}.{name}", file=rel, line=line, framework="Next.js (page)"))
        elif parts[0] == "pages":
            if (len(parts) == 2 and stem in ("_app", "_document", "_error")) or "default" not in exports:
                continue
            root = "pages"
            segs = parts[1:]
            route_parts = segs[:-1] + ([] if stem == "index" else [stem])
            path = _route_from_path("/".join([root] + route_parts + ["x"]), root)
            is_api = segs[0] == "api"
            name, line = exports["default"]
            if is_api:
                methods = sorted({m1 or m2 for m1, m2 in METHOD_CHECK_RE.findall(without_comments(f.content, f.language, f.extension))})
                for m in methods or ["ANY"]:
                    endpoints.append(Endpoint(http_method=m, path=path, handler=f"{module_id(rel)}.{name}", file=rel, line=line, framework="Next.js (api)"))
            else:
                endpoints.append(Endpoint(http_method="GET", path=path, handler=f"{module_id(rel)}.{name}", file=rel, line=line, framework="Next.js (page)"))
    return endpoints

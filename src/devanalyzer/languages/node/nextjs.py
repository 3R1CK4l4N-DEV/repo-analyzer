"""Next.js file-system routing.

App Router:  app/**/route.ts exports GET/POST/...; app/**/page.tsx renders a page (GET).
Pages Router: pages/api/**/*.ts default export handler (method via req.method checks); pages/**/*.tsx pages.
Dynamic segments: [id] -> {id}, [...slug] -> {slug*}, route groups (group) are dropped.
"""

from __future__ import annotations

import re

from devanalyzer.core.models import Endpoint, SourceFile

HTTP = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
EXPORT_HANDLER_RE = re.compile(
    r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b|export\s+const\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*="
)
METHOD_CHECK_RE = re.compile(r"""req(?:uest)?\.method\s*(?:===?|!==?)\s*["'](GET|POST|PUT|PATCH|DELETE)["']|case\s+["'](GET|POST|PUT|PATCH|DELETE)["']""")


def is_next_project(files: list[SourceFile]) -> bool:
    return any(f.name in ("next.config.js", "next.config.mjs", "next.config.ts") for f in files) or any(
        f.name == "package.json" and '"next"' in f.content and "node_modules" not in f.rel_path for f in files
    )


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


def _module_id(rel: str) -> str:
    return re.sub(r"\.(m|c)?(j|t)sx?$", "", rel)


def extract_next_endpoints(files: list[SourceFile]) -> list[Endpoint]:
    if not is_next_project(files):
        return []
    endpoints: list[Endpoint] = []
    for f in files:
        rel = f.rel_path
        if "node_modules" in rel or f.language is None:
            continue
        parts = rel.split("/")
        stem = f.name.rsplit(".", 1)[0]
        if "app" in parts and stem in ("route", "page"):
            root = "app"
            path = _route_from_path(rel, root)
            if stem == "route":
                methods = sorted({m1 or m2 for m1, m2 in EXPORT_HANDLER_RE.findall(f.content)})
                for m in methods or ["ANY"]:
                    line = next((i + 1 for i, ln in enumerate(f.content.splitlines()) if re.search(rf"\b{m}\b", ln) and "export" in ln), 1)
                    endpoints.append(Endpoint(http_method=m, path=path, handler=f"{_module_id(rel)}.{m}", file=rel, line=line, framework="Next.js (route)"))
            else:
                endpoints.append(Endpoint(http_method="GET", path=path, handler=f"{_module_id(rel)}.default", file=rel, line=1, framework="Next.js (page)"))
        elif "pages" in parts:
            if stem.startswith("_") or stem in ("layout", "loading", "error", "not-found", "template"):
                continue
            root = "pages"
            idx = parts.index(root)
            segs = parts[idx + 1 :]
            route_parts = segs[:-1] + ([] if stem == "index" else [stem])
            path = _route_from_path("/".join([root] + route_parts + ["x"]), root)
            is_api = len(segs) > 0 and segs[0] == "api"
            if is_api:
                methods = sorted({m1 or m2 for m1, m2 in METHOD_CHECK_RE.findall(f.content)})
                for m in methods or ["ANY"]:
                    endpoints.append(Endpoint(http_method=m, path=path, handler=f"{_module_id(rel)}.default", file=rel, line=1, framework="Next.js (api)"))
            elif f.extension in (".tsx", ".jsx"):
                endpoints.append(Endpoint(http_method="GET", path=path, handler=f"{_module_id(rel)}.default", file=rel, line=1, framework="Next.js (page)"))
    return endpoints

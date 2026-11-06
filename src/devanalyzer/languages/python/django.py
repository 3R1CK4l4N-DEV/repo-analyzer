"""Django heuristics: URL routing (urls.py), class-based views, DRF routers, models.

Endpoints come from `urlpatterns` rather than decorators, so they are resolved at project level:
`path("owners/<int:pk>/", views.OwnerDetail.as_view())` + `include("owners.urls")` prefixes.
"""

from __future__ import annotations

import ast
import re

from devanalyzer.core.models import CodeUnit, CodeUnitKind, Endpoint, SourceFile
from devanalyzer.languages.python.parser import PyFile

DJANGO_VIEW_BASES = (
    "View",
    "TemplateView",
    "ListView",
    "DetailView",
    "CreateView",
    "UpdateView",
    "DeleteView",
    "FormView",
    "APIView",
    "GenericAPIView",
    "ViewSet",
    "ModelViewSet",
    "ReadOnlyModelViewSet",
    "GenericViewSet",
)
HTTP_METHOD_NAMES = {"get", "post", "put", "patch", "delete", "head", "options"}
VIEWSET_ACTIONS = {"list": "GET", "create": "POST", "retrieve": "GET", "update": "PUT", "partial_update": "PATCH", "destroy": "DELETE"}


def is_django_project(files: list[SourceFile]) -> bool:
    return any(f.name == "manage.py" or f.name == "settings.py" and "INSTALLED_APPS" in f.content for f in files) or any(f.name == "urls.py" for f in files)


def _django_path(pattern: str) -> str:
    # <int:pk> -> {pk}; regex groups (?P<pk>\d+) -> {pk}
    p = re.sub(r"<(?:[\w.]+:)?(\w+)>", r"{\1}", pattern)
    p = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", p)
    p = p.replace("^", "").replace("$", "").replace("\\", "")
    return "/" + p.strip("/") if p.strip("/") else "/"


def _join(prefix: str, path: str) -> str:
    joined = f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix and prefix != "/" else path
    return joined if joined.startswith("/") else "/" + joined


def _view_ref(node: ast.expr) -> str | None:
    """`views.OwnerDetail.as_view()` -> 'views.OwnerDetail'; `views.index` -> 'views.index'."""
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "as_view":
            return _dotted(fn.value)
        return _dotted(fn)
    return _dotted(node)


def _dotted(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _str(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def parse_urls(pf: PyFile) -> tuple[list[tuple[str, str, int]], list[tuple[str, str]], list[tuple[str, str, int]]]:
    """Return (routes, includes, router_registrations) for a urls.py module.
    routes: (pattern, view_ref, line); includes: (prefix, module); registrations: (prefix, viewset_ref, line)."""
    try:
        tree = ast.parse(pf.source.content)
    except SyntaxError:
        return [], [], []
    routes, includes, registrations = [], [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func) or ""
        short = name.rsplit(".", 1)[-1]
        if short in ("path", "re_path", "url") and len(node.args) >= 2 and (pattern := _str(node.args[0])) is not None:
            target = node.args[1]
            if isinstance(target, ast.Call) and (_dotted(target.func) or "").rsplit(".", 1)[-1] == "include":
                inc = target.args[0] if target.args else None
                mod = _str(inc) or (_str(inc.elts[0]) if isinstance(inc, ast.Tuple) and inc.elts else None)
                if mod:
                    includes.append((_django_path(pattern), mod))
            elif (ref := _view_ref(target)) is not None:
                routes.append((_django_path(pattern), ref, node.lineno))
        elif short == "register" and len(node.args) >= 2 and (prefix := _str(node.args[0])) is not None and (ref := _dotted(node.args[1])):
            registrations.append((_django_path(prefix), ref, node.lineno))
    return routes, includes, registrations


def resolve_django_endpoints(parsed: list[PyFile], units_by_fqn: dict[str, CodeUnit]) -> list[Endpoint]:
    url_modules = {pf.module: pf for pf in parsed if pf.module.endswith("urls") or pf.source.name == "urls.py"}
    if not url_modules:
        return []
    parsed_urls = {mod: parse_urls(pf) for mod, pf in url_modules.items()}

    def resolve_module(name: str) -> str | None:
        if name in url_modules:
            return name
        matches = [m for m in url_modules if m.endswith("." + name) or name.endswith("." + m)]
        return matches[0] if len(matches) == 1 else None

    included = {resolve_module(inc_mod) for _, incs, _ in parsed_urls.values() for _, inc_mod in incs}
    roots = [m for m in url_modules if m not in included]
    mounts: list[tuple[str, str]] = []
    seen_mounts: set[tuple[str, str]] = set()

    def mount(mod: str, prefix: str, ancestors: set[str]) -> None:
        if mod in ancestors or (mod, prefix) in seen_mounts:
            return
        seen_mounts.add((mod, prefix))
        mounts.append((mod, prefix))
        for inc_prefix, inc_mod in parsed_urls[mod][1]:
            if target := resolve_module(inc_mod):
                mount(target, _join(prefix, inc_prefix), ancestors | {mod})

    for root in roots or list(url_modules):
        mount(root, "", set())
    endpoints: list[Endpoint] = []
    for mod, base in mounts:
        routes, _, registrations = parsed_urls[mod]
        pf = url_modules[mod]
        for pattern, ref, line in routes:
            handler = _resolve_view(ref, pf, units_by_fqn)
            unit = units_by_fqn.get(handler) if handler else None
            methods = _methods_for(unit)
            for m in methods:
                endpoints.append(
                    Endpoint(http_method=m, path=_join(base, pattern), handler=handler or ref, file=pf.source.rel_path, line=line, framework="Django")
                )
        for prefix, ref, line in registrations:
            handler = _resolve_view(ref, pf, units_by_fqn) or ref
            unit = units_by_fqn.get(handler)
            actions = {c.name for c in unit.children if c.kind == CodeUnitKind.METHOD} if unit else set()
            bases = {b.rsplit(".", 1)[-1] for b in unit.extends} if unit else set()
            if "ModelViewSet" in bases:
                actions.update(VIEWSET_ACTIONS)
            elif "ReadOnlyModelViewSet" in bases:
                actions.update(("list", "retrieve"))
            for action, verb in VIEWSET_ACTIONS.items():
                if action in actions:
                    path = _join(base, prefix) if action in ("list", "create") else _join(_join(base, prefix), "{pk}")
                    endpoints.append(
                        Endpoint(http_method=verb, path=path, handler=f"{handler}.{action}", file=pf.source.rel_path, line=line, framework="Django REST")
                    )
    return endpoints


def _methods_for(unit: CodeUnit | None) -> list[str]:
    if unit is None:
        return ["ANY"]
    if unit.kind == CodeUnitKind.FUNCTION:
        for a in unit.annotations:
            if a.name.rsplit(".", 1)[-1] == "require_http_methods" and a.arguments:
                found = re.findall(r"[A-Z]+", a.arguments)
                if found:
                    return found
            if a.name.rsplit(".", 1)[-1] in ("require_GET", "require_POST"):
                return [a.name.rsplit("_", 1)[-1]]
            if a.name.rsplit(".", 1)[-1] == "api_view" and a.arguments:
                return re.findall(r"[A-Z]+", a.arguments) or ["GET"]
        return ["ANY"]
    methods = sorted(c.name.upper() for c in unit.children if c.kind == CodeUnitKind.METHOD and c.name in HTTP_METHOD_NAMES)
    if methods:
        return methods
    bases = {b.rsplit(".", 1)[-1] for b in unit.extends}
    if bases & {"ListView", "DetailView", "TemplateView"}:
        return ["GET"]
    if bases & {"CreateView", "FormView"}:
        return ["GET", "POST"]
    if bases & {"UpdateView"}:
        return ["GET", "POST"]
    if bases & {"DeleteView"}:
        return ["GET", "POST"]
    return ["ANY"]


def _resolve_view(ref: str, pf: PyFile, units_by_fqn: dict[str, CodeUnit]) -> str | None:
    if ref in units_by_fqn:
        return ref
    first, _, rest = ref.partition(".")
    for imp in pf.imports:
        if first not in imp.names:
            continue
        module = pf.absolute_module(imp)
        original = imp.names[first]
        candidates = [f"{module}.{original}.{rest}" if rest else f"{module}.{original}", f"{original}.{rest}" if rest else original]
        if module:
            candidates.append(f"{module}.{rest}" if rest else module)
        for c in candidates:
            c = c.rstrip(".")
            if c in units_by_fqn:
                return c
    # same package fallback: views.X where views is sibling module
    pkg = pf.module.rpartition(".")[0]
    for c in (f"{pkg}.{ref}", ref):
        if c in units_by_fqn:
            return c
    return None


def classify_django(unit: CodeUnit) -> str | None:
    bases = {b.rsplit(".", 1)[-1] for b in unit.extends}
    if bases & set(DJANGO_VIEW_BASES):
        return "controller"
    if "Model" in bases or any(b.endswith("models.Model") for b in unit.extends):
        return "entity"
    if bases & {"ModelSerializer", "Serializer", "ModelForm", "Form"}:
        return "entity"
    if bases & {"Manager", "QuerySet"}:
        return "repository"
    return None

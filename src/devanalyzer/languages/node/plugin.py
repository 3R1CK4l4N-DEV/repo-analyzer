from __future__ import annotations

from devanalyzer.config import Settings
from devanalyzer.core.metrics import compute_metrics
from devanalyzer.core.models import BuildInfo, CodeUnitKind, Language, LanguageAnalysis, SourceFile
from devanalyzer.core.parse_cache import ParseCache
from devanalyzer.core.plugin import is_test_path, language_ratio
from devanalyzer.languages.node.frameworks import TYPE_KINDS, apply_stereotypes, extract_nest_endpoints, extract_relations
from devanalyzer.languages.node.nextjs import extract_next_endpoints
from devanalyzer.languages.node.parser import NodeParser, module_id
from devanalyzer.languages.node.tsconfig import path_aliases

NODE_LANGUAGES = (Language.JAVASCRIPT, Language.TYPESCRIPT)
SKIP_SUFFIXES = (".d.ts", ".min.js")


class NodePlugin:
    language = Language.JAVASCRIPT

    def __init__(self, *, incremental: bool = False):
        self._cache = ParseCache() if incremental else None

    def detect(self, files: list[SourceFile]) -> float:
        ratio = language_ratio(files, *NODE_LANGUAGES)
        has_build = any(f.name.lower() == "package.json" and "node_modules" not in f.rel_path for f in files)
        if ratio == 0 and not has_build:
            return 0.0
        return min(1.0, ratio * 0.8 + (0.2 if has_build else 0.0))

    def analyze(self, files: list[SourceFile], builds: list[BuildInfo], settings: Settings) -> LanguageAnalysis:
        code = [f for f in files if f.language in NODE_LANGUAGES and not f.name.endswith(SKIP_SUFFIXES) and not is_test_path(f.rel_path)]
        parser = NodeParser()
        parsed = self._cache.parse(code, parser.parse) if self._cache is not None else [parser.parse(f) for f in code]
        project_modules = {module_id(f.rel_path) for f in code}
        aliases = path_aliases(files)

        units = [u for nf in parsed for u in nf.units]
        apply_stereotypes(units)
        # Functional code imports and calls functions, not classes: they are graph nodes too.
        project_types = {u.fqn for u in units if u.kind in TYPE_KINDS or u.kind == CodeUnitKind.FUNCTION}

        endpoints = [e for u in units for e in extract_nest_endpoints(u)] + [e for nf in parsed for e in nf.endpoints] + extract_next_endpoints(files)
        # Route handlers written as plain functions (Next.js route.ts, Express) are the controller layer of functional code.
        handlers = {e.handler for e in endpoints if e.framework != "Next.js (page)"}
        for u in units:
            if u.kind == CodeUnitKind.FUNCTION and u.fqn in handlers:
                u.stereotype = "controller"
        relations = [r for nf in parsed for r in extract_relations(nf, project_modules, project_types, aliases)]
        ts_files = sum(1 for f in code if f.language == Language.TYPESCRIPT)
        return LanguageAnalysis(
            language=Language.TYPESCRIPT if ts_files >= len(code) / 2 and code else Language.JAVASCRIPT,
            confidence=self.detect(files),
            files=len(code),
            loc=sum(f.loc for f in code),
            units=units,
            endpoints=endpoints,
            relations=relations,
            metrics=compute_metrics(code, units, relations),
        )

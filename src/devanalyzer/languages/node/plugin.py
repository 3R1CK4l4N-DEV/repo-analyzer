from __future__ import annotations

from devanalyzer.config import Settings
from devanalyzer.core.metrics import compute_metrics
from devanalyzer.core.models import BuildInfo, Language, LanguageAnalysis, SourceFile
from devanalyzer.core.plugin import language_ratio
from devanalyzer.languages.node.frameworks import TYPE_KINDS, apply_stereotypes, extract_nest_endpoints, extract_relations
from devanalyzer.languages.node.nextjs import extract_next_endpoints
from devanalyzer.languages.node.parser import NodeParser, module_id

NODE_LANGUAGES = (Language.JAVASCRIPT, Language.TYPESCRIPT)
SKIP_SUFFIXES = (".d.ts", ".min.js", ".spec.ts", ".spec.js", ".test.ts", ".test.js")


class NodePlugin:
    language = Language.JAVASCRIPT

    def detect(self, files: list[SourceFile]) -> float:
        ratio = language_ratio(files, *NODE_LANGUAGES)
        has_build = any(f.name.lower() == "package.json" and "node_modules" not in f.rel_path for f in files)
        if ratio == 0 and not has_build:
            return 0.0
        return min(1.0, ratio * 0.8 + (0.2 if has_build else 0.0))

    def analyze(self, files: list[SourceFile], builds: list[BuildInfo], settings: Settings) -> LanguageAnalysis:
        code = [f for f in files if f.language in NODE_LANGUAGES and not f.name.endswith(SKIP_SUFFIXES)]
        parser = NodeParser()
        parsed = [parser.parse(f) for f in code]
        project_modules = {module_id(f.rel_path) for f in code}

        units = [u for nf in parsed for u in nf.units]
        apply_stereotypes(units)
        project_types = {u.fqn for u in units if u.kind in TYPE_KINDS}

        endpoints = [e for u in units for e in extract_nest_endpoints(u)] + [e for nf in parsed for e in nf.endpoints] + extract_next_endpoints(files)
        relations = [r for nf in parsed for r in extract_relations(nf, project_modules, project_types)]
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

from __future__ import annotations

from devanalyzer.config import Settings
from devanalyzer.core.metrics import compute_metrics
from devanalyzer.core.models import BuildInfo, CodeUnitKind, Language, LanguageAnalysis, SourceFile
from devanalyzer.core.plugin import language_ratio
from devanalyzer.languages.python.django import classify_django, resolve_django_endpoints
from devanalyzer.languages.python.frameworks import apply_stereotypes, extract_relations
from devanalyzer.languages.python.parser import PythonParser, package_dirs

SKIP_PREFIXES = ("tests/", "test_", "conftest")


class PythonPlugin:
    language = Language.PYTHON

    def detect(self, files: list[SourceFile]) -> float:
        ratio = language_ratio(files, Language.PYTHON)
        has_build = any(f.name.lower() in ("pyproject.toml", "requirements.txt", "setup.py") for f in files)
        if ratio == 0 and not has_build:
            return 0.0
        return min(1.0, ratio * 0.8 + (0.2 if has_build else 0.0))

    def analyze(self, files: list[SourceFile], builds: list[BuildInfo], settings: Settings) -> LanguageAnalysis:
        code = [f for f in files if f.language == Language.PYTHON]
        parser = PythonParser(package_dirs([f.rel_path for f in code]))
        parsed = [parser.parse(f) for f in code]
        project_modules = {pf.module for pf in parsed} | {pf.module.rsplit(".__init__", 1)[0] for pf in parsed if pf.module.endswith("__init__")}

        units = [u for pf in parsed for u in pf.units]
        apply_stereotypes(units)
        for u in self._iter_classes(units):
            if u.stereotype is None and (dj := classify_django(u)):
                u.stereotype = dj
        project_types = {u.fqn for u in self._iter_classes(units)}

        units_by_fqn = {u.fqn: u for u in self._iter_classes(units)} | {u.fqn: u for u in units if u.kind == CodeUnitKind.FUNCTION}
        endpoints = [e for pf in parsed for e in pf.endpoints] + resolve_django_endpoints(parsed, units_by_fqn)
        relations = [r for pf in parsed for r in extract_relations(pf, project_modules, project_types)]
        return LanguageAnalysis(
            language=Language.PYTHON,
            confidence=self.detect(files),
            files=len(code),
            loc=sum(f.loc for f in code),
            units=units,
            endpoints=endpoints,
            relations=relations,
            metrics=compute_metrics(code, units, relations),
        )

    @staticmethod
    def _iter_classes(units):
        for u in units:
            if u.kind == CodeUnitKind.CLASS:
                yield u
                yield from PythonPlugin._iter_classes([c for c in u.children if c.kind == CodeUnitKind.CLASS])

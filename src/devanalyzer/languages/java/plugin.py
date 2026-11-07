from __future__ import annotations

from devanalyzer.config import Settings
from devanalyzer.core.metrics import compute_metrics, iter_types
from devanalyzer.core.models import BuildInfo, BuildSystem, Language, LanguageAnalysis, SourceFile
from devanalyzer.core.parse_cache import ParseCache
from devanalyzer.core.plugin import is_test_path, language_ratio
from devanalyzer.languages.java.kotlin_parser import KotlinParser
from devanalyzer.languages.java.parser import JavaParser
from devanalyzer.languages.java.spring import apply_stereotypes, extract_endpoints, extract_relations

JVM_LANGUAGES = (Language.JAVA, Language.KOTLIN)


class JavaPlugin:
    """JVM plugin: Java and Kotlin sources share the Spring/Jakarta heuristics and relation extraction."""

    language = Language.JAVA

    def __init__(self, *, incremental: bool = False):
        self._java_cache = ParseCache() if incremental else None
        self._kotlin_cache = ParseCache() if incremental else None

    def detect(self, files: list[SourceFile]) -> float:
        ratio = language_ratio(files, *JVM_LANGUAGES)
        has_build = any(f.name.lower() in ("pom.xml", "build.gradle", "build.gradle.kts") for f in files)
        if ratio == 0 and not has_build:
            return 0.0
        return min(1.0, ratio * 0.8 + (0.2 if has_build else 0.0))

    def analyze(self, files: list[SourceFile], builds: list[BuildInfo], settings: Settings) -> LanguageAnalysis:
        java_files = [f for f in files if f.language == Language.JAVA and not is_test_path(f.rel_path)]
        kotlin_files = [f for f in files if f.language == Language.KOTLIN and f.extension == ".kt" and not is_test_path(f.rel_path)]
        java_parser = JavaParser()
        parsed = self._java_cache.parse(java_files, java_parser.parse) if self._java_cache is not None else [java_parser.parse(f) for f in java_files]
        if kotlin_files:
            kotlin_parser = KotlinParser()
            parsed += (
                self._kotlin_cache.parse(kotlin_files, kotlin_parser.parse)
                if self._kotlin_cache is not None
                else [kotlin_parser.parse(f) for f in kotlin_files]
            )
        elif self._kotlin_cache is not None:
            self._kotlin_cache.clear()

        units = [u for pf in parsed for u in pf.units]
        apply_stereotypes(units)
        project_types = {t.fqn for t in iter_types(units)}

        endpoints = [e for t in iter_types(units) for e in extract_endpoints(t)]
        relations = [r for pf in parsed for r in extract_relations(pf, project_types)]
        code = java_files + kotlin_files
        metrics = compute_metrics(code, units, relations)

        return LanguageAnalysis(
            language=Language.KOTLIN if len(kotlin_files) > len(java_files) else Language.JAVA,
            confidence=self.detect(files),
            files=len(code),
            loc=sum(f.loc for f in code),
            units=units,
            endpoints=endpoints,
            relations=relations,
            metrics=metrics,
        )

    @staticmethod
    def relevant_builds(builds: list[BuildInfo]) -> list[BuildInfo]:
        return [b for b in builds if b.system in (BuildSystem.MAVEN, BuildSystem.GRADLE)]

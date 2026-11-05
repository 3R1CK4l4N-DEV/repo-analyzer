from __future__ import annotations

from devanalyzer.config import Settings
from devanalyzer.core.models import BuildInfo, BuildSystem, Language, LanguageAnalysis, SourceFile
from devanalyzer.core.plugin import language_ratio
from devanalyzer.languages.java.metrics import compute_metrics, iter_types
from devanalyzer.languages.java.parser import JavaParser
from devanalyzer.languages.java.spring import apply_stereotypes, extract_endpoints, extract_relations


class JavaPlugin:
    language = Language.JAVA

    def detect(self, files: list[SourceFile]) -> float:
        ratio = language_ratio(files, Language.JAVA, Language.KOTLIN)
        has_build = any(f.name.lower() in ("pom.xml", "build.gradle", "build.gradle.kts") for f in files)
        if ratio == 0 and not has_build:
            return 0.0
        return min(1.0, ratio * 0.8 + (0.2 if has_build else 0.0))

    def analyze(self, files: list[SourceFile], builds: list[BuildInfo], settings: Settings) -> LanguageAnalysis:
        java_files = [f for f in files if f.language == Language.JAVA]
        parser = JavaParser()
        parsed = [parser.parse(f) for f in java_files]

        units = [u for jf in parsed for u in jf.units]
        apply_stereotypes(units)
        project_types = {t.fqn for t in iter_types(units)}

        endpoints = [e for t in iter_types(units) for e in extract_endpoints(t)]
        relations = [r for jf in parsed for r in extract_relations(jf, project_types)]
        metrics = compute_metrics(java_files, units, relations)

        return LanguageAnalysis(
            language=Language.JAVA,
            confidence=self.detect(files),
            files=len(java_files),
            loc=sum(f.loc for f in java_files),
            units=units,
            endpoints=endpoints,
            relations=relations,
            metrics=metrics,
        )

    @staticmethod
    def relevant_builds(builds: list[BuildInfo]) -> list[BuildInfo]:
        return [b for b in builds if b.system in (BuildSystem.MAVEN, BuildSystem.GRADLE)]

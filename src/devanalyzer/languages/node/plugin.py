from __future__ import annotations

from devanalyzer.config import Settings
from devanalyzer.core.models import BuildInfo, Language, LanguageAnalysis, SourceFile
from devanalyzer.core.plugin import language_ratio


class NodePlugin:
    """Detection-only plugin; deep AST analysis for JS/TS arrives in milestone M8."""

    language = Language.JAVASCRIPT

    def detect(self, files: list[SourceFile]) -> float:
        ratio = language_ratio(files, Language.JAVASCRIPT, Language.TYPESCRIPT)
        has_build = any(f.name.lower() == "package.json" and "node_modules" not in f.rel_path for f in files)
        if ratio == 0 and not has_build:
            return 0.0
        return min(1.0, ratio * 0.8 + (0.2 if has_build else 0.0))

    def analyze(self, files: list[SourceFile], builds: list[BuildInfo], settings: Settings) -> LanguageAnalysis:
        code = [f for f in files if f.language in (Language.JAVASCRIPT, Language.TYPESCRIPT)]
        return LanguageAnalysis(
            language=self.language,
            confidence=self.detect(files),
            files=len(code),
            loc=sum(f.loc for f in code),
        )

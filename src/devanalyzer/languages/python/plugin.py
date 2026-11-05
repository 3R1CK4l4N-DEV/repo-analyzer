from __future__ import annotations

from devanalyzer.config import Settings
from devanalyzer.core.models import BuildInfo, Language, LanguageAnalysis, SourceFile
from devanalyzer.core.plugin import language_ratio


class PythonPlugin:
    """Detection-only plugin; deep AST analysis for Python arrives in milestone M9."""

    language = Language.PYTHON

    def detect(self, files: list[SourceFile]) -> float:
        ratio = language_ratio(files, Language.PYTHON)
        has_build = any(f.name.lower() in ("pyproject.toml", "requirements.txt", "setup.py") for f in files)
        if ratio == 0 and not has_build:
            return 0.0
        return min(1.0, ratio * 0.8 + (0.2 if has_build else 0.0))

    def analyze(self, files: list[SourceFile], builds: list[BuildInfo], settings: Settings) -> LanguageAnalysis:
        code = [f for f in files if f.language == Language.PYTHON]
        return LanguageAnalysis(
            language=self.language,
            confidence=self.detect(files),
            files=len(code),
            loc=sum(f.loc for f in code),
        )

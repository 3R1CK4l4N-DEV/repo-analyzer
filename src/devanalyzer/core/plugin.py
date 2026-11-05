from __future__ import annotations

from typing import Protocol, runtime_checkable

from devanalyzer.config import Settings
from devanalyzer.core.models import BuildInfo, Language, LanguageAnalysis, SourceFile


@runtime_checkable
class LanguagePlugin(Protocol):
    language: Language

    def detect(self, files: list[SourceFile]) -> float:
        """Return a confidence score in [0, 1] that this language is present."""
        ...

    def analyze(self, files: list[SourceFile], builds: list[BuildInfo], settings: Settings) -> LanguageAnalysis:
        """Produce the language-specific analysis for the given files."""
        ...


class PluginRegistry:
    def __init__(self):
        self._plugins: list[LanguagePlugin] = []

    def register(self, plugin: LanguagePlugin) -> None:
        self._plugins.append(plugin)

    @property
    def plugins(self) -> list[LanguagePlugin]:
        return list(self._plugins)

    def detect_all(self, files: list[SourceFile], threshold: float = 0.0) -> list[tuple[LanguagePlugin, float]]:
        scored = [(p, p.detect(files)) for p in self._plugins]
        return sorted([(p, s) for p, s in scored if s > threshold], key=lambda x: x[1], reverse=True)


def language_ratio(files: list[SourceFile], *languages: Language) -> float:
    code_files = [f for f in files if f.language is not None]
    if not code_files:
        return 0.0
    hits = sum(1 for f in code_files if f.language in languages)
    return hits / len(code_files)


def default_registry() -> PluginRegistry:
    from devanalyzer.languages.java.plugin import JavaPlugin
    from devanalyzer.languages.node.plugin import NodePlugin
    from devanalyzer.languages.python.plugin import PythonPlugin

    registry = PluginRegistry()
    registry.register(JavaPlugin())
    registry.register(NodePlugin())
    registry.register(PythonPlugin())
    return registry

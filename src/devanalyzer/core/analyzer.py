from __future__ import annotations

from pathlib import Path

from devanalyzer.config import Settings, get_settings
from devanalyzer.core.build_detectors import detect_builds
from devanalyzer.core.models import ProjectAnalysis, ProjectMetrics
from devanalyzer.core.plugin import PluginRegistry, default_registry
from devanalyzer.core.reader import ProjectReader

DETECTION_THRESHOLD = 0.05


class ProjectAnalyzer:
    def __init__(self, settings: Settings | None = None, registry: PluginRegistry | None = None):
        self.settings = settings or get_settings()
        self.registry = registry or default_registry()
        self.reader = ProjectReader(self.settings)

    def run(self, path: str | Path) -> ProjectAnalysis:
        root = Path(path).resolve()
        files = self.reader.read(root)
        builds = sorted(detect_builds(files), key=lambda b: (b.manifest_path.count("/"), b.manifest_path))

        analysis = ProjectAnalysis(
            project_name=next((b.project_name for b in builds if b.project_name), root.name),
            project_path=str(root),
            builds=builds,
        )
        for plugin, _score in self.registry.detect_all(files, threshold=DETECTION_THRESHOLD):
            analysis.languages.append(plugin.analyze(files, builds, self.settings))

        stereotypes: dict[str, int] = {}
        units_by_kind: dict[str, int] = {}
        for lang in analysis.languages:
            for k, v in lang.metrics.stereotypes.items():
                stereotypes[k] = stereotypes.get(k, 0) + v
            for k, v in lang.metrics.units_by_kind.items():
                units_by_kind[k] = units_by_kind.get(k, 0) + v
        analysis.metrics = ProjectMetrics(
            files=len(files),
            loc=sum(f.loc for f in files),
            stereotypes=stereotypes,
            units_by_kind=units_by_kind,
            fan_in={k: v for lang in analysis.languages for k, v in lang.metrics.fan_in.items()},
            fan_out={k: v for lang in analysis.languages for k, v in lang.metrics.fan_out.items()},
        )
        return analysis

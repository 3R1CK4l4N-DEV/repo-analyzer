from pathlib import Path

import pytest

from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import BuildSystem, Language

BANCO = Path.home() / "IdeaProjects" / "BancoProject"

pytestmark = pytest.mark.skipif(not BANCO.exists(), reason="BancoProject not available on this machine")


def test_banco_project(settings):
    analysis = ProjectAnalyzer(settings).run(BANCO)
    assert analysis.project_name == "BancoProject"
    assert analysis.build_systems == [BuildSystem.MAVEN]
    assert analysis.versions["java"] == "17"
    assert analysis.versions["spring_boot"] == "3.0.0"
    assert {f.name for f in analysis.frameworks} >= {"Spring Boot", "Spring Web", "Lombok"}
    assert analysis.languages[0].language == Language.JAVA
    assert analysis.metrics.stereotypes.get("application") == 1

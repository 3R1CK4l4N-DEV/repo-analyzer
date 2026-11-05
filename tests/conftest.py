from pathlib import Path

import pytest

from devanalyzer.config import Settings
from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.reader import ProjectReader

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def reader(settings) -> ProjectReader:
    return ProjectReader(settings)


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def spring_analysis(settings):
    return ProjectAnalyzer(settings).run(FIXTURES / "java_spring")

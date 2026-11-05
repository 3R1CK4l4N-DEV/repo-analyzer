from devanalyzer.core.build_detectors import GradleDetector, MavenDetector, NpmDetector, PythonBuildDetector, detect_builds
from devanalyzer.core.models import BuildSystem


def _by_name(infos, name):
    return next(d for d in infos[0].dependencies if d.name == name)


def test_maven_versions_frameworks_and_property_resolution(reader, fixtures):
    infos = MavenDetector().detect(reader.read(fixtures / "java_spring"))
    assert len(infos) == 1
    info = infos[0]
    assert info.system == BuildSystem.MAVEN
    assert info.project_name == "bank-service"
    assert info.versions == {"java": "21", "spring_boot": "3.2.5"}
    assert {f.name for f in info.frameworks} >= {"Spring Boot", "Spring Web", "Spring Data JPA", "Lombok", "MapStruct"}
    assert _by_name(infos, "org.mapstruct:mapstruct").version == "1.5.5.Final"
    assert _by_name(infos, "org.springframework.boot:spring-boot-starter-test").scope == "test"


def test_gradle_kts(reader, fixtures):
    infos = GradleDetector().detect(reader.read(fixtures / "java_gradle"))
    info = infos[0]
    assert info.system == BuildSystem.GRADLE
    assert info.versions == {"java": "17", "spring_boot": "3.3.1"}
    assert {f.name for f in info.frameworks} >= {"Spring Boot", "Spring WebFlux", "Lombok"}
    assert _by_name(infos, "org.mapstruct:mapstruct").version == "1.5.5.Final"
    assert _by_name(infos, "org.projectlombok:lombok").scope == "compileOnly"


def test_npm_with_pnpm_lock(reader, fixtures):
    infos = NpmDetector().detect(reader.read(fixtures / "node_basic"))
    info = infos[0]
    assert info.system == BuildSystem.PNPM
    assert info.project_name == "orders-api"
    assert info.versions["node"] == ">=20"
    assert info.versions["typescript"] == "^5.4.5"
    assert {f.name for f in info.frameworks} >= {"NestJS", "Express", "TypeORM", "Jest", "TypeScript"}
    assert _by_name(infos, "jest").scope == "dev"


def test_python_pyproject_uv(reader, fixtures):
    infos = PythonBuildDetector().detect(reader.read(fixtures / "python_pyproject"))
    info = infos[0]
    assert info.system == BuildSystem.UV
    assert info.project_name == "inventory-api"
    assert info.versions["python"] == ">=3.12"
    sa = _by_name(infos, "sqlalchemy")
    assert sa.version == "==2.0.30"
    assert _by_name(infos, "pytest").scope == "dev"
    assert {f.name for f in info.frameworks} >= {"FastAPI", "SQLAlchemy", "Pydantic"}


def test_detect_builds_runs_all_detectors(reader, fixtures):
    all_files = [f for name in ("java_spring", "node_basic", "python_pyproject") for f in reader.read(fixtures / name)]
    systems = {b.system for b in detect_builds(all_files)}
    assert systems == {BuildSystem.MAVEN, BuildSystem.PNPM, BuildSystem.UV}

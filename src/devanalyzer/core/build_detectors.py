from __future__ import annotations

import json
import posixpath
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Protocol

from devanalyzer.core.models import BuildInfo, BuildSystem, Dependency, SourceFile, Technology

# Known framework markers: (match on dependency name, display name)
JAVA_FRAMEWORKS: list[tuple[str, str]] = [
    ("spring-boot-starter-web", "Spring Web"),
    ("spring-boot-starter-webflux", "Spring WebFlux"),
    ("spring-boot-starter-data-jpa", "Spring Data JPA"),
    ("spring-boot-starter-data-mongodb", "Spring Data MongoDB"),
    ("spring-boot-starter-security", "Spring Security"),
    ("spring-boot-starter-validation", "Bean Validation"),
    ("spring-boot-starter-actuator", "Spring Actuator"),
    ("spring-boot-starter-test", "Spring Test"),
    ("spring-cloud", "Spring Cloud"),
    ("spring-kafka", "Spring Kafka"),
    ("hibernate", "Hibernate"),
    ("jakarta.persistence", "JPA"),
    ("javax.persistence", "JPA"),
    ("lombok", "Lombok"),
    ("mapstruct", "MapStruct"),
    ("quarkus-resteasy", "RESTEasy"),
    ("quarkus-hibernate-orm-panache", "Panache"),
    ("quarkus", "Quarkus"),
    ("micronaut-data", "Micronaut Data"),
    ("micronaut", "Micronaut"),
    ("jakarta.ws.rs", "JAX-RS"),
    ("jakarta.jakartaee", "Jakarta EE"),
    ("jakarta.enterprise", "CDI"),
    ("resteasy", "RESTEasy"),
    ("jersey", "Jersey"),
    ("junit", "JUnit"),
    ("mockito", "Mockito"),
    ("flyway", "Flyway"),
    ("liquibase", "Liquibase"),
    ("springdoc", "SpringDoc OpenAPI"),
]

NODE_FRAMEWORKS: list[tuple[str, str]] = [
    ("@nestjs/core", "NestJS"),
    ("express", "Express"),
    ("fastify", "Fastify"),
    ("koa", "Koa"),
    ("next", "Next.js"),
    ("react", "React"),
    ("vue", "Vue"),
    ("@angular/core", "Angular"),
    ("typescript", "TypeScript"),
    ("prisma", "Prisma"),
    ("typeorm", "TypeORM"),
    ("mongoose", "Mongoose"),
    ("sequelize", "Sequelize"),
    ("jest", "Jest"),
    ("vitest", "Vitest"),
]

PYTHON_FRAMEWORKS: list[tuple[str, str]] = [
    ("fastapi", "FastAPI"),
    ("flask", "Flask"),
    ("django", "Django"),
    ("sqlalchemy", "SQLAlchemy"),
    ("pydantic", "Pydantic"),
    ("celery", "Celery"),
    ("pytest", "pytest"),
    ("typer", "Typer"),
    ("click", "Click"),
]


def match_frameworks(deps: list[Dependency], table: list[tuple[str, str]]) -> list[Technology]:
    found: dict[str, Technology] = {}
    for dep in deps:
        lowered = dep.name.lower()
        for marker, display in table:
            if marker in lowered and display not in found:
                found[display] = Technology(name=display, version=dep.version)
    return list(found.values())


class BuildDetector(Protocol):
    def detect(self, files: list[SourceFile]) -> list[BuildInfo]: ...


def _find(files: list[SourceFile], *names: str) -> list[SourceFile]:
    lowered = {n.lower() for n in names}
    return [f for f in files if f.name.lower() in lowered]


# ---------------------------------------------------------------- Maven
class MavenDetector:
    NS = "{http://maven.apache.org/POM/4.0.0}"

    def detect(self, files: list[SourceFile]) -> list[BuildInfo]:
        poms = {pom.rel_path: pom for pom in _find(files, "pom.xml")}
        return [info for pom in poms.values() if (info := self._parse(pom, self._inherited(pom, poms)))]

    def _inherited(self, pom: SourceFile, poms: dict[str, SourceFile], depth: int = 0) -> dict:
        """Properties and Spring Boot/Quarkus versions inherited through `<parent>` (aggregator poms in the project
        or, for a module analysed on its own, the parent found via `relativePath` on disk). Child values win."""
        empty: dict = {"props": {}, "versions": {}}
        try:
            root = ET.fromstring(pom.content)
        except ET.ParseError:
            return empty
        ns = self.NS if root.tag.startswith("{") else ""
        parent = root.find(f"{ns}parent")
        if parent is None or depth > 3:
            return empty
        artifact, version = (parent.findtext(f"{ns}artifactId") or "").strip(), (parent.findtext(f"{ns}version") or "").strip()
        if "spring-boot" in artifact:
            return {"props": {}, "versions": {"spring_boot": version or "managed"}}
        if "quarkus" in artifact:
            return {"props": {}, "versions": {"quarkus": version or "managed"}}
        relative = parent.findtext(f"{ns}relativePath")
        relative = "../pom.xml" if relative is None else relative.strip()
        if not relative:
            return empty  # explicitly empty relativePath: parent comes from a repository
        if relative.endswith("/") or not relative.endswith(".xml"):
            relative = relative.rstrip("/") + "/pom.xml"
        target_rel = posixpath.normpath(posixpath.join(posixpath.dirname(pom.rel_path), relative))
        source = poms.get(target_rel)
        if source is None:
            candidate = Path(pom.path).parent / relative if pom.path else None
            if candidate is None or not candidate.is_file():
                return empty
            source = SourceFile(
                name="pom.xml", path=str(candidate), rel_path=target_rel, extension=".xml", content=candidate.read_text(encoding="utf-8", errors="replace")
            )
        above = self._inherited(source, poms, depth + 1)
        try:
            parent_root = ET.fromstring(source.content)
        except ET.ParseError:
            return above
        props = dict(above["props"])
        props_el = parent_root.find(f"{ns}properties")
        for child in props_el if props_el is not None else []:
            props[child.tag.replace(ns, "")] = (child.text or "").strip()
        return {"props": props, "versions": above["versions"]}

    def _parse(self, pom: SourceFile, inherited: dict | None = None) -> BuildInfo | None:
        inherited = inherited or {"props": {}, "versions": {}}
        try:
            root = ET.fromstring(pom.content)
        except ET.ParseError:
            return None
        ns = self.NS if root.tag.startswith("{") else ""
        text = lambda el, tag: (el.findtext(f"{ns}{tag}") or "").strip() or None  # noqa: E731

        props: dict[str, str] = dict(inherited["props"])
        props_el = root.find(f"{ns}properties")
        if props_el is not None:
            for child in props_el:
                props[child.tag.replace(ns, "")] = (child.text or "").strip()
        for tag in ("groupId", "artifactId", "version"):
            if value := text(root, tag):
                props[f"project.{tag}"] = value

        resolve = lambda v: re.sub(r"\$\{([^}]+)\}", lambda m: props.get(m.group(1), m.group(0)), v or "") or None  # noqa: E731

        versions: dict[str, str] = dict(inherited["versions"])
        java_version = (
            props.get("maven.compiler.release")
            or props.get("maven.compiler.source")
            or props.get("maven.compiler.target")
            or props.get("java.version")
            or props.get("release")
        )
        if java_version:
            versions["java"] = resolve(java_version)

        frameworks: list[Technology] = []
        parent = root.find(f"{ns}parent")
        if parent is not None:
            parent_artifact = text(parent, "artifactId") or ""
            parent_version = text(parent, "version")
            if "spring-boot" in parent_artifact:
                versions["spring_boot"] = parent_version or "managed"
            elif "quarkus" in parent_artifact:
                versions["quarkus"] = parent_version or "managed"
        if "spring_boot" in versions:
            frameworks.append(Technology(name="Spring Boot", version=versions["spring_boot"] if versions["spring_boot"] != "managed" else None))

        deps: list[Dependency] = []
        deps_el = root.find(f"{ns}dependencies")
        if deps_el is not None:
            for dep in deps_el.findall(f"{ns}dependency"):
                group = text(dep, "groupId") or ""
                artifact = text(dep, "artifactId") or ""
                deps.append(
                    Dependency(
                        name=f"{group}:{artifact}" if group else artifact,
                        version=resolve(text(dep, "version")),
                        scope=text(dep, "scope") or "compile",
                        source_file=pom.rel_path,
                    )
                )

        for plugin_el in root.iter(f"{ns}plugin"):
            if (text(plugin_el, "artifactId") or "") == "spring-boot-maven-plugin" and "spring_boot" not in versions:
                versions["spring_boot"] = resolve(text(plugin_el, "version")) or "managed"
                frameworks.append(Technology(name="Spring Boot", version=versions["spring_boot"]))
        # Modules that get Spring Boot through a BOM or an aggregator parent still declare the starters.
        if "spring_boot" not in versions and any(d.name.startswith("org.springframework.boot:spring-boot-starter") for d in deps):
            versions["spring_boot"] = resolve(props.get("spring-boot.version") or props.get("spring.boot.version")) or "managed"
            frameworks.append(Technology(name="Spring Boot", version=None if versions["spring_boot"] == "managed" else versions["spring_boot"]))

        frameworks += match_frameworks(deps, JAVA_FRAMEWORKS)
        if any("kotlin" in d.name for d in deps):
            versions.setdefault("kotlin", props.get("kotlin.version", "unknown"))

        return BuildInfo(
            system=BuildSystem.MAVEN,
            manifest_path=pom.rel_path,
            versions=versions,
            dependencies=deps,
            frameworks=frameworks,
            project_name=text(root, "artifactId") or text(root, "name"),
        )


# ---------------------------------------------------------------- Gradle
class GradleDetector:
    DEP_RE = re.compile(
        r"""(?P<conf>implementation|api|compileOnly|runtimeOnly|testImplementation|annotationProcessor|
        testRuntimeOnly|developmentOnly|kapt|ksp)\s*\(?\s*['"](?P<coord>[^'"]+)['"]""",
        re.X,
    )
    PLUGIN_RE = re.compile(r"""id\s*\(?\s*['"](?P<id>[^'"]+)['"]\s*\)?\s*version\s*\(?\s*['"](?P<ver>[^'"]+)['"]""")
    KOTLIN_PLUGIN_RE = re.compile(r"""kotlin\s*\(\s*['"](?:jvm|multiplatform|android)['"]\s*\)\s*version\s*['"]([^'"]+)['"]""")
    JAVA_RES = [
        re.compile(r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)"),
        re.compile(r"sourceCompatibility\s*=?\s*(?:JavaVersion\.VERSION_)?['\"]?(\d+(?:\.\d+)?)"),
        re.compile(r"targetCompatibility\s*=?\s*(?:JavaVersion\.VERSION_)?['\"]?(\d+(?:\.\d+)?)"),
        re.compile(r"jvmTarget\s*=?\s*['\"](\d+(?:\.\d+)?)['\"]"),
    ]

    def detect(self, files: list[SourceFile]) -> list[BuildInfo]:
        return [self._parse(f) for f in _find(files, "build.gradle", "build.gradle.kts")]

    def _parse(self, build: SourceFile) -> BuildInfo:
        content = build.content
        versions: dict[str, str] = {}
        for rx in self.JAVA_RES:
            if m := rx.search(content):
                versions["java"] = m.group(1).replace("1.", "", 1) if m.group(1).startswith("1.") else m.group(1)
                break

        frameworks: list[Technology] = []
        for m in self.PLUGIN_RE.finditer(content):
            if m.group("id") == "org.springframework.boot":
                versions["spring_boot"] = m.group("ver")
                frameworks.append(Technology(name="Spring Boot", version=m.group("ver")))
            elif m.group("id").startswith("org.jetbrains.kotlin"):
                versions.setdefault("kotlin", m.group("ver"))
        if km := self.KOTLIN_PLUGIN_RE.search(content):
            versions.setdefault("kotlin", km.group(1))
            frameworks.append(Technology(name="Kotlin", version=km.group(1)))

        deps: list[Dependency] = []
        for m in self.DEP_RE.finditer(content):
            parts = m.group("coord").split(":")
            name = ":".join(parts[:2]) if len(parts) >= 2 else parts[0]
            deps.append(
                Dependency(
                    name=name,
                    version=parts[2] if len(parts) >= 3 else None,
                    scope=m.group("conf"),
                    source_file=build.rel_path,
                )
            )
        frameworks += match_frameworks(deps, JAVA_FRAMEWORKS)
        return BuildInfo(
            system=BuildSystem.GRADLE,
            manifest_path=build.rel_path,
            versions=versions,
            dependencies=deps,
            frameworks=frameworks,
        )


# ---------------------------------------------------------------- Node
class NpmDetector:
    def detect(self, files: list[SourceFile]) -> list[BuildInfo]:
        infos = []
        lock_names = {f.name.lower() for f in files}
        for pkg in _find(files, "package.json"):
            if "node_modules" in pkg.rel_path:
                continue
            if info := self._parse(pkg, lock_names):
                infos.append(info)
        return infos

    def _parse(self, pkg: SourceFile, lock_names: set[str]) -> BuildInfo | None:
        try:
            data = json.loads(pkg.content)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None

        system = BuildSystem.NPM
        pm = str(data.get("packageManager", ""))
        if pm.startswith("pnpm") or "pnpm-lock.yaml" in lock_names:
            system = BuildSystem.PNPM
        elif pm.startswith("yarn") or "yarn.lock" in lock_names:
            system = BuildSystem.YARN

        versions: dict[str, str] = {}
        engines = data.get("engines") or {}
        if node := engines.get("node") or (data.get("volta") or {}).get("node"):
            versions["node"] = str(node)

        deps: list[Dependency] = []
        for key, scope in (("dependencies", "runtime"), ("devDependencies", "dev"), ("peerDependencies", "peer")):
            for name, version in (data.get(key) or {}).items():
                deps.append(Dependency(name=name, version=str(version), scope=scope, source_file=pkg.rel_path))

        frameworks = match_frameworks(deps, NODE_FRAMEWORKS)
        ts = next((d for d in deps if d.name == "typescript"), None)
        if ts:
            versions["typescript"] = ts.version or "unknown"

        return BuildInfo(
            system=system,
            manifest_path=pkg.rel_path,
            versions=versions,
            dependencies=deps,
            frameworks=frameworks,
            project_name=data.get("name"),
        )


# ---------------------------------------------------------------- Python
class PythonBuildDetector:
    REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*(?:(==|>=|<=|~=|!=|>|<)\s*([^\s;#]+))?")

    REQUIREMENTS_RE = re.compile(r"(?:^|/)requirements(?:[\w.-]*\.txt|/[\w.-]+\.txt)$")

    def detect(self, files: list[SourceFile]) -> list[BuildInfo]:
        infos: list[BuildInfo] = []
        names = {f.name.lower() for f in files}
        for py in _find(files, "pyproject.toml"):
            if info := self._parse_pyproject(py, names):
                infos.append(info)
        if not infos:
            req_files = {f.rel_path: f for f in files if self.REQUIREMENTS_RE.search(f.rel_path)}
            # `requirements.txt` usually includes the others with `-r`; without it, every requirements file counts.
            roots = [f for f in req_files.values() if f.name == "requirements.txt"] or sorted(req_files.values(), key=lambda f: f.rel_path)
            if roots:
                infos.append(self._parse_requirements(roots, req_files))
        return infos

    def _parse_pyproject(self, py: SourceFile, names: set[str]) -> BuildInfo | None:
        try:
            data = tomllib.loads(py.content)
        except tomllib.TOMLDecodeError:
            return None
        project = data.get("project") or {}
        tool = data.get("tool") or {}
        poetry = tool.get("poetry") or {}
        if not project and not poetry:
            return None  # tooling-only pyproject (ruff, pytest...): dependencies live in requirements files

        system = BuildSystem.PIP
        if poetry:
            system = BuildSystem.POETRY
        elif "uv.lock" in names or "uv" in tool:
            system = BuildSystem.UV

        versions: dict[str, str] = {}
        if rp := project.get("requires-python") or (poetry.get("dependencies") or {}).get("python"):
            versions["python"] = str(rp)

        deps: list[Dependency] = []
        for spec in project.get("dependencies") or []:
            deps.append(self._dep_from_spec(spec, "runtime", py.rel_path))
        for group, specs in (project.get("optional-dependencies") or {}).items():
            deps += [self._dep_from_spec(s, group, py.rel_path) for s in specs]
        for name, spec in (poetry.get("dependencies") or {}).items():
            if name.lower() == "python":
                continue
            version = spec if isinstance(spec, str) else (spec.get("version") if isinstance(spec, dict) else None)
            deps.append(Dependency(name=name, version=version, scope="runtime", source_file=py.rel_path))

        return BuildInfo(
            system=system,
            manifest_path=py.rel_path,
            versions=versions,
            dependencies=deps,
            frameworks=match_frameworks(deps, PYTHON_FRAMEWORKS),
            project_name=project.get("name") or poetry.get("name"),
        )

    def _parse_requirements(self, roots: list[SourceFile], by_path: dict[str, SourceFile]) -> BuildInfo:
        deps: list[Dependency] = []
        seen: set[str] = set()

        def visit(req: SourceFile) -> None:
            if req.rel_path in seen:
                return
            seen.add(req.rel_path)
            for line in req.content.splitlines():
                line = line.split(" #", 1)[0].strip()
                if line.startswith(("-r", "--requirement")):
                    target = line.split("=", 1)[1] if line.startswith("--requirement=") else line.split(None, 1)[1] if " " in line else line[2:]
                    included = by_path.get(posixpath.normpath(posixpath.join(posixpath.dirname(req.rel_path), target.strip())))
                    if included is not None:
                        visit(included)
                elif line and not line.startswith(("#", "-")):
                    dep = self._dep_from_spec(line, "runtime", req.rel_path)
                    if dep.name.lower() not in {d.name.lower() for d in deps}:
                        deps.append(dep)

        for root in roots:
            visit(root)
        return BuildInfo(
            system=BuildSystem.PIP,
            manifest_path=roots[0].rel_path,
            dependencies=deps,
            frameworks=match_frameworks(deps, PYTHON_FRAMEWORKS),
        )

    def _dep_from_spec(self, spec: str, scope: str, source: str) -> Dependency:
        m = self.REQ_RE.match(spec)
        if not m:
            return Dependency(name=spec, scope=scope, source_file=source)
        name = re.sub(r"\[.*\]", "", m.group(1))
        version = f"{m.group(2)}{m.group(3)}" if m.group(2) else None
        return Dependency(name=name, version=version, scope=scope, source_file=source)


DEFAULT_BUILD_DETECTORS: list[BuildDetector] = [
    MavenDetector(),
    GradleDetector(),
    NpmDetector(),
    PythonBuildDetector(),
]


def detect_builds(files: list[SourceFile], detectors: list[BuildDetector] | None = None) -> list[BuildInfo]:
    infos: list[BuildInfo] = []
    for detector in detectors or DEFAULT_BUILD_DETECTORS:
        infos.extend(detector.detect(files))
    return infos

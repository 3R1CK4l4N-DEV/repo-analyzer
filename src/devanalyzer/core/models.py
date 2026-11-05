from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Language(str, Enum):
    JAVA = "java"
    KOTLIN = "kotlin"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    PYTHON = "python"


EXTENSION_LANGUAGE: dict[str, Language] = {
    ".java": Language.JAVA,
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".py": Language.PYTHON,
}


class BuildSystem(str, Enum):
    MAVEN = "maven"
    GRADLE = "gradle"
    NPM = "npm"
    PNPM = "pnpm"
    YARN = "yarn"
    PIP = "pip"
    POETRY = "poetry"
    UV = "uv"
    UNKNOWN = "unknown"


class SourceFile(BaseModel):
    name: str
    path: str
    rel_path: str
    extension: str
    content: str
    language: Language | None = None
    size: int = 0

    @property
    def loc(self) -> int:
        return sum(1 for line in self.content.splitlines() if line.strip())


class Dependency(BaseModel):
    name: str
    version: str | None = None
    scope: str = "compile"
    source_file: str | None = None


class Technology(BaseModel):
    name: str
    version: str | None = None


class BuildInfo(BaseModel):
    system: BuildSystem
    manifest_path: str
    versions: dict[str, str] = Field(default_factory=dict)
    dependencies: list[Dependency] = Field(default_factory=list)
    frameworks: list[Technology] = Field(default_factory=list)
    project_name: str | None = None


class Annotation(BaseModel):
    name: str
    arguments: str | None = None


class Parameter(BaseModel):
    name: str
    type: str | None = None


class CodeUnitKind(str, Enum):
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    RECORD = "record"
    METHOD = "method"
    FUNCTION = "function"
    FIELD = "field"
    MODULE = "module"


class CodeUnit(BaseModel):
    kind: CodeUnitKind
    name: str
    fqn: str
    file: str
    line_start: int
    line_end: int
    language: Language | None = None
    package: str | None = None
    modifiers: list[str] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    type: str | None = None
    parameters: list[Parameter] = Field(default_factory=list)
    extends: list[str] = Field(default_factory=list)
    implements: list[str] = Field(default_factory=list)
    stereotype: str | None = None
    docstring: str | None = None
    calls: list[str] = Field(default_factory=list)
    children: list[CodeUnit] = Field(default_factory=list)

    @property
    def loc(self) -> int:
        return self.line_end - self.line_start + 1

    def annotation_names(self) -> set[str]:
        return {a.name for a in self.annotations}

    def has_annotation(self, *names: str) -> bool:
        return bool(self.annotation_names() & set(names))


class Endpoint(BaseModel):
    http_method: str
    path: str
    handler: str
    file: str
    line: int
    framework: str | None = None


class RelationKind(str, Enum):
    CONTAINS = "contains"
    IMPORTS = "imports"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    DEPENDS_ON = "depends_on"
    CALLS = "calls"
    EXPOSES = "exposes"
    USES_DEPENDENCY = "uses_dependency"


class Relation(BaseModel):
    source: str
    target: str
    kind: RelationKind


class Evidence(BaseModel):
    file: str
    line_start: int
    line_end: int
    unit: str | None = None
    snippet: str | None = None


class RuleStatus(str, Enum):
    INFERRED = "inferred"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class BusinessRule(BaseModel):
    id: str
    statement: str
    rationale: str | None = None
    category: str = "general"
    domain_terms: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.5
    status: RuleStatus = RuleStatus.INFERRED
    tags: list[str] = Field(default_factory=list)
    source_unit: str | None = None
    model: str | None = None


class GlossaryTerm(BaseModel):
    term: str
    definition: str
    aliases: list[str] = Field(default_factory=list)
    related_units: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)


class RuleExtractionResult(BaseModel):
    project_name: str
    model: str
    rules: list[BusinessRule] = Field(default_factory=list)
    glossary: list[GlossaryTerm] = Field(default_factory=list)
    units_analyzed: int = 0
    units_skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class ProjectMetrics(BaseModel):
    files: int = 0
    loc: int = 0
    stereotypes: dict[str, int] = Field(default_factory=dict)
    units_by_kind: dict[str, int] = Field(default_factory=dict)
    fan_in: dict[str, int] = Field(default_factory=dict)
    fan_out: dict[str, int] = Field(default_factory=dict)


class LanguageAnalysis(BaseModel):
    language: Language
    confidence: float
    files: int = 0
    loc: int = 0
    units: list[CodeUnit] = Field(default_factory=list)
    endpoints: list[Endpoint] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    metrics: ProjectMetrics = Field(default_factory=ProjectMetrics)


class ProjectAnalysis(BaseModel):
    project_name: str
    project_path: str
    languages: list[LanguageAnalysis] = Field(default_factory=list)
    builds: list[BuildInfo] = Field(default_factory=list)
    metrics: ProjectMetrics = Field(default_factory=ProjectMetrics)

    @property
    def build_systems(self) -> list[BuildSystem]:
        return [b.system for b in self.builds]

    @property
    def versions(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for b in self.builds:
            merged.update(b.versions)
        return merged

    @property
    def dependencies(self) -> list[Dependency]:
        return [d for b in self.builds for d in b.dependencies]

    @property
    def frameworks(self) -> list[Technology]:
        seen: dict[str, Technology] = {}
        for b in self.builds:
            for f in b.frameworks:
                seen.setdefault(f.name, f)
        return list(seen.values())

    @property
    def units(self) -> list[CodeUnit]:
        return [u for lang in self.languages for u in lang.units]

    @property
    def endpoints(self) -> list[Endpoint]:
        return [e for lang in self.languages for e in lang.endpoints]

    @property
    def relations(self) -> list[Relation]:
        return [r for lang in self.languages for r in lang.relations]

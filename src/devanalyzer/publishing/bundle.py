from __future__ import annotations

import json
import re
import shutil
import tempfile
import warnings
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from devanalyzer import __version__
from devanalyzer.config import Settings
from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import ProjectAnalysis, RuleExtractionResult
from devanalyzer.core.workspace_lookup import find_workspace, project_name_in
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.kb.store import KnowledgeBase
from devanalyzer.obsidian.vault import ObsidianVault
from devanalyzer.reporting.diff import diff_analyses, render_diff_markdown
from devanalyzer.reporting.html_report import _iter_types, render_html, render_markdown


class PublishSource(BaseModel):
    repo_url: str | None = None
    ref: str | None = None
    commit: str | None = None
    subdir: str | None = None


class PublishManifest(BaseModel):
    schema_version: int = 1
    slug: str
    name: str
    source: PublishSource
    generated_at: str
    devanalyzer_version: str
    summary: dict[str, Any]
    artifacts: dict[str, str]
    sizes: dict[str, int]


def sanitize_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", value) or "project"


def _load_knowledge(
    analysis: ProjectAnalysis,
    out: Path,
    *,
    rules_path: Path | None,
    kb_path: Path | None,
) -> tuple[list, list, list, list, bool]:
    if rules_path is not None and kb_path is not None:
        raise ValueError("--rules and --kb are mutually exclusive")
    published_kb = out / "kb.sqlite"
    if kb_path is not None:
        if not kb_path.is_file():
            raise FileNotFoundError(kb_path)
        if kb_path.resolve() != published_kb.resolve():
            shutil.copy2(kb_path, published_kb)
    elif rules_path is not None:
        if not rules_path.is_file():
            raise FileNotFoundError(rules_path)
        result = RuleExtractionResult.model_validate_json(rules_path.read_text(encoding="utf-8"))
        published_kb.unlink(missing_ok=True)
        with KnowledgeBase(published_kb) as kb:
            kb.ingest(result)
    else:
        published_kb.unlink(missing_ok=True)
        return [], [], [], [], False

    with KnowledgeBase(published_kb) as kb:
        rules = [r for r in kb.rules(project=analysis.project_name) if r.status.value != "rejected"]
        glossary = kb.terms(project=analysis.project_name)
        decisions = kb.decisions(project=analysis.project_name)
        groups = kb.groups(project=analysis.project_name)
    return rules, glossary, decisions, groups, True


def _write_vault(
    analysis: ProjectAnalysis,
    graph: KnowledgeGraph,
    out: Path,
    slug: str,
    rules: list,
    glossary: list,
    decisions: list,
    groups: list,
) -> None:
    with tempfile.TemporaryDirectory(prefix="devanalyzer-vault-") as tmp:
        vault_dir = Path(tmp) / "vault"
        ObsidianVault(vault_dir).generate(analysis, graph, rules, glossary, decisions, groups)
        with zipfile.ZipFile(out / "vault.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(vault_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, (Path(f"{slug}-vault") / path.relative_to(vault_dir)).as_posix())


def _summary(analysis: ProjectAnalysis, graph: KnowledgeGraph, *, rules: list, glossary: list) -> dict[str, Any]:
    return {
        "files": analysis.metrics.files,
        "loc": analysis.metrics.loc,
        "languages": [{"language": lang.language.value, "confidence": lang.confidence, "files": lang.files, "loc": lang.loc} for lang in analysis.languages],
        "build_systems": [build.value for build in analysis.build_systems],
        "versions": analysis.versions,
        "frameworks": [{"name": framework.name, "version": framework.version} for framework in analysis.frameworks],
        "stereotypes": analysis.metrics.stereotypes,
        "units": sum(1 for _ in _iter_types(analysis.units)),
        "endpoints": len(analysis.endpoints),
        "tables": len(analysis.db_schema.tables),
        "dependencies": len(analysis.dependencies),
        "rules": len(rules),
        "terms": len(glossary),
        "graph": graph.stats(),
    }


def build_bundle(
    project: Path,
    out: Path,
    *,
    slug: str | None = None,
    source: PublishSource | None = None,
    rules_path: Path | None = None,
    kb_path: Path | None = None,
    baseline: Path | None = None,
    graphml: bool = True,
    settings: Settings | None = None,
) -> PublishManifest:
    project = project.resolve()
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    slug = sanitize_slug(slug or project.name)
    source = source or PublishSource()

    analysis = ProjectAnalyzer(settings).run(project)
    real_path = analysis.project_path
    display_path = source.repo_url or slug
    workspace = find_workspace(real_path)
    workspace_name = project_name_in(workspace, real_path) if workspace is not None else None
    workspace_aware = workspace_name is not None and any(link.source == workspace_name or link.target == workspace_name for link in workspace.links)
    analysis.project_path = display_path
    graph = KnowledgeGraph.from_analysis(analysis)
    rules, glossary, decisions, groups, has_kb = _load_knowledge(analysis, out, rules_path=rules_path, kb_path=kb_path)

    graph.save(out, graphml=graphml)
    if not graphml:
        (out / "graph.graphml").unlink(missing_ok=True)
    analysis.project_path = real_path if workspace_aware else display_path
    _write_vault(analysis, graph, out, slug, rules, glossary, decisions, groups)

    analysis.project_path = display_path
    (out / "report.html").write_text(render_html(analysis, graph, rules, glossary), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(analysis, graph, rules, glossary), encoding="utf-8")
    (out / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    artifacts = {
        "analysis": "analysis.json",
        "graph": "graph.json",
        "report_html": "report.html",
        "report_md": "report.md",
        "vault_zip": "vault.zip",
    }
    if graphml:
        artifacts["graphml"] = "graph.graphml"
    if has_kb:
        artifacts["kb"] = "kb.sqlite"
        knowledge = {
            "rules": [rule.model_dump(mode="json") for rule in rules],
            "glossary": [term.model_dump(mode="json") for term in glossary],
            "decisions": decisions,
            "groups": [group.model_dump(mode="json") for group in groups],
        }
        (out / "knowledge.json").write_text(json.dumps(knowledge, indent=2), encoding="utf-8")
        artifacts["knowledge"] = "knowledge.json"
    else:
        (out / "knowledge.json").unlink(missing_ok=True)

    for stale in ("changes.md", "changes.json"):
        (out / stale).unlink(missing_ok=True)
    if baseline is not None and baseline.is_file():
        try:
            before = ProjectAnalysis.model_validate_json(baseline.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warnings.warn(f"Ignoring invalid baseline {baseline}: {exc}", stacklevel=2)
        else:
            changes = diff_analyses(before, analysis)
            (out / "changes.md").write_text(render_diff_markdown(changes), encoding="utf-8")
            (out / "changes.json").write_text(changes.model_dump_json(indent=2), encoding="utf-8")
            artifacts["changes_md"] = "changes.md"
            artifacts["changes_json"] = "changes.json"

    sizes = {filename: (out / filename).stat().st_size for filename in artifacts.values()}
    manifest = PublishManifest(
        slug=slug,
        name=analysis.project_name,
        source=source,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        devanalyzer_version=__version__,
        summary=_summary(analysis, graph, rules=rules, glossary=glossary),
        artifacts=artifacts,
        sizes=sizes,
    )
    (out / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest

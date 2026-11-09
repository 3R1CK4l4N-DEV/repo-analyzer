import json
import os
import zipfile

import pytest

from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import BusinessRule, ProjectAnalysis, RuleExtractionResult, RuleStatus
from devanalyzer.core.workspace import WorkspaceAnalysis
from devanalyzer.publishing.bundle import PublishSource, build_bundle
from devanalyzer.publishing.site import parse_repo, publish_site, write_index
from devanalyzer.reporting.diff import AnalysisDiff


def test_build_bundle(fixtures, tmp_path, settings):
    project = fixtures / "java_spring"
    out = tmp_path / "bundle"
    source = PublishSource(repo_url="https://github.com/acme/demo", ref="main", commit="abc123")
    manifest = build_bundle(project, out, source=source, settings=settings)

    expected = {"analysis.json", "graph.json", "graph.graphml", "report.html", "report.md", "vault.zip", "manifest.json"}
    assert expected <= {path.name for path in out.iterdir()}
    assert manifest.slug == "java_spring"
    assert manifest.summary["endpoints"] > 0
    assert manifest.summary["graph"]["nodes"] > 0
    assert manifest.summary["units"] > 0
    assert set(manifest.artifacts) == {"analysis", "graph", "graphml", "report_html", "report_md", "vault_zip"}
    assert "kb" not in manifest.artifacts and "knowledge" not in manifest.artifacts and "changes_md" not in manifest.artifacts
    assert all(manifest.sizes[name] == (out / name).stat().st_size for name in manifest.sizes)

    with zipfile.ZipFile(out / "vault.zip") as archive:
        names = archive.namelist()
        home = archive.read(next(name for name in names if name.endswith(" - Home.md"))).decode()
    assert names and all(name.startswith("java_spring-vault/") for name in names)
    assert any(name.endswith(" - Home.md") for name in names)
    analysis = ProjectAnalysis.model_validate_json((out / "analysis.json").read_text(encoding="utf-8"))
    assert analysis.project_path == "https://github.com/acme/demo"
    absolute_paths = (str(project.resolve()), project.resolve().as_posix())
    for filename in ("graph.json", "graph.graphml", "report.html", "report.md", "analysis.json"):
        content = (out / filename).read_text(encoding="utf-8")
        assert all(absolute_path not in content for absolute_path in absolute_paths)
    assert all(absolute_path not in home for absolute_path in absolute_paths)


def test_build_bundle_with_rules(fixtures, tmp_path, settings):
    rules_path = tmp_path / "rules.json"
    inferred = BusinessRule(
        id="BR-accepted",
        statement="Deposits must preserve the account balance.",
        source_unit="com.acme.bank.service.AccountService",
        status=RuleStatus.INFERRED,
    )
    rejected = BusinessRule(
        id="BR-rejected",
        statement="This rejected statement must not be published.",
        source_unit="com.acme.bank.service.AccountService",
        status=RuleStatus.REJECTED,
    )
    result = RuleExtractionResult(project_name="bank-service", model="test", rules=[inferred, rejected])
    rules_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    out = tmp_path / "bundle"
    manifest = build_bundle(fixtures / "java_spring", out, rules_path=rules_path, settings=settings)
    assert manifest.summary["rules"] == 1
    assert manifest.artifacts["kb"] == "kb.sqlite"
    assert manifest.artifacts["knowledge"] == "knowledge.json"
    knowledge = json.loads((out / "knowledge.json").read_text(encoding="utf-8"))
    assert len(knowledge["rules"]) == 1
    with zipfile.ZipFile(out / "vault.zip") as archive:
        assert any("/30 Business Rules/" in name for name in archive.namelist())
    report = (out / "report.html").read_text(encoding="utf-8")
    assert inferred.statement in report
    assert rejected.statement not in report


def test_build_bundle_with_baseline(fixtures, tmp_path, settings):
    baseline = tmp_path / "baseline.json"
    before = ProjectAnalyzer(settings).run(fixtures / "python_fastapi")
    baseline.write_text(before.model_dump_json(indent=2), encoding="utf-8")
    out = tmp_path / "bundle"

    manifest = build_bundle(fixtures / "java_spring", out, baseline=baseline, settings=settings)
    assert manifest.artifacts["changes_md"] == "changes.md"
    assert manifest.artifacts["changes_json"] == "changes.json"
    assert (out / "changes.md").is_file()
    changes = AnalysisDiff.model_validate_json((out / "changes.json").read_text(encoding="utf-8"))
    assert changes.units_added


def test_build_bundle_ignores_invalid_baseline(fixtures, tmp_path, settings):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    with pytest.warns(UserWarning, match="Ignoring invalid baseline"):
        manifest = build_bundle(fixtures / "java_spring", tmp_path / "bundle", baseline=baseline, settings=settings)
    assert "changes_md" not in manifest.artifacts
    assert not (tmp_path / "bundle" / "changes.json").exists()


def test_write_index_orders_projects(fixtures, tmp_path, settings):
    data = tmp_path / "data"
    build_bundle(fixtures / "java_spring", data / "p" / "spring", slug="spring", settings=settings)
    build_bundle(fixtures / "python_fastapi", data / "p" / "python", slug="python", settings=settings)
    index = json.loads(write_index(data).read_text(encoding="utf-8"))
    assert [project["name"] for project in index["projects"]] == sorted([project["name"] for project in index["projects"]], key=str.casefold)
    assert {project["base"] for project in index["projects"]} == {"p/spring/", "p/python/"}


def _write_config(path, projects):
    path.write_text(json.dumps({"workspace": False, "projects": projects}), encoding="utf-8")


def test_publish_site_with_local_projects(fixtures, tmp_path):
    config = tmp_path / "repos.json"
    _write_config(
        config,
        [
            {"repo": str(fixtures / "java_spring"), "slug": "spring"},
            {"repo": str(fixtures / "python_fastapi"), "slug": "python"},
        ],
    )
    out = tmp_path / "data"
    manifests = publish_site(config, out)
    assert {manifest.slug for manifest in manifests} == {"spring", "python"}
    assert (out / "p" / "spring" / "manifest.json").is_file()
    assert (out / "p" / "python" / "manifest.json").is_file()
    assert (out / "projects.json").is_file()
    assert all(manifest.source.repo_url is None and manifest.source.commit is None for manifest in manifests)


def test_publish_site_workspace(fixtures, tmp_path, monkeypatch, settings):
    monkeypatch.delenv("DEVANALYZER_WORKSPACE", raising=False)
    config = tmp_path / "repos.json"
    config.write_text(
        json.dumps(
            {
                "workspace": True,
                "projects": [
                    {"repo": str(fixtures / "workspace" / "orders-service"), "slug": "orders-service"},
                    {"repo": str(fixtures / "workspace" / "billing-service"), "slug": "billing-service"},
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "data"

    manifests = publish_site(config, out, settings=settings)

    assert {manifest.slug for manifest in manifests} == {"orders-service", "billing-service"}
    assert (out / "workspace.md").is_file()
    workspace_json = (out / "workspace.json").read_text(encoding="utf-8")
    assert str(fixtures.resolve()) not in workspace_json
    workspace = WorkspaceAnalysis.model_validate_json(workspace_json)
    links = {(link.kind, link.source, link.target, link.label) for link in workspace.links}
    assert ("http", "orders-service", "billing-service", "POST /invoices") in links
    with zipfile.ZipFile(out / "p" / "orders-service" / "vault.zip") as archive:
        assert any(name.endswith("/00 MOC/Related Services.md") for name in archive.namelist())
    assert "DEVANALYZER_WORKSPACE" not in os.environ


def test_publish_site_continues_after_failure(fixtures, tmp_path):
    config = tmp_path / "repos.json"
    missing = tmp_path / "does-not-exist"
    _write_config(
        config,
        [
            {"repo": str(missing), "slug": "missing"},
            {"repo": str(fixtures / "java_spring"), "slug": "spring"},
        ],
    )
    progress = []
    manifests = publish_site(config, tmp_path / "data", progress=progress.append)
    assert [manifest.slug for manifest in manifests] == ["spring"]
    assert any(message.startswith("ERROR missing:") for message in progress)

    _write_config(config, [{"repo": str(missing), "slug": "missing"}, {"repo": str(tmp_path / "also-missing"), "slug": "also"}])
    with pytest.raises(RuntimeError, match="All projects failed"):
        publish_site(config, tmp_path / "all-fail")


@pytest.mark.parametrize(
    ("value", "url", "slug", "ref"),
    [
        ("owner/name", "https://github.com/owner/name", "name", None),
        ("owner/name@dev", "https://github.com/owner/name", "name", "dev"),
        ("https://github.com/o/r.git", "https://github.com/o/r", "r", None),
        ("https://github.com/o/r", "https://github.com/o/r", "r", None),
    ],
)
def test_parse_repo(value, url, slug, ref):
    parsed = parse_repo(value)
    assert (parsed.url, parsed.slug, parsed.ref) == (url, slug, ref)

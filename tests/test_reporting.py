import copy

from devanalyzer.core.models import CodeUnitKind, Dependency, Endpoint
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.llm.business_rules import BusinessRuleExtractor
from devanalyzer.reporting.diff import diff_analyses, render_diff_markdown
from devanalyzer.reporting.html_report import render_html, render_markdown
from tests.test_business_rules import FakeProvider


def test_html_and_markdown_reports(spring_analysis, fixtures):
    result = BusinessRuleExtractor(FakeProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    page = render_html(spring_analysis, kg, result.rules, result.glossary)
    assert page.startswith("<!doctype html>")
    assert "bank-service" in page and "<svg" in page
    assert "POST" in page and "/accounts/{id}/deposit" in page
    assert "BR-" in page and "A single deposit cannot exceed 10,000." in page
    assert "<script" not in page  # self-contained, no external/JS dependencies

    md = render_markdown(spring_analysis, kg, result.rules, result.glossary)
    assert md.startswith("# bank-service")
    assert "| POST | `/accounts/{id}/deposit` |" in md
    assert "## Business rules" in md and "## Glossary" in md


def test_diff_detects_structural_changes(spring_analysis):
    after = copy.deepcopy(spring_analysis)
    java = after.languages[0]
    service = next(u for u in java.units if u.name == "AccountService")
    service.children = [c for c in service.children if c.name != "close"]
    java.units = [u for u in java.units if u.name != "Auditable"]
    java.endpoints = [e for e in java.endpoints if e.http_method != "DELETE"] + [Endpoint(http_method="GET", path="/accounts/search", handler="x.search", file="f", line=1)]
    after.builds[0].dependencies.append(Dependency(name="org.flywaydb:flyway-core", version="10.0.0"))
    after.builds[0].dependencies[0].version = "9.9.9"
    after.builds[0].versions["java"] = "22"

    d = diff_analyses(spring_analysis, after)
    assert d.units_removed == ["com.acme.bank.model.Auditable"] and d.units_added == []
    assert set(d.methods_removed) == {"com.acme.bank.service.AccountService.close(Long)", "com.acme.bank.model.Auditable.auditTag()"}
    assert d.endpoints_added == ["GET /accounts/search"] and d.endpoints_removed == ["DELETE /accounts/{id}"]
    assert d.dependencies_added == ["org.flywaydb:flyway-core 10.0.0"]
    assert d.dependencies_changed == ["org.springframework.boot:spring-boot-starter-web: managed -> 9.9.9"]
    assert d.versions_changed == ["java: 21 -> 22"]
    text = render_diff_markdown(d)
    assert "## Endpoints added (1)" in text and "Versions changed" in text

    assert diff_analyses(spring_analysis, spring_analysis).is_empty
    assert render_diff_markdown(diff_analyses(spring_analysis, spring_analysis)) == "No structural changes detected."

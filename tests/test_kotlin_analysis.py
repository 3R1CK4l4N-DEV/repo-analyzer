from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import CodeUnitKind, Language, RelationKind
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.llm.business_rules import BusinessRuleExtractor


def _unit(analysis, fqn):
    return next(u for u in analysis.units if u.fqn == fqn)


def test_kotlin_project_analysis(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "kotlin_spring")
    jvm = analysis.languages[0]
    assert jvm.language == Language.KOTLIN and jvm.files == 4
    assert analysis.versions == {"java": "21", "spring_boot": "3.3.1", "kotlin": "1.9.24"}

    service = _unit(analysis, "com.acme.loans.LoanService")
    assert service.stereotype == "service" and service.language == Language.KOTLIN
    fields = {c.name: c for c in service.children if c.kind == CodeUnitKind.FIELD}
    assert fields["repository"].type == "LoanRepository" and "injected" in fields["repository"].modifiers
    approve = next(c for c in service.children if c.name == "approve")
    assert approve.type == "Loan" and [p.type for p in approve.parameters] == ["Long"]
    assert approve.has_annotation("Transactional") and approve.docstring.startswith("Business rule")
    assert {"repository.findById", "repository.save", "scoring.scoreFor", "require"} <= set(approve.calls)
    assert approve.line_start == 12 and approve.line_end == 19  # span starts at @Transactional, KDoc excluded

    loan = _unit(analysis, "com.acme.loans.Loan")
    assert loan.kind == CodeUnitKind.RECORD and loan.stereotype == "entity" and loan.docstring == "A loan requested by a customer."
    assert {c.name for c in loan.children if c.kind == CodeUnitKind.FIELD} == {"id", "customerId", "amount", "status"}
    assert next(c for c in loan.children if c.name == "id").has_annotation("Id")
    assert _unit(analysis, "com.acme.loans.LoanStatus").kind == CodeUnitKind.ENUM
    repo = _unit(analysis, "com.acme.loans.LoanRepository")
    assert repo.kind == CodeUnitKind.INTERFACE and repo.stereotype == "repository" and repo.extends == ["org.springframework.data.jpa.repository.JpaRepository"]
    assert analysis.metrics.stereotypes == {"controller": 1, "service": 1, "repository": 1, "entity": 1}


def test_kotlin_endpoints_relations_graph(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "kotlin_spring")
    eps = {(e.http_method, e.path): e for e in analysis.endpoints}
    assert set(eps) == {("GET", "/loans/customer/{customerId}"), ("POST", "/loans/{id}/approve")}
    assert eps[("POST", "/loans/{id}/approve")].handler == "com.acme.loans.LoanController.approve"

    rels = {(r.source, r.target, r.kind) for r in analysis.relations}
    assert ("com.acme.loans.LoanController", "com.acme.loans.LoanService", RelationKind.DEPENDS_ON) in rels
    assert ("com.acme.loans.LoanService", "com.acme.loans.LoanRepository", RelationKind.DEPENDS_ON) in rels
    assert ("com.acme.loans.LoanService", "com.acme.loans.ScoringClient", RelationKind.DEPENDS_ON) in rels
    assert ("com.acme.loans.LoanService.approve", "com.acme.loans.ScoringClient.scoreFor", RelationKind.CALLS) in rels

    kg = KnowledgeGraph.from_analysis(analysis)
    path = kg.shortest_path("com.acme.loans.LoanController", "com.acme.loans.LoanRepository")
    assert path and len(path) == 3
    assert "com.acme.loans.LoanService" in {u.fqn for u in BusinessRuleExtractor.candidate_units(analysis)}

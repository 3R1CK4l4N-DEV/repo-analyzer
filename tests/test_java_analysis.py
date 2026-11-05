from devanalyzer.core.models import CodeUnitKind, Language, RelationKind
from devanalyzer.core.plugin import default_registry
from devanalyzer.languages.java.parser import JavaParser


def _unit(analysis, fqn):
    return next(u for u in analysis.units if u.fqn == fqn)


def test_plugin_detection(reader, fixtures):
    files = reader.read(fixtures / "java_spring")
    scores = {p.language: s for p, s in default_registry().detect_all(files)}
    assert scores[Language.JAVA] == 1.0
    assert Language.PYTHON not in scores


def test_parser_extracts_structure(reader, fixtures):
    files = reader.read(fixtures / "java_spring")
    account = next(f for f in files if f.name == "Account.java")
    jf = JavaParser().parse(account)
    assert jf.package == "com.acme.bank.model"
    assert "jakarta.persistence.Entity" in jf.imports
    cls = jf.units[0]
    assert cls.kind == CodeUnitKind.CLASS and cls.fqn == "com.acme.bank.model.Account"
    assert cls.extends == ["com.acme.bank.model.BaseEntity"]
    assert cls.implements == ["com.acme.bank.model.Auditable"]
    assert cls.has_annotation("Entity")
    fields = {c.name: c for c in cls.children if c.kind == CodeUnitKind.FIELD}
    assert fields["id"].has_annotation("Id") and fields["balance"].type == "double"
    nested = [c for c in cls.children if c.kind == CodeUnitKind.ENUM]
    assert nested and nested[0].fqn == "com.acme.bank.model.Account.Status"


def test_methods_signatures_lines_calls_and_docstring(reader, fixtures):
    files = reader.read(fixtures / "java_spring")
    svc = JavaParser().parse(next(f for f in files if f.name == "AccountService.java")).units[0]
    deposit = next(c for c in svc.children if c.name == "deposit")
    assert deposit.kind == CodeUnitKind.METHOD
    assert [p.type for p in deposit.parameters] == ["Long", "double"]
    assert deposit.type == "Account"
    # Declaration span includes its annotations (line 33 = @Transactional)
    assert deposit.line_start == 33 and deposit.line_end == 41
    assert deposit.has_annotation("Transactional")
    assert "repository.save" in deposit.calls and "findById" in deposit.calls
    assert deposit.docstring and "MAX_DEPOSIT" in deposit.docstring


def test_stereotypes_and_metrics(spring_analysis):
    assert _unit(spring_analysis, "com.acme.bank.controller.AccountController").stereotype == "controller"
    assert _unit(spring_analysis, "com.acme.bank.service.AccountService").stereotype == "service"
    assert _unit(spring_analysis, "com.acme.bank.repository.AccountRepository").stereotype == "repository"
    assert _unit(spring_analysis, "com.acme.bank.model.Account").stereotype == "entity"
    assert spring_analysis.metrics.stereotypes == {"controller": 1, "service": 1, "repository": 1, "entity": 1}
    assert spring_analysis.metrics.units_by_kind["class"] == 4
    assert spring_analysis.metrics.units_by_kind["interface"] == 2
    java = spring_analysis.languages[0]
    assert java.metrics.fan_in["com.acme.bank.model.Account"] >= 3
    assert java.metrics.fan_out["com.acme.bank.controller.AccountController"] == 2


def test_endpoints(spring_analysis):
    eps = {(e.http_method, e.path): e for e in spring_analysis.endpoints}
    assert set(eps) == {("GET", "/accounts"), ("GET", "/accounts/{id}"), ("POST", "/accounts/{id}/deposit"), ("DELETE", "/accounts/{id}")}
    ep = eps[("POST", "/accounts/{id}/deposit")]
    assert ep.handler == "com.acme.bank.controller.AccountController.deposit"
    assert ep.file.endswith("AccountController.java") and ep.line == 33


def test_relations(spring_analysis):
    rels = {(r.source, r.target, r.kind) for r in spring_analysis.relations}
    assert ("com.acme.bank.controller.AccountController", "com.acme.bank.service.AccountService", RelationKind.DEPENDS_ON) in rels
    assert ("com.acme.bank.service.AccountService", "com.acme.bank.repository.AccountRepository", RelationKind.DEPENDS_ON) in rels
    assert ("com.acme.bank.model.Account", "com.acme.bank.model.BaseEntity", RelationKind.EXTENDS) in rels
    assert ("com.acme.bank.controller.AccountController.deposit", "com.acme.bank.service.AccountService.deposit", RelationKind.CALLS) in rels

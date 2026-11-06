from pathlib import Path

from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import BuildSystem, CodeUnitKind, Language, RelationKind
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.llm.business_rules import BusinessRuleExtractor


def _unit(analysis, fqn):
    return next(u for u in analysis.units if u.fqn == fqn)


def test_python_project_analysis(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "python_fastapi")
    assert analysis.project_name == "inventory-api"
    assert analysis.build_systems == [BuildSystem.UV]
    assert [l.language for l in analysis.languages] == [Language.PYTHON]

    service = _unit(analysis, "app.inventory.service.InventoryService")
    assert service.stereotype == "service" and service.docstring == "Business operations on stock levels."
    adjust = next(m for m in service.children if m.name == "adjust_stock")
    assert adjust.kind == CodeUnitKind.METHOD and adjust.type == "Product"
    assert adjust.parameters[0].type == "StockMovement"
    assert {"repository.get", "repository.save", "abs"} <= set(adjust.calls)
    assert adjust.line_start == 13 and adjust.line_end == 23
    repo_field = next(c for c in service.children if c.kind == CodeUnitKind.FIELD and c.name == "repository")
    assert repo_field.type == "ProductRepository" and "injected" in repo_field.modifiers

    assert _unit(analysis, "app.inventory.models.Product").stereotype == "entity"
    assert _unit(analysis, "app.inventory.models.StockMovement").stereotype == "entity"
    assert _unit(analysis, "app.inventory.repository.ProductRepository").stereotype == "repository"
    assert _unit(analysis, "app.api.read_product").stereotype == "controller"
    assert _unit(analysis, "app.api.adjust").kind == CodeUnitKind.FUNCTION and "async" in _unit(analysis, "app.api.adjust").modifiers
    assert analysis.metrics.stereotypes["service"] == 2 and analysis.metrics.stereotypes["entity"] == 3


def test_python_endpoints_fastapi_and_flask(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "python_fastapi")
    eps = {(e.http_method, e.path): e for e in analysis.endpoints}
    assert set(eps) == {
        ("GET", "/products/{sku}"),
        ("POST", "/products/{sku}/adjust"),
        ("GET", "/health"),
        ("GET", "/legacy/products"),
        ("POST", "/legacy/products"),
    }
    assert eps[("POST", "/products/{sku}/adjust")].handler == "app.api.adjust"
    assert eps[("POST", "/products/{sku}/adjust")].framework == "FastAPI" and eps[("POST", "/products/{sku}/adjust")].line == 19
    assert eps[("GET", "/legacy/products")].framework == "Flask"


def test_python_relations_and_graph(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "python_fastapi")
    rels = {(r.source, r.target, r.kind) for r in analysis.relations}
    assert ("app.inventory.service.InventoryService", "app.inventory.repository.ProductRepository", RelationKind.DEPENDS_ON) in rels
    assert ("app.inventory.service.InventoryService", "app.inventory.models.StockMovement", RelationKind.DEPENDS_ON) in rels
    assert ("app.inventory.repository.ProductRepository", "app.inventory.models.Product", RelationKind.IMPORTS) in rels
    assert ("app.inventory.service.InventoryService.adjust_stock", "app.inventory.repository.ProductRepository.get", RelationKind.CALLS) in rels
    assert ("app.api.adjust", "app.inventory.service.InventoryService", RelationKind.DEPENDS_ON) in rels
    assert ("app.inventory.models.Product", "pydantic.BaseModel", RelationKind.EXTENDS) not in rels  # external bases are not graph edges

    kg = KnowledgeGraph.from_analysis(analysis)
    assert kg.stats()["nodes_by_kind"]["endpoint"] == 5
    assert "app.inventory.service.InventoryService" in {u.fqn for u in BusinessRuleExtractor.candidate_units(analysis)}


def test_self_analysis_dogfooding(settings):
    root = Path(__file__).resolve().parents[1]
    analysis = ProjectAnalyzer(settings).run(root)
    py = next(l for l in analysis.languages if l.language == Language.PYTHON)
    fqns = {u.fqn for u in py.units}
    assert "devanalyzer.core.analyzer.ProjectAnalyzer" in fqns
    assert "devanalyzer.graph.knowledge_graph.KnowledgeGraph" in fqns
    rels = {(r.source, r.target) for r in py.relations if r.kind == RelationKind.DEPENDS_ON}
    assert ("devanalyzer.core.analyzer.ProjectAnalyzer", "devanalyzer.core.reader.ProjectReader") in rels


def test_relative_import_in_package_init(reader, tmp_path):
    from devanalyzer.languages.python.parser import PythonParser, package_dirs

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .service import Service\n", encoding="utf-8")
    (pkg / "service.py").write_text("class Service:\n    pass\n", encoding="utf-8")
    sources = reader.read(tmp_path)
    parser = PythonParser(package_dirs([f.rel_path for f in sources]))
    init = parser.parse(next(f for f in sources if f.name == "__init__.py"))
    assert init.resolve_name("Service", {"pkg", "pkg.__init__", "pkg.service"}) == "pkg.service.Service"

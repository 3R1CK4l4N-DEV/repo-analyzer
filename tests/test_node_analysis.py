from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import CodeUnitKind, Language, RelationKind
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.llm.business_rules import BusinessRuleExtractor


def _unit(analysis, fqn):
    return next(u for u in analysis.units if u.fqn == fqn)


def test_node_project_analysis(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "node_nest")
    assert analysis.project_name == "orders-service"
    assert [l.language for l in analysis.languages] == [Language.TYPESCRIPT]
    assert {f.name for f in analysis.frameworks} >= {"NestJS", "Express", "TypeORM", "TypeScript"}

    controller = _unit(analysis, "src/orders/order.controller.OrderController")
    assert controller.stereotype == "controller" and controller.has_annotation("Controller")
    assert controller.docstring == "HTTP API for orders."
    methods = {m.name: m for m in controller.children if m.kind == CodeUnitKind.METHOD}
    assert methods["findOne"].type == "Promise<Order>"
    assert methods["findOne"].parameters[0].type == "string"
    assert "orders.findOne" in methods["findOne"].calls
    injected = next(c for c in controller.children if c.kind == CodeUnitKind.FIELD and c.name == "orders")
    assert injected.type == "OrderService" and "injected" in injected.modifiers

    assert _unit(analysis, "src/orders/order.service.OrderService").stereotype == "service"
    assert _unit(analysis, "src/orders/order.repository.OrderRepository").stereotype == "repository"
    assert _unit(analysis, "src/orders/order.entity.Order").stereotype == "entity"
    assert _unit(analysis, "src/orders/order.entity.OrderStatus").kind == CodeUnitKind.ENUM
    assert _unit(analysis, "src/orders/order.controller.CreateOrderDto").kind == CodeUnitKind.INTERFACE
    base = _unit(analysis, "src/common/base.repository.BaseRepository")
    assert "abstract" in base.modifiers
    assert _unit(analysis, "src/legacy.healthHandler").kind == CodeUnitKind.FUNCTION
    assert analysis.metrics.stereotypes == {"controller": 1, "service": 1, "repository": 2, "entity": 1}


def test_node_endpoints_nest_and_express(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "node_nest")
    eps = {(e.http_method, e.path): e for e in analysis.endpoints}
    assert set(eps) == {("GET", "/orders"), ("GET", "/orders/:id"), ("POST", "/orders"), ("GET", "/health"), ("POST", "/legacy/orders/:id/cancel")}
    assert eps[("POST", "/orders")].handler == "src/orders/order.controller.OrderController.create"
    assert eps[("POST", "/orders")].framework == "NestJS" and eps[("POST", "/orders")].line == 22
    assert eps[("GET", "/health")].handler == "src/legacy.healthHandler" and eps[("GET", "/health")].framework == "Express"


def test_node_relations_and_graph(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "node_nest")
    rels = {(r.source, r.target, r.kind) for r in analysis.relations}
    assert ("src/orders/order.controller.OrderController", "src/orders/order.service.OrderService", RelationKind.DEPENDS_ON) in rels
    assert ("src/orders/order.service.OrderService", "src/orders/order.repository.OrderRepository", RelationKind.DEPENDS_ON) in rels
    assert ("src/orders/order.repository.OrderRepository", "src/common/base.repository.BaseRepository", RelationKind.EXTENDS) in rels
    assert ("src/orders/order.controller.OrderController.create", "src/orders/order.service.OrderService.create", RelationKind.CALLS) in rels

    kg = KnowledgeGraph.from_analysis(analysis)
    path = kg.shortest_path("src/orders/order.controller.OrderController", "src/common/base.repository.BaseRepository")
    assert path and len(path) == 4
    assert kg.stats()["nodes_by_kind"]["endpoint"] == 5

    candidates = {u.fqn for u in BusinessRuleExtractor.candidate_units(analysis)}
    assert "src/orders/order.service.OrderService" in candidates

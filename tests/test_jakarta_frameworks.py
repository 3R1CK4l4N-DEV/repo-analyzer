from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import RelationKind


def _unit(analysis, fqn):
    return next(u for u in analysis.units if u.fqn == fqn)


def test_quarkus_jaxrs_panache_cdi(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "java_quarkus")
    assert analysis.versions["java"] == "21"
    assert {f.name for f in analysis.frameworks} >= {"Quarkus", "RESTEasy", "Panache"}
    assert _unit(analysis, "org.acme.inventory.ItemResource").stereotype == "controller"
    assert _unit(analysis, "org.acme.inventory.InventoryService").stereotype == "service"  # @ApplicationScoped
    assert _unit(analysis, "org.acme.inventory.ItemRepository").stereotype == "repository"  # implements PanacheRepository
    assert _unit(analysis, "org.acme.inventory.Item").stereotype == "entity"

    eps = {(e.http_method, e.path): e for e in analysis.endpoints}
    assert set(eps) == {("GET", "/items/{sku}"), ("POST", "/items/{sku}/withdraw"), ("GET", "/items")}
    assert all(e.framework == "JAX-RS" for e in eps.values())
    assert eps[("POST", "/items/{sku}/withdraw")].handler == "org.acme.inventory.ItemResource.withdraw"

    rels = {(r.source, r.target, r.kind) for r in analysis.relations}
    assert ("org.acme.inventory.ItemResource", "org.acme.inventory.InventoryService", RelationKind.DEPENDS_ON) in rels  # @Inject field
    assert ("org.acme.inventory.InventoryService.withdraw", "org.acme.inventory.ItemRepository.findBySku", RelationKind.CALLS) in rels


def test_micronaut(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "java_micronaut")
    assert {f.name for f in analysis.frameworks} >= {"Micronaut", "Micronaut Data"}
    assert _unit(analysis, "com.acme.catalog.ProductController").stereotype == "controller"
    assert _unit(analysis, "com.acme.catalog.PricingService").stereotype == "service"  # @Singleton
    assert _unit(analysis, "com.acme.catalog.ProductRepository").stereotype == "repository"
    assert _unit(analysis, "com.acme.catalog.Product").stereotype == "entity"  # @Serdeable record

    eps = {(e.http_method, e.path): e for e in analysis.endpoints}
    assert set(eps) == {("GET", "/products/{id}"), ("PUT", "/products/{id}/price")}
    assert all(e.framework == "Micronaut" for e in eps.values())
    rels = {(r.source, r.target, r.kind) for r in analysis.relations}
    assert ("com.acme.catalog.ProductController", "com.acme.catalog.PricingService", RelationKind.DEPENDS_ON) in rels


def test_spring_still_classified_as_spring(spring_analysis):
    assert {e.framework for e in spring_analysis.endpoints} == {"Spring Web"}

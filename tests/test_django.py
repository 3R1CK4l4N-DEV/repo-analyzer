from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import RelationKind


def test_django_views_models_and_urls(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "python_django")
    by = {u.fqn: u for u in analysis.units}
    assert by["shop.owners.models.Owner"].stereotype == "entity"
    assert by["shop.owners.views.AdoptView"].stereotype == "controller"
    assert by["shop.owners.views.PetViewSet"].stereotype == "controller"

    eps = {(e.http_method, e.path): e for e in analysis.endpoints}
    assert ("GET", "/api/owners") in eps and eps[("GET", "/api/owners")].handler == "shop.owners.views.OwnerList"
    assert {m for m, p in eps if p == "/api/owners/{pk}/adopt"} == {"GET", "POST"}
    assert eps[("POST", "/api/owners/{pk}/adopt")].framework == "Django"
    assert ("ANY", "/api/health") in eps and eps[("ANY", "/api/health")].handler == "shop.owners.views.health"
    assert {(m, p) for m, p in eps if "pets" in p} == {
        ("GET", "/api/pets"),
        ("POST", "/api/pets"),
        ("GET", "/api/pets/{pk}"),
        ("PUT", "/api/pets/{pk}"),
        ("PATCH", "/api/pets/{pk}"),
        ("DELETE", "/api/pets/{pk}"),
    }
    assert eps[("POST", "/api/pets")].framework == "Django REST"

    rels = {(r.source, r.target, r.kind) for r in analysis.relations}
    assert ("shop.owners.views.AdoptView", "shop.owners.models.Owner", RelationKind.IMPORTS) in rels

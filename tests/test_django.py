import shutil

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


def test_read_only_viewsets_and_multiple_url_mounts(settings, fixtures, tmp_path):
    root = tmp_path / "project"
    shutil.copytree(fixtures / "python_django", root)
    views = root / "shop/owners/views.py"
    views.write_text(views.read_text(encoding="utf-8").replace("ModelViewSet", "ReadOnlyModelViewSet"), encoding="utf-8")
    urls = root / "shop/urls.py"
    urls.write_text(
        'from django.urls import path, include\nurlpatterns = [path("api/", include("shop.owners.urls")), path("v2/", include("shop.owners.urls"))]\n',
        encoding="utf-8",
    )
    endpoints = ProjectAnalyzer(settings).run(root).endpoints
    pets = {(e.http_method, e.path) for e in endpoints if "/pets" in e.path}
    assert pets == {("GET", f"/{prefix}/pets{suffix}") for prefix in ("api", "v2") for suffix in ("", "/{pk}")}

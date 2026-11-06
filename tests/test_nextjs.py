from devanalyzer.core.analyzer import ProjectAnalyzer


def test_nextjs_file_system_routes(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "node_next")
    assert {f.name for f in analysis.frameworks} >= {"Next.js", "React"}
    eps = {(e.http_method, e.path): e for e in analysis.endpoints}
    assert ("GET", "/api/orders/{id}") in eps and ("DELETE", "/api/orders/{id}") in eps
    assert eps[("GET", "/api/orders/{id}")].framework == "Next.js (route)" and eps[("GET", "/api/orders/{id}")].handler.endswith("route.GET")
    assert ("GET", "/products/{slug}") in eps  # route group "(shop)" dropped
    assert {m for m, p in eps if p == "/api/checkout"} == {"GET", "POST"}
    assert ("GET", "/legacy") in eps and eps[("GET", "/legacy")].framework == "Next.js (page)"

import json

import pytest

from devanalyzer.core.models import EXTENSION_LANGUAGE, Endpoint, LanguageAnalysis, ProjectAnalysis, SourceFile
from devanalyzer.core.source_text import without_comments
from devanalyzer.core.workspace import WorkspaceAnalysis, WorkspaceAnalyzer, WorkspaceConfig, _norm_path, _path_matches, workspace_markdown


def test_path_matching_normalises_params_and_prefixes():
    assert _norm_path("http://billing-service/invoices/{id}") == "/invoices/*"
    assert _path_matches("http://billing-service/invoices", "/invoices")
    assert _path_matches("/api/v1/orders/42", "/orders/{id}")
    assert _path_matches("/orders/:id", "/orders/{id}")
    assert not _path_matches("/orders", "/invoices")
    assert not _path_matches("/x", "/")  # single-segment roots never match
    assert _path_matches("/orders/${orderId}", "/orders/{id}")


def test_project_names_require_unambiguous_aliases():
    resolve = WorkspaceAnalyzer._project_for_name
    names = {"orders": "orders", "orders-legacy": "orders-legacy", "preorders": "preorders"}
    assert resolve("http://orders-legacy:8080/x", names) == "orders-legacy"
    assert resolve("orders-service", names) == "orders"
    assert resolve("orders-legacy-service", names) == "orders-legacy"
    assert resolve("myorders", names) is None
    assert resolve("ord", names) is None
    assert resolve("orders.example.com", names) is None
    assert resolve("http://[broken", names) is None
    assert resolve("ORDERS_SERVICE", names) == "orders"
    assert resolve("orders_service", {"orders-service": "a", "orders_service": "b"}) == "b"


@pytest.mark.parametrize("extension", [".java", ".kt", ".ts", ".py"])
def test_workspace_ignores_comments_preserves_urls_and_evidence(settings, extension):
    language = EXTENSION_LANGUAGE[extension]
    comment = "# " if extension == ".py" else "// "
    producer = (
        comment
        + 'client.get("http://billing/invoices"); kafka.send("legacy");\n'
        + ('"""' if extension == ".py" else "/*")
        + '\n kafka.send("obsolete");\n'
        + ('"""' if extension == ".py" else "*/")
        + '\nclient.get("http://billing/invoices");\n'
        + 'kafka.send("orders.placed");\n'
        + 'client.get("http://external.example/invoices");\n'
    )
    consumer = 'bus.subscribe("orders.placed");\nbus.subscribe("legacy");\nbus.subscribe("obsolete");\n'
    analyzer = WorkspaceAnalyzer(settings)
    config = WorkspaceConfig.model_validate({"projects": [{"name": "orders", "path": "orders"}, {"name": "billing", "path": "billing"}]})
    analyzer.analyses = {
        name: ProjectAnalysis(
            project_name=name,
            project_path=name,
            languages=[
                LanguageAnalysis(
                    language=language, confidence=1, endpoints=[Endpoint(http_method="GET", path="/invoices", handler="X.get", file="X" + extension, line=1)]
                )
            ]
            if name == "billing"
            else [],
        )
        for name in ("orders", "billing")
    }
    analyzer.files = {
        name: [
            SourceFile(
                name="X" + extension,
                path="X" + extension,
                rel_path="X" + extension,
                extension=extension,
                language=language,
                content=without_comments(text, language),
            )
        ]
        for name, text in (("orders", producer), ("billing", consumer))
    }
    ws = WorkspaceAnalysis(name="test")
    http = analyzer._http_links(config, ws)
    events = analyzer._event_links(config, ws)
    assert [(e.source, e.target, e.evidence_line) for e in http] == [("orders", "billing", 5)]
    assert [(e.label, e.evidence_line) for e in events] == [("orders.placed", 6)]
    assert ws.unresolved_calls == [{"project": "orders", "url": "http://external.example/invoices", "file": "X" + extension}]


def test_workspace_accepts_explicit_config_path(tmp_path):
    cfg = tmp_path / "devanalyzer.workspace.json"
    cfg.write_text('{"projects":[{"path":"billing"}]}', encoding="utf-8")
    assert WorkspaceConfig.load(cfg) == WorkspaceConfig.load(tmp_path)


def test_workspace_discovery_and_links(settings, fixtures, tmp_path):
    config = WorkspaceConfig.load(fixtures / "workspace")
    assert [p.name for p in config.projects] == ["billing-service", "orders-service"]

    ws = WorkspaceAnalyzer(settings).run(config)
    kinds = {(link.kind, link.source, link.target, link.label) for link in ws.links}
    assert ("http", "orders-service", "billing-service", "POST /invoices") in kinds
    assert ("http", "billing-service", "orders-service", "GET /orders/{id}") in kinds
    assert ("event", "orders-service", "billing-service", "orders.placed") in kinds
    assert ("table", "billing-service", "orders-service", "customers") in kinds
    assert ws.topics["orders.placed"] == {"producers": ["orders-service"], "consumers": ["billing-service"]}
    http = next(link for link in ws.links if link.kind == "http" and link.label == "POST /invoices")
    assert http.evidence_file.endswith("OrderService.java") and http.detail == "http://billing-service/invoices"

    md = workspace_markdown(ws)
    assert "flowchart LR" in md and "orders_service -->|HTTP" in md and "topic_orders_placed" in md
    assert "| `customers`" in md or "`customers`: billing-service ↔ orders-service" in md

    # explicit config file with custom names/paths
    (tmp_path / "devanalyzer.workspace.json").write_text(
        json.dumps(
            {
                "name": "shop",
                "projects": [
                    {"name": "orders", "path": str(fixtures / "workspace" / "orders-service")},
                    {"path": str(fixtures / "workspace" / "billing-service")},
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = WorkspaceConfig.load(tmp_path)
    assert cfg.name == "shop" and [p.name for p in cfg.projects] == ["orders", "billing-service"]
    ws2 = WorkspaceAnalyzer(settings).run(cfg)
    assert any(link.kind == "http" and link.source == "orders" and link.target == "billing-service" for link in ws2.links)


def test_vault_related_services_and_lookup(settings, fixtures, tmp_path, monkeypatch):
    import shutil

    from devanalyzer.core.analyzer import ProjectAnalyzer
    from devanalyzer.core.workspace_lookup import find_workspace, links_for, project_name_in
    from devanalyzer.graph.knowledge_graph import KnowledgeGraph
    from devanalyzer.obsidian.vault import ObsidianVault

    wsdir = tmp_path / "shop"
    shutil.copytree(fixtures / "workspace", wsdir)
    ws = WorkspaceAnalyzer(settings).run(WorkspaceConfig.load(wsdir))
    (wsdir / ".devanalyzer").mkdir()
    (wsdir / ".devanalyzer" / "workspace.json").write_text(ws.model_dump_json(), encoding="utf-8")
    monkeypatch.delenv("DEVANALYZER_WORKSPACE", raising=False)

    orders = wsdir / "orders-service"
    found = find_workspace(orders)
    assert found is not None and project_name_in(found, orders) == "orders-service"
    assert {x.target for x in links_for(found, "orders-service")["outgoing"]} == {"billing-service"}

    analysis = ProjectAnalyzer(settings).run(orders)
    ObsidianVault(tmp_path / "vault").generate(analysis, KnowledgeGraph.from_analysis(analysis))
    note = (tmp_path / "vault" / "00 MOC" / "Related Services.md").read_text(encoding="utf-8")
    assert "billing-service" in note and "orders.placed" in note and "flowchart LR" in note
    assert "[[Related Services]]" in (tmp_path / "vault" / "orders-service - Home.md").read_text(encoding="utf-8")

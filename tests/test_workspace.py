import json

from devanalyzer.core.workspace import WorkspaceAnalyzer, WorkspaceConfig, _norm_path, _path_matches, workspace_markdown


def test_path_matching_normalises_params_and_prefixes():
    assert _norm_path("http://billing-service/invoices/{id}") == "/invoices/*"
    assert _path_matches("http://billing-service/invoices", "/invoices")
    assert _path_matches("/api/v1/orders/42", "/orders/{id}")
    assert _path_matches("/orders/:id", "/orders/{id}")
    assert not _path_matches("/orders", "/invoices")
    assert not _path_matches("/x", "/")  # single-segment roots never match


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

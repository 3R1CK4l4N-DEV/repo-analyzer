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

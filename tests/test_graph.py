from devanalyzer.graph.knowledge_graph import KnowledgeGraph


def test_graph_build_nodes_and_edges(spring_analysis):
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    stats = kg.stats()
    assert stats["nodes_by_kind"]["project"] == 1
    assert stats["nodes_by_kind"]["module"] == 1
    assert stats["nodes_by_kind"]["class"] == 4
    assert stats["nodes_by_kind"]["endpoint"] == 4
    assert stats["nodes_by_kind"]["dependency"] == 5
    assert stats["nodes_by_kind"]["package"] >= 6
    assert stats["edges_by_kind"]["exposes"] == 4
    assert stats["stereotypes"] == {"controller": 1, "service": 1, "repository": 1, "entity": 1}


def test_queries(spring_analysis):
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    controller = kg.find("AccountController")[0]
    assert controller == "com.acme.bank.controller.AccountController"
    targets = {t for t, k in kg.neighbors(controller, kind="depends_on")}
    assert "com.acme.bank.service.AccountService" in targets
    path = kg.shortest_path(controller, "com.acme.bank.repository.AccountRepository")
    assert path and path[0] == controller and path[-1] == "com.acme.bank.repository.AccountRepository"
    top = dict(kg.top_by_degree(3))
    assert "com.acme.bank.model.Account" in top
    sub = kg.subgraph_for(controller, depth=1)
    assert "endpoint:GET /accounts" in sub.g


def test_save_and_load_roundtrip(spring_analysis, tmp_path):
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    paths = kg.save(tmp_path)
    assert paths["json"].exists() and paths["graphml"].exists()
    loaded = KnowledgeGraph.load(tmp_path)
    assert loaded.stats() == kg.stats()
    node = loaded.g.nodes["com.acme.bank.service.AccountService"]
    assert node["stereotype"] == "service" and node["file"].endswith("AccountService.java")

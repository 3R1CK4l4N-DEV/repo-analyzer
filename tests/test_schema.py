from pathlib import Path

import pytest

from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.reader import ProjectReader
from devanalyzer.core.schema import detect_schema
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.obsidian.mermaid import er_diagram
from devanalyzer.obsidian.vault import ObsidianVault

MIG = Path(__file__).parent / "fixtures" / "migrations"


def _schema(reader, sub):
    return detect_schema(reader.read(MIG / sub))


def test_flyway_sql_applies_statements_in_order(reader):
    schema = _schema(reader, "flyway")
    assert [t.name for t in schema.tables] == ["customers", "orders"]  # legacy_tmp dropped
    customers, orders = schema.tables
    assert customers.tool == "flyway" and [c.name for c in customers.columns] == ["id", "email"]  # name dropped in V2
    assert customers.column("id").primary_key and not customers.column("email").nullable
    assert [c.name for c in orders.columns] == ["id", "customer_id", "total", "status", "notes"]
    assert orders.column("id").primary_key  # table-level PRIMARY KEY (id)
    assert orders.column("total").type == "DECIMAL(12,2)"
    assert {(fk.column, fk.ref_table, fk.ref_column) for fk in orders.foreign_keys} == {("customer_id", "customers", "id")}


def test_liquibase_xml_and_yaml(reader):
    schema = _schema(reader, "liquibase")
    names = {t.name for t in schema.tables}
    assert names == {"products", "stock_movements", "warehouses"}
    sm = schema.table("stock_movements")
    assert sm.tool == "liquibase" and sm.column("product_id").nullable is False
    assert {(fk.column, fk.ref_table) for fk in sm.foreign_keys} == {("product_id", "products")}
    wh = schema.table("warehouses")
    assert wh.column("id").primary_key and wh.foreign_keys[0].ref_table == "products"


def test_alembic(reader):
    schema = _schema(reader, "alembic")
    assert [t.name for t in schema.tables] == ["accounts", "transactions"]
    accounts = schema.table("accounts")
    assert accounts.tool == "alembic" and [c.name for c in accounts.columns] == ["id", "owner", "balance", "status"]
    assert accounts.column("id").primary_key and accounts.column("owner").nullable is False
    tx = schema.table("transactions")
    assert tx.column("id").primary_key and tx.foreign_keys[0].model_dump() == {"column": "account_id", "ref_table": "accounts", "ref_column": "id"}


def test_typeorm_migration_sql(reader):
    schema = _schema(reader, "typeorm")
    assert {t.name for t in schema.tables} == {"order", "order_item"}
    item = schema.table("order_item")
    assert item.tool == "typeorm" and item.column("id").primary_key
    assert item.foreign_keys[0].ref_table == "order"


def test_prisma(reader):
    schema = _schema(reader, "prisma")
    users, post = schema.table("users"), schema.table("post")
    assert users is not None and users.mapped_entities == ["prisma.User"]
    assert [c.name for c in users.columns] == ["id", "email"] and users.column("id").primary_key
    assert post.foreign_keys[0].model_dump() == {"column": "authorid", "ref_table": "users", "ref_column": "id"}
    assert "posts" not in [c.name for c in users.columns]  # back-reference is not a column


def test_graph_vault_and_er(settings, reader, tmp_path):
    analysis = ProjectAnalyzer(settings).run(MIG / "flyway")
    kg = KnowledgeGraph.from_analysis(analysis)
    assert kg.stats()["nodes_by_kind"]["table"] == 2 and kg.stats()["edges_by_kind"]["references"] == 1
    diagram = er_diagram(analysis.db_schema)
    assert "erDiagram" in diagram and 'customers ||--o{ orders : "customer_id"' in diagram
    ObsidianVault(tmp_path).generate(analysis, kg)
    note = (tmp_path / "60 Data" / "orders.md").read_text(encoding="utf-8")
    assert "FK → [[customers]]" in note and "| `total` | DECIMAL(12,2) | no |" in note
    assert (tmp_path / "00 MOC" / "Data Model.md").exists()


@pytest.mark.skipif(not (Path.home() / "IdeaProjects" / "spring-petclinic").exists(), reason="spring-petclinic not cloned")
def test_entity_mapping_on_petclinic(settings):
    analysis = ProjectAnalyzer(settings).run(Path.home() / "IdeaProjects" / "spring-petclinic")
    by = {t.name: t for t in analysis.db_schema.tables}
    assert set(by) == {"owners", "pets", "specialties", "types", "vet_specialties", "vets", "visits"}
    assert by["types"].mapped_entities == ["org.springframework.samples.petclinic.owner.PetType"]  # via @Table(name = "types")
    assert by["pets"].mapped_entities == ["org.springframework.samples.petclinic.owner.Pet"]
    kg = KnowledgeGraph.from_analysis(analysis)
    assert ("org.springframework.samples.petclinic.owner.Pet", "table:pets", "maps_to") in {(u, v, k) for u, v, k in kg.g.edges(keys=True)}


def test_reader_includes_sql_and_prisma(settings):
    files = ProjectReader(settings).read(MIG)
    assert {f.extension for f in files} >= {".sql", ".prisma", ".xml", ".yaml", ".py", ".ts"}

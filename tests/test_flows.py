from pathlib import Path

import pytest

from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.graph.flows import build_flows, flow_mermaid
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.llm.business_rules import BusinessRuleExtractor
from devanalyzer.obsidian.vault import ObsidianVault
from tests.test_business_rules import FakeProvider


def test_flows_follow_layers_and_rules(spring_analysis, fixtures):
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    rules = BusinessRuleExtractor(FakeProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"}).rules
    flows = {f.title: f for f in build_flows(spring_analysis, kg, rules)}
    assert set(flows) == {"GET /accounts", "GET /accounts/{id}", "POST /accounts/{id}/deposit", "DELETE /accounts/{id}"}
    deposit = flows["POST /accounts/{id}/deposit"]
    assert deposit.layers["controller"] == ["com.acme.bank.controller.AccountController"]
    assert deposit.layers["service"] == ["com.acme.bank.service.AccountService"]
    assert deposit.layers["repository"] == ["com.acme.bank.repository.AccountRepository"]
    assert "com.acme.bank.model.Account" in deposit.layers["entity"]
    assert deposit.writes is True and len(deposit.rule_ids) == 2
    assert flows["GET /accounts"].writes is False
    diagram = flow_mermaid(deposit)
    assert diagram.startswith("```mermaid\nflowchart LR") and "client([Client]) -->|POST /accounts/{id}/deposit|" in diagram
    assert 'h_com_acme_bank_service_AccountService(["AccountService"])' in diagram


@pytest.mark.skipif(not (Path.home() / "IdeaProjects" / "spring-petclinic").exists(), reason="spring-petclinic not cloned")
def test_flows_reach_tables_on_petclinic(settings):
    analysis = ProjectAnalyzer(settings).run(Path.home() / "IdeaProjects" / "spring-petclinic")
    flows = {f.title: f for f in build_flows(analysis, KnowledgeGraph.from_analysis(analysis))}
    new_pet = flows["POST /owners/{ownerId}/pets/new"]
    assert "owners" in new_pet.tables and "pets" in new_pet.tables
    assert new_pet.writes is True


def test_vault_writes_flow_notes(spring_analysis, tmp_path):
    ObsidianVault(tmp_path).generate(spring_analysis, KnowledgeGraph.from_analysis(spring_analysis))
    note = (tmp_path / "70 Flows" / "POST _accounts_{id}_deposit.md").read_text(encoding="utf-8")
    assert "flowchart LR" in note and "## Repository" in note and "writes: true" in note
    moc = (tmp_path / "00 MOC" / "Use Case Flows.md").read_text(encoding="utf-8")
    assert "| [[POST _accounts_{id}_deposit\\|POST /accounts/{id}/deposit]] | write |" in moc

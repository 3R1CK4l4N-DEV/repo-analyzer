from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.obsidian.mermaid import class_diagram, sequence_diagram
from devanalyzer.obsidian.vault import ObsidianVault
from devanalyzer.reporting.html_report import render_markdown


def _types(analysis):
    return [u for u in analysis.units if u.kind.value in ("class", "interface", "enum", "record")]


def test_class_diagram_contains_relations_and_stereotypes(spring_analysis):
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    diagram = class_diagram(_types(spring_analysis), kg, title="bank")
    assert diagram.startswith("```mermaid\n%% bank\nclassDiagram") and diagram.endswith("```")
    assert 'class com_acme_bank_service_AccountService["AccountService"]' in diagram
    assert "<<service>>" in diagram and "<<interface>>" in diagram
    assert "+deposit(Long, double) Account" in diagram
    assert "com_acme_bank_model_BaseEntity <|-- com_acme_bank_model_Account" in diagram  # extends
    assert "com_acme_bank_model_Auditable <|.. com_acme_bank_model_Account" in diagram  # implements
    assert "com_acme_bank_controller_AccountController --> com_acme_bank_service_AccountService" in diagram  # depends_on
    assert "style com_acme_bank_controller_AccountController fill:#4f86c6" in diagram


def test_sequence_diagram_follows_calls(spring_analysis):
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    deposit = next(e for e in spring_analysis.endpoints if e.path.endswith("/deposit"))
    diagram = sequence_diagram(deposit, _types(spring_analysis), kg)
    assert diagram and "sequenceDiagram" in diagram
    assert "Client->>+com_acme_bank_controller_AccountController: POST /accounts/{id}/deposit" in diagram
    assert "com_acme_bank_controller_AccountController->>+com_acme_bank_service_AccountService: deposit()" in diagram
    assert "participant com_acme_bank_service_AccountService as AccountService" in diagram
    assert diagram.index("participant") < diagram.index("Client->>+")


def test_vault_and_markdown_include_mermaid(spring_analysis, tmp_path):
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    ObsidianVault(tmp_path).generate(spring_analysis, kg)
    endpoint_note = (tmp_path / "25 Endpoints" / "POST _accounts_{id}_deposit.md").read_text(encoding="utf-8")
    assert "## Flow" in endpoint_note and "sequenceDiagram" in endpoint_note
    arch = (tmp_path / "00 MOC" / "Architecture.md").read_text(encoding="utf-8")
    assert "classDiagram" in arch
    assert "classDiagram" in render_markdown(spring_analysis, kg)

import json

from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.llm.business_rules import BusinessRuleExtractor
from devanalyzer.obsidian.vault import GENERATED_END, GENERATED_START, ObsidianVault
from tests.test_business_rules import FakeProvider


def _generate(spring_analysis, fixtures, vault_dir, folder=None):
    result = BusinessRuleExtractor(FakeProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    return ObsidianVault(vault_dir, folder).generate(spring_analysis, kg, result.rules, result.glossary)


def test_vault_structure_and_links(spring_analysis, fixtures, tmp_path):
    written = _generate(spring_analysis, fixtures, tmp_path)
    names = {p.relative_to(tmp_path).as_posix() for p in written}
    assert "bank-service - Home.md" in names
    assert "20 Classes/AccountService.md" in names
    assert "25 Endpoints/POST _accounts_{id}_deposit.md" in names
    assert "40 Glossary/Account.md" in names
    assert "canvas/architecture.canvas" in names
    assert any(n.startswith("30 Business Rules/BR-") for n in names)

    service = (tmp_path / "20 Classes" / "AccountService.md").read_text(encoding="utf-8")
    assert service.startswith("---\ntype: class\nstereotype: service")
    assert "[[AccountRepository|AccountRepository]]" in service
    assert "## Business rules" in service and "BR-" in service

    canvas = json.loads((tmp_path / "canvas" / "architecture.canvas").read_text(encoding="utf-8"))
    ids = {n["id"] for n in canvas["nodes"]}
    assert "com.acme.bank.controller.AccountController" in ids
    assert any(e["label"] == "depends_on" for e in canvas["edges"])


def test_vault_regeneration_preserves_manual_notes(spring_analysis, fixtures, tmp_path):
    _generate(spring_analysis, fixtures, tmp_path)
    note = tmp_path / "20 Classes" / "AccountService.md"
    text = note.read_text(encoding="utf-8")
    text = text.replace("## Notes\n", "## Notes\n\nMy manual insight about this service.\n")
    note.write_text(text, encoding="utf-8")

    _generate(spring_analysis, fixtures, tmp_path)
    regenerated = note.read_text(encoding="utf-8")
    assert "My manual insight about this service." in regenerated
    assert regenerated.count(GENERATED_START) == 1 and regenerated.count(GENERATED_END) == 1


def test_vault_inside_existing_vault_folder(spring_analysis, fixtures, tmp_path):
    (tmp_path / "Daily").mkdir()
    written = _generate(spring_analysis, fixtures, tmp_path, folder="Projects/bank")
    assert all(p.is_relative_to(tmp_path / "Projects" / "bank") for p in written)
    arch = (tmp_path / "Projects" / "bank" / "00 MOC" / "Architecture.md").read_text(encoding="utf-8")
    assert 'FROM "Projects/bank/20 Classes"' in arch

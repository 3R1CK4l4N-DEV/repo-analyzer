import re

from devanalyzer.core.models import RuleStatus
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.kb.store import KnowledgeBase
from devanalyzer.llm.business_rules import BusinessRuleExtractor
from devanalyzer.obsidian.sync import parse_frontmatter, sync_vault_to_kb
from devanalyzer.obsidian.vault import ObsidianVault
from tests.test_business_rules import FakeProvider


def test_parse_frontmatter_handles_quotes_and_lists():
    text = '---\ntype: business-rule\nrule_id: BR-1\nstatus: inferred\nstatement: "A rule: with colon"\ntags: [a, b]\n---\nbody'
    fm = parse_frontmatter(text)
    assert fm["rule_id"] == "BR-1" and fm["statement"] == "A rule: with colon" and fm["tags"] == "[a, b]"
    assert parse_frontmatter("no frontmatter") == {}


def test_sync_obsidian_edits_back_to_kb(spring_analysis, fixtures, tmp_path):
    result = BusinessRuleExtractor(FakeProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    kb = KnowledgeBase(tmp_path / "kb.sqlite")
    kb.ingest(result)
    kg = KnowledgeGraph.from_analysis(spring_analysis)
    vault = tmp_path / "vault"
    ObsidianVault(vault).generate(spring_analysis, kg, kb.rules(), [], [])

    r1, r2 = result.rules
    note1 = next(vault.glob(f"30 Business Rules/{r1.id}*.md"))
    note2 = next(vault.glob(f"30 Business Rules/{r2.id}*.md"))
    text = note1.read_text(encoding="utf-8")
    text = re.sub(r"^status: .*$", "status: confirmed", text, count=1, flags=re.M)
    text = re.sub(r"^review_note: .*$", 'review_note: "checked with product owner"', text, count=1, flags=re.M)
    text = re.sub(r"^statement: .*$", 'statement: "Un depósito individual no puede superar 10.000."', text, count=1, flags=re.M)
    note1.write_text(text, encoding="utf-8")
    text2 = re.sub(r"^status: .*$", "status: rejected", note2.read_text(encoding="utf-8"), count=1, flags=re.M)
    note2.write_text(text2, encoding="utf-8")

    dry = sync_vault_to_kb(vault, kb, dry_run=True)
    assert dry.changed == 3 and kb.rule(r1.id).status == RuleStatus.INFERRED

    res = sync_vault_to_kb(vault, kb)
    assert res.scanned == 2 and not res.errors
    assert {(c[0], c[2]) for c in res.status_changes} == {(r1.id, "confirmed"), (r2.id, "rejected")}
    stored = kb.rule(r1.id)
    assert stored.status == RuleStatus.CONFIRMED and stored.review_note == "checked with product owner"
    assert stored.statement == "Un depósito individual no puede superar 10.000."
    assert "[human] original statement: A single deposit cannot exceed 10,000." in stored.rationale

    # a later LLM extraction must not overwrite the human wording or decision
    kb.ingest(result)
    again = kb.rule(r1.id)
    assert again.statement.startswith("Un depósito") and again.status == RuleStatus.CONFIRMED

    # regenerating the vault keeps the edited values in the frontmatter
    ObsidianVault(vault).generate(spring_analysis, kg, kb.rules(), [], [])
    regenerated = note1.read_text(encoding="utf-8")
    assert "status: confirmed" in regenerated and 'review_note: "checked with product owner"' in regenerated
    kb.close()

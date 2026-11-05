import json

from devanalyzer.core.models import BusinessRule, Evidence
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.kb.store import KnowledgeBase
from devanalyzer.llm.consolidation import RuleConsolidator, duplicate_of, module_key
from devanalyzer.obsidian.vault import ObsidianVault
from tests.test_business_rules import FakeProvider


def _rule(rid, statement, unit, terms, file="src/main/java/com/acme/bank/service/AccountService.java", line=10):
    return BusinessRule(id=rid, statement=statement, source_unit=unit, domain_terms=terms, evidence=[Evidence(file=file, line_start=line, line_end=line + 2)])


RULES = [
    _rule("BR-1", "A deposit must be positive.", "com.acme.bank.service.AccountService", ["Account", "Deposit"]),
    _rule("BR-2", "Deposits must be greater than zero.", "com.acme.bank.service.AccountService", ["Deposit"]),
    _rule("BR-3", "A withdrawal must be positive.", "com.acme.bank.service.WithdrawalService", ["Account"]),
    _rule("BR-4", "Orders need a customer.", "src/orders/order.service.OrderService", ["Order"]),
]


class ConsolidatingProvider(FakeProvider):
    def generate(self, prompt, system=None, temperature=0.0):
        if 'Scope: module "com.acme.bank"' in prompt:
            return json.dumps(
                {
                    "duplicates": [{"canonical": "BR-1", "others": ["BR-2"]}],
                    "cross_cutting": [
                        {"statement": "Every monetary operation requires a positive amount.", "category": "validation", "covers": ["BR-1", "BR-3"]},
                        {"statement": "Hallucinated rule.", "category": "policy", "covers": ["BR-999"]},
                    ],
                    "summary": "The bank module handles deposits and withdrawals on accounts.",
                }
            )
        return json.dumps({"duplicates": [], "cross_cutting": [], "summary": "Term summary."})


def test_module_key_java_and_module_style_ids():
    assert module_key("com.acme.bank.service.AccountService", 3) == "com.acme.bank"
    assert module_key("com.acme.bank.service.AccountService", 0) == "com.acme.bank.service"
    assert module_key("src/orders/order.service.OrderService", 3) == "src.orders"
    assert module_key(None, 3) == "(root)"


def test_grouping_and_consolidation(tmp_path):
    consolidator = RuleConsolidator(ConsolidatingProvider(), "bank", cache_dir=tmp_path / "c")
    keys = {(k, key) for k, key, _ in consolidator.group(RULES, by="both", depth=3)}
    assert ("module", "com.acme.bank") in keys and ("term", "Account") in keys and ("term", "Deposit") in keys
    assert ("module", "src.orders") not in keys  # single rule -> below min_size

    groups = consolidator.consolidate(RULES, by="both", depth=3, workers=2)
    module = next(g for g in groups if g.kind == "module")
    assert module.summary.startswith("The bank module")
    assert [c.statement for c in module.cross_cutting] == ["Every monetary operation requires a positive amount."]  # hallucinated one dropped
    assert module.duplicates == [["BR-1", "BR-2"]]
    assert duplicate_of(RULES[1]) == "BR-1" and duplicate_of(RULES[0]) is None

    with KnowledgeBase(tmp_path / "kb.sqlite") as kb:
        kb.save_groups("bank", groups, RULES)
        stored = kb.groups(project="bank", kind="module")
        assert stored[0].key == "com.acme.bank" and stored[0].cross_cutting[0].covers == ["BR-1", "BR-3"]


def test_vault_renders_domain_notes(spring_analysis, tmp_path):
    groups = RuleConsolidator(ConsolidatingProvider(), "bank").consolidate(RULES, by="module", depth=3)
    written = ObsidianVault(tmp_path).generate(spring_analysis, KnowledgeGraph.from_analysis(spring_analysis), RULES, [], [], groups)
    domain = next(p for p in written if p.parent.name == "35 Domains")
    text = domain.read_text(encoding="utf-8")
    assert "# com.acme.bank" in text and "Every monetary operation" in text and "[[BR-1 A deposit must be positive.|BR-1]]" in text
    moc = (tmp_path / "00 MOC" / "Business Rules.md").read_text(encoding="utf-8")
    assert "## Cross-cutting rules" in moc and "[[com.acme.bank|com.acme.bank]]" in moc

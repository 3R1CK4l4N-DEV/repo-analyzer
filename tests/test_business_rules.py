import json

import pytest

from devanalyzer.core.models import RuleStatus
from devanalyzer.kb.store import KnowledgeBase
from devanalyzer.llm.business_rules import BusinessRuleExtractor, parse_llm_json
from devanalyzer.llm.provider import LLMProvider


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self):
        super().__init__("fake-model")
        self.calls = 0

    def generate(self, prompt, system=None, temperature=0.0):
        self.calls += 1
        if "AccountService" in prompt:
            return json.dumps(
                {
                    "rules": [
                        {
                            "statement": "A single deposit cannot exceed 10,000.",
                            "rationale": "MAX_DEPOSIT constant guards deposit()",
                            "category": "validation",
                            "domain_terms": ["Deposit", "Account"],
                            "line_start": 35,
                            "line_end": 37,
                            "confidence": 0.9,
                        },
                        {
                            "statement": "An account can only be closed when its balance is zero.",
                            "category": "state transition",
                            "domain_terms": ["Account"],
                            "line_start": 46,
                            "line_end": 48,
                            "confidence": 0.85,
                        },
                    ],
                    "glossary": [{"term": "Account", "definition": "A customer's bank account holding a balance."}],
                }
            )
        return '```json\n{"rules": [], "glossary": []}\n```'


def test_parse_llm_json_tolerates_fences_and_prose():
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_llm_json('Sure! Here it is: {"rules": []} hope it helps') == {"rules": []}


def test_candidate_units_prefer_logic_bearing_types(spring_analysis):
    fqns = {u.fqn for u in BusinessRuleExtractor.candidate_units(spring_analysis)}
    assert "com.acme.bank.service.AccountService" in fqns
    assert "com.acme.bank.controller.AccountController" in fqns
    assert "com.acme.bank.repository.AccountRepository" not in fqns  # no methods, repository stereotype


def test_extract_builds_rules_with_evidence_and_cache(spring_analysis, fixtures, tmp_path):
    provider = FakeProvider()
    extractor = BusinessRuleExtractor(provider, cache_dir=tmp_path / "cache")
    result = extractor.extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    assert result.units_analyzed == 1 and not result.errors
    assert len(result.rules) == 2
    rule = result.rules[0]
    assert rule.id.startswith("BR-") and rule.source_unit == "com.acme.bank.service.AccountService"
    assert rule.category == "validation" and rule.status == RuleStatus.INFERRED
    ev = rule.evidence[0]
    assert ev.file.endswith("AccountService.java") and (ev.line_start, ev.line_end) == (35, 37)
    assert "MAX_DEPOSIT" in ev.snippet
    assert result.rules[1].category == "state_transition"
    assert [g.term for g in result.glossary] == ["Account", "Deposit"]
    assert result.glossary[0].definition.startswith("A customer")

    first_calls = provider.calls
    extractor.extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    assert provider.calls == first_calls  # served from cache


def test_extract_clamps_out_of_range_lines(spring_analysis, fixtures):
    class BadLines(FakeProvider):
        def generate(self, prompt, system=None, temperature=0.0):
            return json.dumps({"rules": [{"statement": "x", "line_start": 999, "line_end": 1000, "confidence": 2}], "glossary": []})

    result = BusinessRuleExtractor(BadLines()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    rule = result.rules[0]
    assert rule.confidence == 1.0
    assert rule.evidence[0].line_start == 11 and rule.evidence[0].line_end == 21


def test_parallel_extraction_matches_sequential_and_is_faster(spring_analysis, fixtures, tmp_path):
    import time

    class SlowProvider(FakeProvider):
        def generate(self, prompt, system=None, temperature=0.0):
            time.sleep(0.15)
            return super().generate(prompt, system, temperature)

    seq = BusinessRuleExtractor(SlowProvider()).extract(spring_analysis, fixtures / "java_spring")
    t = time.perf_counter()
    par = BusinessRuleExtractor(SlowProvider(), cache_dir=tmp_path / "c").extract(spring_analysis, fixtures / "java_spring", workers=4)
    elapsed = time.perf_counter() - t
    assert par.units_analyzed == seq.units_analyzed >= 3
    assert [r.id for r in par.rules] == [r.id for r in seq.rules]
    assert [g.term for g in par.glossary] == [g.term for g in seq.glossary]
    assert elapsed < 0.15 * seq.units_analyzed * 0.75  # clearly faster than sequential
    assert len(list((tmp_path / "c").glob("*.json"))) == par.units_analyzed  # cache written atomically, no .tmp leftovers
    assert not list((tmp_path / "c").glob("*.tmp"))


def test_verify_pass_adjusts_rules(spring_analysis, fixtures, tmp_path):
    class Verifier(FakeProvider):
        def generate(self, prompt, system=None, temperature=0.0):
            if "auditing a BUSINESS RULE" in prompt:
                if "cannot exceed 10,000" in prompt:
                    return json.dumps({"verdict": "supported", "confidence": 0.95, "reason": "MAX_DEPOSIT check"})
                return json.dumps({"verdict": "technical", "confidence": 0.2, "reason": "not a domain rule"})
            return super().generate(prompt, system, temperature)

    extractor = BusinessRuleExtractor(Verifier())
    result = extractor.extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    result = extractor.verify(result, root=fixtures / "java_spring", workers=2)
    kept, rejected = result.rules
    assert kept.status == RuleStatus.INFERRED and kept.confidence == 0.95 and "verdict/supported" in kept.tags
    assert rejected.status == RuleStatus.REJECTED and "verdict/technical" in rejected.tags and "[verifier]" in rejected.rationale

    with KnowledgeBase(tmp_path / "kb.sqlite") as kb:
        kb.ingest(result)
        assert kb.rule(rejected.id).status == RuleStatus.REJECTED
        # a human confirmation must survive a later automated rejection
        kb.set_status(rejected.id, RuleStatus.CONFIRMED, "it is a real rule")
        kb.ingest(result)
        assert kb.rule(rejected.id).status == RuleStatus.CONFIRMED


def test_kb_ingest_dedups_and_preserves_review(spring_analysis, fixtures, tmp_path):
    result = BusinessRuleExtractor(FakeProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    with KnowledgeBase(tmp_path / "kb.sqlite") as kb:
        first = kb.ingest(result)
        assert (first["new"], first["seen"], first["changed"]) == (2, 0, 0)
        rule_id = result.rules[0].id
        assert kb.set_status(rule_id, RuleStatus.CONFIRMED, "verified with product owner")

        second = kb.ingest(result)
        assert (second["new"], second["seen"], second["changed"]) == (0, 2, 0)
        assert kb.rule(rule_id).status == RuleStatus.CONFIRMED

        result.rules[0].confidence = 0.3
        result.rules[0].category = "policy"
        third = kb.ingest(result)
        assert third["changed"] == 1
        assert [h["change"] for h in kb.history(rule_id)] == ["new", "changed"]

        assert len(kb.rules(status="confirmed")) == 1
        assert len(kb.rules(search="balance")) == 1
        assert [t.term for t in kb.terms()] == ["Account", "Deposit"]
        summary = kb.summary()
        assert summary["rules"] == 2 and summary["runs"] == 3


def test_glossary_terms_merge_case_insensitively(spring_analysis, fixtures):
    class SpellingProvider(FakeProvider):
        def generate(self, prompt, system=None, temperature=0.0):
            unit = prompt.split("Unit: ", 1)[1].split()[0]
            spelling = "Entidad" if unit.endswith("AccountService") else "entidad"
            return json.dumps(
                {
                    "rules": [{"statement": f"{unit} owns one {spelling}", "domain_terms": [spelling], "line_start": 1, "line_end": 2}],
                    "glossary": [{"term": spelling, "definition": "A domain object" if spelling == "entidad" else ""}],
                }
            )

    result = BusinessRuleExtractor(SpellingProvider()).extract(spring_analysis, fixtures / "java_spring")
    assert len(result.rules) >= 2
    assert [t.term for t in result.glossary] == ["Entidad"]
    term = result.glossary[0]
    assert term.definition == "A domain object" and sorted(term.rules) == sorted(r.id for r in result.rules)
    assert all(r.domain_terms == ["Entidad"] for r in result.rules)


def test_kb_migrates_legacy_rule_ids_keeping_human_review(tmp_path):
    from devanalyzer.core.models import BusinessRule, Evidence, GlossaryTerm, RuleExtractionResult, RuleGroup
    from devanalyzer.llm.business_rules import legacy_rule_id, rule_id

    file = "src/Account.java"
    statement, sibling = "The balance of an active account must be >= 10.", "The balance of an active account must be <= 10."
    old_id, new_id = legacy_rule_id(statement, file), rule_id(statement, file)
    assert old_id != new_id and legacy_rule_id(sibling, file) == old_id

    def extraction(rules):
        return RuleExtractionResult(
            project_name="bank", model="m", rules=rules, glossary=[GlossaryTerm(term="Balance", definition="", rules=[r.id for r in rules])]
        )

    evidence = [Evidence(file=file, line_start=10, line_end=12)]
    stored = BusinessRule(id=old_id, statement=statement, category="validation", source_unit="Account", domain_terms=["Balance"], evidence=evidence)
    with KnowledgeBase(tmp_path / "kb.sqlite") as kb:
        kb.ingest(extraction([stored]))
        kb.set_statement(old_id, "Active accounts keep at least 10 in balance.")
        kb.set_status(old_id, RuleStatus.CONFIRMED, "checked with finance")
        other = BusinessRule(id="BR-other", statement="x", source_unit="Account", tags=[f"duplicate-of/{old_id}"], evidence=evidence)
        kb.ingest(extraction([other]))
        kb.save_groups("bank", [RuleGroup(kind="module", key="bank", rule_ids=[old_id, "BR-other"], duplicates=[[old_id, "BR-other"]])], [])
        kb.conn.execute("CREATE TABLE embeddings (project TEXT, doc_id TEXT, kind TEXT)")
        kb.conn.execute("INSERT INTO embeddings VALUES ('bank', ?, 'business_rule')", (f"rule:{old_id}",))

        # Re-extraction with the new ID scheme: both symbol variants now survive, and the review follows the row.
        fresh = [
            BusinessRule(id=rule_id(s, file), statement=s, category="validation", source_unit="Account", domain_terms=["Balance"], evidence=evidence)
            for s in (statement, sibling)
        ]
        counts = kb.ingest(extraction(fresh))
        assert (counts["new"], counts["seen"], counts["changed"]) == (1, 1, 0)
        assert kb.rule(old_id) is None
        migrated = kb.rule(new_id)
        assert migrated.status == RuleStatus.CONFIRMED and migrated.review_note == "checked with finance"
        assert migrated.statement == "Active accounts keep at least 10 in balance."
        assert [h["change"] for h in kb.history(new_id)] == ["new"] and kb.history(old_id) == []
        assert kb.terms(project="bank")[0].rules == sorted([new_id, "BR-other", rule_id(sibling, file)])
        assert kb.rule("BR-other").tags == [f"duplicate-of/{new_id}"]
        group = kb.groups(project="bank")[0]
        assert group.rule_ids == [new_id, "BR-other"] and group.duplicates == [[new_id, "BR-other"]]
        assert kb.conn.execute("SELECT doc_id FROM embeddings").fetchone()[0] == f"rule:{new_id}"
        assert kb.rules(project="bank", status="inferred")[0].statement == sibling
        assert kb.ingest(extraction(fresh))["seen"] == 2


def test_kb_merges_legacy_row_when_both_id_variants_exist(tmp_path):
    from devanalyzer.core.models import BusinessRule, Evidence, GlossaryTerm, RuleExtractionResult, RuleGroup
    from devanalyzer.llm.business_rules import legacy_rule_id, rule_id

    file, statement = "src/Account.java", "The balance of an active account must be >= 10."
    old_id, new_id = legacy_rule_id(statement, file), rule_id(statement, file)
    evidence = [Evidence(file=file, line_start=10, line_end=12)]

    def extraction(rules):
        return RuleExtractionResult(
            project_name="bank", model="m", rules=rules, glossary=[GlossaryTerm(term="Balance", definition="", rules=[r.id for r in rules])]
        )

    with KnowledgeBase(tmp_path / "kb.sqlite") as kb:
        kb.ingest(extraction([BusinessRule(id=old_id, statement=statement, source_unit="Account", evidence=evidence)]))
        kb.set_status(old_id, RuleStatus.CONFIRMED, "checked with finance")
        # Without evidence no legacy lookup happens, so the new id lands as a second, verifier-rejected row.
        verified = BusinessRule(id=new_id, statement=statement, source_unit="Account", status=RuleStatus.REJECTED, tags=[f"duplicate-of/{old_id}"])
        kb.ingest(extraction([verified]))
        kb.save_groups("bank", [RuleGroup(kind="module", key="bank", rule_ids=[old_id, new_id], duplicates=[[old_id, new_id]])], [])
        kb.conn.execute("CREATE TABLE embeddings (project TEXT, doc_id TEXT, PRIMARY KEY (project, doc_id))")
        kb.conn.executemany("INSERT INTO embeddings VALUES ('bank', ?)", [(f"rule:{old_id}",), (f"rule:{new_id}",)])
        assert kb.summary()["rules"] == 2

        fresh = BusinessRule(id=new_id, statement=statement, source_unit="Account", evidence=evidence)
        counts = kb.ingest(extraction([fresh]))
        assert (counts["new"], counts["seen"], counts["changed"]) == (0, 0, 1)
        assert kb.summary()["rules"] == 1 and kb.rule(old_id) is None
        merged = kb.rule(new_id)
        assert merged.status == RuleStatus.CONFIRMED and merged.review_note == "checked with finance"
        assert merged.tags == []  # not a duplicate of itself
        assert kb.terms(project="bank")[0].rules == [new_id]
        group = kb.groups(project="bank")[0]
        assert group.rule_ids == [new_id] and group.duplicates == [[new_id]]
        assert [r[0] for r in kb.conn.execute("SELECT doc_id FROM embeddings").fetchall()] == [f"rule:{new_id}"]
        assert kb.history(old_id) == []
        assert kb.ingest(extraction([fresh]))["seen"] == 1


def test_merge_near_duplicates_keeps_all_evidence():
    from devanalyzer.core.models import BusinessRule, Evidence
    from devanalyzer.llm.business_rules import merge_near_duplicates

    a = BusinessRule(
        id="A",
        statement="El nombre del perro no puede repetirse para el mismo propietario.",
        category="validation",
        source_unit="X",
        confidence=0.8,
        evidence=[Evidence(file="f.java", line_start=111, line_end=112)],
    )
    b = BusinessRule(
        id="B",
        statement="El nombre del perro no puede repetirse para el mismo propietario",
        category="validation",
        source_unit="X",
        confidence=0.9,
        evidence=[Evidence(file="f.java", line_start=150, line_end=151)],
    )
    c = BusinessRule(
        id="C",
        statement="La fecha de nacimiento no puede ser futura.",
        category="validation",
        source_unit="X",
        evidence=[Evidence(file="f.java", line_start=115, line_end=116)],
    )
    d = BusinessRule(
        id="D",
        statement="El telefono debe tener 10 digitos",
        category="validation",
        source_unit="Y",
        evidence=[Evidence(file="g.java", line_start=63, line_end=64)],
    )
    e = BusinessRule(
        id="E",
        statement="El telefono debe tener exactamente 10 digitos",
        category="validation",
        source_unit="Z",
        evidence=[Evidence(file="g.java", line_start=63, line_end=65)],
    )
    out = merge_near_duplicates([a, b, c, d, e])
    assert [r.id for r in out] == ["A", "C", "D"]
    assert len(out[0].evidence) == 2 and out[0].confidence == 0.9 and "merged" in out[0].tags
    assert {(ev.line_start, ev.line_end) for ev in out[2].evidence} == {(63, 64), (63, 65)}


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Customers must approve the transfer before closing the account.", "Customers must not approve the transfer before closing the account."),
        ("La cuenta puede cerrarse cuando existe saldo pendiente.", "La cuenta no puede cerrarse cuando existe saldo pendiente."),
        ("An account must have at least 1 active owner.", "An account must have at least 2 active owners."),
        ("An account must have at least 10 active owners.", "An account must have at least 20 active owners."),
        ("The balance of an active account must be >= 10.", "The balance of an active account must be <= 10."),
        ("The balance of an active account must be above 10.", "The balance of an active account must be below 10."),
        ("Customers must approve the transfer before closing the account.", "Customers may approve the transfer before closing the account."),
        ("Customers can approve the transfer before closing the account.", "Customers can't approve the transfer before closing the account."),
        ("Transfers are not allowed when the account is frozen.", "Transfers are allowed when the account is not frozen."),
    ],
)
def test_dedup_preserves_different_business_constraints(first, second):
    from devanalyzer.core.models import BusinessRule, Evidence
    from devanalyzer.llm.business_rules import merge_near_duplicates

    rules = [
        BusinessRule(id=str(i), statement=statement, source_unit="AccountService", evidence=[Evidence(file="f.java", line_start=10, line_end=15)])
        for i, statement in enumerate((first, second))
    ]
    assert [r.statement for r in merge_near_duplicates(rules)] == [first, second]


def test_dedup_preserves_categories_and_glossary_references(spring_analysis, fixtures):
    from devanalyzer.core.models import BusinessRule
    from devanalyzer.llm.business_rules import merge_near_duplicates

    statement = "An account must have 10 digits."
    validation = BusinessRule(id="A", statement=statement, category="validation")
    authorization = BusinessRule(id="B", statement=statement, category="authorization")
    assert len(merge_near_duplicates([validation, authorization])) == 2

    class DuplicateProvider(FakeProvider):
        def generate(self, prompt, system=None, temperature=0.0):
            return json.dumps(
                {
                    "rules": [
                        {"statement": statement, "category": "validation", "domain_terms": ["Account"], "line_start": 35, "line_end": 37},
                        {
                            "statement": "An account must have exactly 10 digits.",
                            "category": "validation",
                            "domain_terms": ["Account", "Number"],
                            "line_start": 35,
                            "line_end": 38,
                        },
                        {"statement": statement, "category": "validation", "domain_terms": ["Audit"], "line_start": 46, "line_end": 48},
                    ],
                    "glossary": [],
                }
            )

    result = BusinessRuleExtractor(DuplicateProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    assert len(result.rules) == 1
    assert len(result.rules[0].evidence) == 3
    assert {term.term for term in result.glossary} == {"Account", "Number", "Audit"}
    assert all(term.rules == [result.rules[0].id] for term in result.glossary)


@pytest.mark.parametrize(
    ("first", "second"), [(">= 10", "<= 10"), ("-1", "1"), ("1%", "1"), ("$1", "€1"), ("1% plus 2", "1 plus 2%"), ("-1 plus 2", "1 plus -2")]
)
def test_rule_ids_keep_meaningful_symbols(first, second):
    from devanalyzer.llm.business_rules import rule_id

    assert rule_id(f"The required account balance is {first}.", "f.java") != rule_id(f"The required account balance is {second}.", "f.java")
    assert rule_id("A deposit requires approval.", "f.java") == rule_id("a deposit requires approval", "f.java")

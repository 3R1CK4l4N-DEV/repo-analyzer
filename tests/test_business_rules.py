import json

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
        return "```json\n{\"rules\": [], \"glossary\": []}\n```"


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

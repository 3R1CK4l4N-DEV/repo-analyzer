from devanalyzer.core.models import BusinessRule, Evidence, RuleStatus
from devanalyzer.llm.business_rules import BusinessRuleExtractor
from devanalyzer.llm.evaluation import GoldSet, evaluate
from tests.test_business_rules import FakeProvider

SERVICE = "src/main/java/com/acme/bank/service/AccountService.java"


def _rule(rid, statement, file, start, end, category="validation", status=RuleStatus.INFERRED):
    return BusinessRule(id=rid, statement=statement, category=category, status=status, evidence=[Evidence(file=file, line_start=start, line_end=end)])


def test_eval_perfect_match_on_fake_extractor(spring_analysis, fixtures):
    result = BusinessRuleExtractor(FakeProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    report = evaluate(result.rules, GoldSet.load(fixtures / "java_spring" / "gold_rules.json"), model="fake")
    assert (report.true_positives, report.false_positives, report.false_negatives) == (2, 0, 0)
    assert report.precision == report.recall == report.f1 == 1.0
    assert report.category_accuracy == 1.0
    assert "P=100% R=100% F1=100%" in report.summary_line()


def test_eval_counts_fp_fn_noise_and_ignores_rejected(fixtures):
    gold = GoldSet.load(fixtures / "java_spring" / "gold_rules.json")
    gold.not_rules.append(type(gold.rules[0])(id="N1", file=SERVICE, line_start=22, line_end=24, keywords=["findAll"]))
    rules = [
        _rule("A", "Deposits above 10,000 are not allowed", SERVICE, 35, 36),  # matches G1 (within tolerance)
        _rule("B", "Accounts are listed via findAll", SERVICE, 22, 23, category="general"),  # noise
        _rule("C", "Something unrelated", "src/main/java/com/acme/bank/model/Account.java", 5, 9),  # spurious
        _rule("D", "An account can only be closed when balance is zero", SERVICE, 44, 48, status=RuleStatus.REJECTED),  # rejected -> ignored
    ]
    report = evaluate(rules, gold, model="x")
    assert report.true_positives == 1 and report.false_positives == 2 and report.false_negatives == 1
    assert report.noise_reported == 1
    assert report.missed == ["G2: An account can only be closed when its balance is zero"]
    assert {s.split(":")[0] for s in report.spurious} == {"B", "C"}

    with_rejected = evaluate(rules, gold, model="x", include_rejected=True)
    assert with_rejected.true_positives == 2 and with_rejected.recall == 1.0


def test_eval_keyword_threshold_and_accents():
    gold = GoldSet.model_validate({"rules": [{"id": "G", "file": "a.py", "line_start": 1, "line_end": 3, "keywords": ["depósito", "10000"]}]})
    good = _rule("ok", "El DEPOSITO no supera 10.000", "a.py", 1, 2)
    assert evaluate([good], gold).true_positives == 1  # accent/case-insensitive, 1 of 2 keywords meets the 0.5 threshold
    bad = _rule("no", "Totalmente otra cosa", "a.py", 1, 2)
    assert evaluate([bad], gold).true_positives == 0

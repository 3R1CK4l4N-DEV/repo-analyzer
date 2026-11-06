import hashlib
import math

from devanalyzer.kb.semantic import SemanticIndex, build_documents
from devanalyzer.kb.store import KnowledgeBase
from devanalyzer.llm.business_rules import BusinessRuleExtractor
from tests.test_business_rules import FakeProvider


class HashEmbeddings:
    """Deterministic bag-of-words embedding: shared tokens -> higher cosine. No network."""

    name = "fake"
    model = "hash-64"

    def embed(self, texts):
        out = []
        for t in texts:
            vec = [0.0] * 64
            for tok in t.lower().replace("(", " ").replace(")", " ").replace(",", " ").replace(".", " ").split():
                vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def test_documents_cover_all_knowledge_kinds(spring_analysis, fixtures):
    result = BusinessRuleExtractor(FakeProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    docs = build_documents(spring_analysis, result.rules, result.glossary)
    kinds = {d.kind for d in docs}
    assert kinds >= {"class", "interface", "method", "endpoint", "business_rule", "glossary_term"}
    svc = next(d for d in docs if d.doc_id == "com.acme.bank.service.AccountService")
    assert "stereotype service" in svc.text and "deposit(Long, double)" in svc.text


def test_index_is_incremental_and_search_ranks_by_meaning(spring_analysis, fixtures, tmp_path):
    result = BusinessRuleExtractor(FakeProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    docs = build_documents(spring_analysis, result.rules, result.glossary)
    with KnowledgeBase(tmp_path / "kb.sqlite") as kb:
        idx = SemanticIndex(kb, HashEmbeddings(), "bank-service")
        first = idx.index(docs)
        assert first["indexed"] == len(docs) and first["unchanged"] == 0
        second = idx.index(docs)
        assert second["indexed"] == 0 and second["unchanged"] == len(docs)
        docs[0].text += " changed"
        third = idx.index(docs[:-1])
        assert third["indexed"] == 1 and third["removed"] == 1
        assert idx.count() == len(docs) - 1

        hits = idx.search("deposit cannot exceed 10,000", limit=5)
        assert hits and hits[0].kind == "business_rule" and "10,000" in hits[0].snippet
        only_methods = idx.search("deposit", limit=5, kinds={"method"})
        assert only_methods and all(h.kind == "method" for h in only_methods)
        assert any(h.doc_id.endswith("AccountService.deposit") for h in only_methods)


def test_extractor_uses_retriever_as_context(spring_analysis, fixtures):
    from devanalyzer.kb.semantic import Hit

    calls = []

    def retriever(query, limit):
        calls.append(query)
        return [
            Hit(doc_id="rule:BR-x", kind="business_rule", title="BR-x", score=0.9, file=None, line=None, snippet="Deposits are capped at 10,000."),
            Hit(doc_id="com.acme.bank.service.AccountService", kind="class", title="self", score=1.0, file=None, line=None, snippet="ignored (self)"),
        ]

    ex = BusinessRuleExtractor(FakeProvider(), retriever=retriever)
    unit = next(u for u in spring_analysis.units if u.name == "AccountService")
    prompt = ex._prompt(spring_analysis, unit, "code")
    assert calls and "AccountService" in calls[0]
    assert "semantically related knowledge" in prompt and "Deposits are capped" in prompt and "ignored (self)" not in prompt

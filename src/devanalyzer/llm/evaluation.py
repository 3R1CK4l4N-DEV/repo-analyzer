"""Precision/recall evaluation of extracted business rules against a hand-labelled gold set.

Gold file format (`<project>/.devanalyzer/gold_rules.json` or any path):
{
  "rules": [
    {"id": "G1", "statement": "A single deposit cannot exceed 10,000",
     "file": "src/main/java/.../AccountService.java", "line_start": 34, "line_end": 37,
     "keywords": ["deposit", "10000"], "category": "validation"}
  ],
  "not_rules": [   # optional: known technical/noise statements that must NOT be reported
    {"file": "...", "line_start": 30, "line_end": 34, "keywords": ["oups", "exception"]}
  ]
}

Matching is deterministic (no LLM): a prediction matches a gold rule when the evidence files
agree, the line ranges overlap (with tolerance) and enough keywords appear in the statement.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel, Field

from devanalyzer.core.models import BusinessRule, RuleStatus


class GoldRule(BaseModel):
    id: str
    statement: str = ""
    file: str
    line_start: int
    line_end: int
    keywords: list[str] = Field(default_factory=list)
    category: str | None = None


class GoldSet(BaseModel):
    rules: list[GoldRule] = Field(default_factory=list)
    not_rules: list[GoldRule] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> GoldSet:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for i, r in enumerate(data.get("not_rules", [])):
            r.setdefault("id", f"N{i + 1}")
        return cls.model_validate(data)


class Match(BaseModel):
    gold_id: str
    rule_id: str
    statement: str
    score: float


class EvalReport(BaseModel):
    model: str
    gold_total: int
    predicted_total: int
    true_positives: int
    false_positives: int
    false_negatives: int
    noise_reported: int
    precision: float
    recall: float
    f1: float
    matches: list[Match] = Field(default_factory=list)
    missed: list[str] = Field(default_factory=list)
    spurious: list[str] = Field(default_factory=list)
    category_accuracy: float | None = None

    def summary_line(self) -> str:
        return f"P={self.precision:.0%} R={self.recall:.0%} F1={self.f1:.0%} (TP={self.true_positives} FP={self.false_positives} FN={self.false_negatives}, noise={self.noise_reported})"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _keyword_score(statement: str, keywords: list[str], rationale: str | None = None) -> float:
    if not keywords:
        return 1.0
    hay = _norm(statement + " " + (rationale or ""))
    # "a|b" means any alternative satisfies the keyword (useful across languages/synonyms).
    hits = sum(1 for k in keywords if any(_norm(alt) in hay for alt in k.split("|")))
    return hits / len(keywords)


def _overlap(a: tuple[int, int], b: tuple[int, int], tolerance: int) -> bool:
    return a[0] <= b[1] + tolerance and b[0] <= a[1] + tolerance


def evaluate(
    rules: list[BusinessRule],
    gold: GoldSet,
    *,
    model: str = "",
    line_tolerance: int = 3,
    min_keyword_score: float = 0.5,
    include_rejected: bool = False,
) -> EvalReport:
    preds = [r for r in rules if include_rejected or r.status != RuleStatus.REJECTED]
    candidates: list[tuple[float, int, int]] = []  # (score, gold_idx, pred_idx)
    for gi, g in enumerate(gold.rules):
        for pi, p in enumerate(preds):
            for ev in p.evidence:
                if not ev.file.endswith(g.file) and not g.file.endswith(ev.file):
                    continue
                if not _overlap((ev.line_start, ev.line_end), (g.line_start, g.line_end), line_tolerance):
                    continue
                ks = _keyword_score(p.statement, g.keywords, p.rationale)
                if ks >= min_keyword_score:
                    candidates.append((ks, gi, pi))
                break
    # Greedy one-to-one assignment, best keyword score first.
    candidates.sort(reverse=True)
    used_gold: set[int] = set()
    used_pred: set[int] = set()
    matches: list[Match] = []
    for score, gi, pi in candidates:
        if gi in used_gold or pi in used_pred:
            continue
        used_gold.add(gi)
        used_pred.add(pi)
        matches.append(Match(gold_id=gold.rules[gi].id, rule_id=preds[pi].id, statement=preds[pi].statement, score=round(score, 2)))

    noise = 0
    for pi, p in enumerate(preds):
        if pi in used_pred:
            continue
        for n in gold.not_rules:
            if any(ev.file.endswith(n.file) and _overlap((ev.line_start, ev.line_end), (n.line_start, n.line_end), line_tolerance) for ev in p.evidence):
                if _keyword_score(p.statement, n.keywords, p.rationale) >= min_keyword_score:
                    noise += 1
                    break

    tp = len(matches)
    fp = len(preds) - tp
    fn = len(gold.rules) - tp
    precision = tp / len(preds) if preds else 0.0
    recall = tp / len(gold.rules) if gold.rules else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    cat_pairs = [(gold.rules[gi].category, preds[pi].category) for _, gi, pi in candidates if gold.rules[gi].category and gi in used_gold and pi in used_pred]
    cat_acc = sum(1 for g, p in cat_pairs if g == p) / len(cat_pairs) if cat_pairs else None
    return EvalReport(
        model=model,
        gold_total=len(gold.rules),
        predicted_total=len(preds),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        noise_reported=noise,
        precision=round(precision, 3),
        recall=round(recall, 3),
        f1=round(f1, 3),
        matches=matches,
        missed=[g.id + ": " + g.statement for gi, g in enumerate(gold.rules) if gi not in used_gold],
        spurious=[p.id + ": " + p.statement for pi, p in enumerate(preds) if pi not in used_pred],
        category_accuracy=round(cat_acc, 3) if cat_acc is not None else None,
    )

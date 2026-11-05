"""Second-level analysis: group rules by module or domain term, merge duplicates and
derive cross-cutting rules plus a business summary per group."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from devanalyzer.core.models import BusinessRule, CrossCuttingRule, RuleGroup, RuleStatus
from devanalyzer.llm.business_rules import BusinessRuleExtractor
from devanalyzer.llm.provider import LLMProvider

PROMPT_PATH = Path(__file__).parent / "prompts" / "consolidate.md"
DUPLICATE_TAG = "duplicate-of/"


class _Dup(BaseModel):
    canonical: str
    others: list[str] = Field(default_factory=list)


class _LLMConsolidation(BaseModel):
    duplicates: list[_Dup] = Field(default_factory=list)
    cross_cutting: list[CrossCuttingRule] = Field(default_factory=list)
    summary: str = ""


def module_key(source_unit: str | None, depth: int) -> str:
    """Package/module prefix of a unit FQN. Java: com.acme.bank.service.X -> com.acme.bank (depth 3).
    Node/Python module ids use '/' or '.'; the class name is always the last segment."""
    if not source_unit:
        return "(root)"
    owner = source_unit.rpartition(".")[0]  # drop class/function name
    if "/" in owner:  # module-style id (Node): "src/orders/order.service" -> directory "src/orders"
        parts = owner.rpartition("/")[0].split("/")
    else:
        parts = owner.split(".")
    parts = [p for p in parts if p]
    return ".".join(parts[:depth]) if depth > 0 else ".".join(parts)


class RuleConsolidator:
    def __init__(
        self,
        provider: LLMProvider,
        project_name: str,
        cache_dir: Path | None = None,
        output_language: str = "English",
        progress: Callable[[str], None] | None = None,
    ):
        self.provider = provider
        self.project_name = project_name
        self.output_language = output_language
        self.progress = progress or (lambda _m: None)
        self.template = PROMPT_PATH.read_text(encoding="utf-8")
        # Reuse the extractor's cached, thread-safe generate.
        self._gen = BusinessRuleExtractor(provider, cache_dir=cache_dir, output_language=output_language)

    def group(self, rules: list[BusinessRule], by: str = "both", depth: int = 3, min_size: int = 2) -> list[tuple[str, str, list[BusinessRule]]]:
        active = [r for r in rules if r.status != RuleStatus.REJECTED]
        groups: dict[tuple[str, str], list[BusinessRule]] = defaultdict(list)
        if by in ("module", "both"):
            for r in active:
                groups[("module", module_key(r.source_unit, depth))].append(r)
        if by in ("term", "both"):
            for r in active:
                for t in r.domain_terms:
                    groups[("term", t)].append(r)
        return [(k, key, rs) for (k, key), rs in sorted(groups.items()) if len(rs) >= min_size]

    def consolidate(self, rules: list[BusinessRule], by: str = "both", depth: int = 3, workers: int = 1) -> list[RuleGroup]:
        by_id = {r.id: r for r in rules}
        jobs = self.group(rules, by, depth)

        def run(job: tuple[str, str, list[BusinessRule]]) -> RuleGroup:
            kind, key, rs = job
            self.progress(f"{kind} {key} ({len(rs)})")
            block = "\n".join(
                f"{r.id} | {r.category} | {r.statement} | {r.evidence[0].file}:{r.evidence[0].line_start}"
                if r.evidence
                else f"{r.id} | {r.category} | {r.statement} | -"
                for r in rs
            )
            prompt = self.template.format(
                project_name=self.project_name, scope_kind=kind, scope_key=key, count=len(rs), rules_block=block, output_language=self.output_language
            )
            group = RuleGroup(kind=kind, key=key, rule_ids=[r.id for r in rs], model=self.provider.model)
            try:
                parsed = self._gen._generate_json(prompt, _LLMConsolidation)
            except Exception as exc:
                group.summary = f"[error] {type(exc).__name__}: {exc}"
                return group
            ids = {r.id for r in rs}
            group.summary = parsed.summary.strip()
            group.cross_cutting = [c for c in parsed.cross_cutting if len([i for i in c.covers if i in ids]) >= 2]
            for c in group.cross_cutting:
                c.covers = [i for i in c.covers if i in ids]
                c.category = c.category.strip().lower().replace(" ", "_") or "general"
            for d in parsed.duplicates:
                others = [o for o in d.others if o in ids and o != d.canonical]
                if d.canonical in ids and others:
                    group.duplicates.append([d.canonical, *others])
            return group

        if workers > 1 and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                groups = list(pool.map(run, jobs))
        else:
            groups = [run(j) for j in jobs]

        # Tag duplicates on the rule objects (module-scoped duplicates win over term-scoped ones).
        for g in sorted(groups, key=lambda g: g.kind != "module"):
            for canonical, *others in g.duplicates:
                for o in others:
                    rule = by_id.get(o)
                    if rule and not any(t.startswith(DUPLICATE_TAG) for t in rule.tags):
                        rule.tags.append(f"{DUPLICATE_TAG}{canonical}")
        return groups


def duplicate_of(rule: BusinessRule) -> str | None:
    return next((t[len(DUPLICATE_TAG) :] for t in rule.tags if t.startswith(DUPLICATE_TAG)), None)

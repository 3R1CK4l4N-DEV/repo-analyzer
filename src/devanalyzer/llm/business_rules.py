from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from devanalyzer.config import Settings, get_settings
from devanalyzer.core.models import (
    BusinessRule,
    CodeUnit,
    CodeUnitKind,
    Evidence,
    GlossaryTerm,
    ProjectAnalysis,
    RuleExtractionResult,
    RuleStatus,
)
from devanalyzer.llm.provider import LLMProvider

PROMPT_PATH = Path(__file__).parent / "prompts" / "business_rules.md"
VERIFY_PROMPT_PATH = Path(__file__).parent / "prompts" / "verify_rule.md"
TYPE_KINDS = {CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD, CodeUnitKind.MODULE}
DEFAULT_STEREOTYPES = {"service", "controller", "entity", "component", "advice", None}
SYSTEM_PROMPT = "You extract business rules from code and answer strictly with valid JSON."


class _LLMRule(BaseModel):
    statement: str
    rationale: str | None = None
    category: str = "general"
    domain_terms: list[str] = Field(default_factory=list)
    line_start: int | None = None
    line_end: int | None = None
    confidence: float = 0.5


class _LLMTerm(BaseModel):
    term: str
    definition: str


class _LLMResponse(BaseModel):
    rules: list[_LLMRule] = Field(default_factory=list)
    glossary: list[_LLMTerm] = Field(default_factory=list)


class _LLMVerdict(BaseModel):
    verdict: str = "supported"
    confidence: float = 0.5
    corrected_statement: str | None = None
    reason: str | None = None

    def model_post_init(self, __context) -> None:
        self.verdict = self.verdict.strip().lower()
        if self.verdict not in ("supported", "partial", "unsupported", "technical"):
            self.verdict = "partial"
        self.confidence = max(0.0, min(1.0, self.confidence))


def rule_id(statement: str, file: str) -> str:
    normalized = re.sub(r"\W+", " ", statement.lower()).strip()
    return "BR-" + hashlib.sha1(f"{normalized}|{file}".encode()).hexdigest()[:10]


def parse_llm_json(text: str) -> dict:
    """Tolerant JSON extraction: strips fences and grabs the outermost object."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1:
            raise
        return json.loads(cleaned[start : end + 1])


class BusinessRuleExtractor:
    def __init__(
        self,
        provider: LLMProvider,
        settings: Settings | None = None,
        cache_dir: Path | None = None,
        output_language: str = "English",
        progress: Callable[[str], None] | None = None,
    ):
        self.provider = provider
        self.settings = settings or get_settings()
        self.cache_dir = cache_dir
        self.output_language = output_language
        self.progress = progress or (lambda _msg: None)
        self.template = PROMPT_PATH.read_text(encoding="utf-8")
        self._line_cache: dict[Path, list[str]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ selection
    @staticmethod
    def candidate_units(analysis: ProjectAnalysis, stereotypes: set[str | None] | None = None, min_loc: int = 3) -> list[CodeUnit]:
        wanted = stereotypes if stereotypes is not None else DEFAULT_STEREOTYPES

        def walk(units: Iterable[CodeUnit]):
            for u in units:
                if u.kind in TYPE_KINDS:
                    has_logic = any(c.kind in (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION) for c in u.children) or u.kind == CodeUnitKind.MODULE
                    if u.stereotype in wanted and has_logic and u.loc >= min_loc:
                        yield u
                    yield from walk(c for c in u.children if c.kind in TYPE_KINDS)

        return list(walk(analysis.units))

    # ------------------------------------------------------------ extraction
    def extract(
        self,
        analysis: ProjectAnalysis,
        root: str | Path,
        stereotypes: set[str | None] | None = None,
        limit: int | None = None,
        workers: int = 1,
    ) -> RuleExtractionResult:
        root = Path(root)
        result = RuleExtractionResult(project_name=analysis.project_name, model=self.provider.model)
        units = self.candidate_units(analysis, stereotypes)
        if limit:
            units = units[:limit]
        glossary: dict[str, GlossaryTerm] = {}
        seen_rules: set[str] = set()

        # Phase 1: build prompts sequentially (file reads are cached, cheap).
        jobs: list[tuple[CodeUnit, str]] = []
        for unit in units:
            code = self._slice(root / unit.file, unit.line_start, unit.line_end)
            if not code:
                result.units_skipped += 1
                continue
            jobs.append((unit, self._prompt(analysis, unit, code)))

        # Phase 2: call the LLM, in parallel when the provider allows it; results keep job order.
        def run(job: tuple[CodeUnit, str]) -> tuple[CodeUnit, _LLMResponse | None, str | None]:
            unit, prompt = job
            self.progress(unit.fqn)
            try:
                raw = self._cached_generate(prompt)
                return unit, _LLMResponse.model_validate(parse_llm_json(raw)), None
            except Exception as exc:  # malformed JSON or provider/network failures must not abort the run
                return unit, None, f"{unit.fqn}: {type(exc).__name__}: {exc}"

        if workers > 1 and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outcomes = list(pool.map(run, jobs))
        else:
            outcomes = [run(j) for j in jobs]

        # Phase 3: merge deterministically.
        for unit, parsed, error in outcomes:
            if parsed is None:
                result.errors.append(error or unit.fqn)
                result.units_skipped += 1
                continue
            result.units_analyzed += 1
            for r in parsed.rules:
                rule = self._to_rule(r, unit, root)
                if rule.id in seen_rules:
                    continue
                seen_rules.add(rule.id)
                result.rules.append(rule)
                for term in rule.domain_terms:
                    glossary.setdefault(term, GlossaryTerm(term=term, definition="")).rules.append(rule.id)
            for t in parsed.glossary:
                entry = glossary.setdefault(t.term, GlossaryTerm(term=t.term, definition=t.definition))
                if not entry.definition:
                    entry.definition = t.definition
                if unit.fqn not in entry.related_units:
                    entry.related_units.append(unit.fqn)

        result.glossary = sorted(glossary.values(), key=lambda g: g.term.lower())
        return result

    # ------------------------------------------------------------ verification
    def verify(
        self, result: RuleExtractionResult, root: str | Path | None = None, workers: int = 1, context_lines: int = 6, judge: LLMProvider | None = None
    ) -> RuleExtractionResult:
        """Second LLM pass: judge each rule strictly against its evidence (plus surrounding context).

        Adjusts confidence, tags the verdict, rewrites 'partial' statements and moves
        'unsupported'/'technical' rules to REJECTED so humans can focus on the rest.
        """
        template = VERIFY_PROMPT_PATH.read_text(encoding="utf-8")
        root = Path(root) if root else None
        judge = judge or self.provider

        def run(rule: BusinessRule) -> tuple[BusinessRule, _LLMVerdict | None, str | None]:
            ev = rule.evidence[0] if rule.evidence else None
            if ev is None or not ev.snippet:
                return rule, None, None
            self.progress(f"verify {rule.id}")
            # The extractor's line numbers are often off by a few lines; widen the window so the
            # verifier sees annotations/conditions right next to the cited range.
            start, end, snippet = ev.line_start, ev.line_end, ev.snippet
            if root is not None:
                start, end = max(1, ev.line_start - context_lines), ev.line_end + context_lines
                snippet = self._slice(root / ev.file, start, end) or ev.snippet
            prompt = template.format(
                statement=rule.statement,
                rationale=rule.rationale or "-",
                file=ev.file,
                line_start=start,
                line_end=end,
                language=self._language_of(rule),
                snippet=snippet,
                output_language=self.output_language,
            )
            try:
                return rule, _LLMVerdict.model_validate(parse_llm_json(self._cached_generate(prompt, judge))), None
            except Exception as exc:
                return rule, None, f"verify {rule.id}: {type(exc).__name__}: {exc}"

        if workers > 1 and len(result.rules) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outcomes = list(pool.map(run, result.rules))
        else:
            outcomes = [run(r) for r in result.rules]

        for rule, verdict, error in outcomes:
            if error:
                result.errors.append(error)
            if verdict is None:
                continue
            rule.tags = [t for t in rule.tags if not t.startswith("verdict/")] + [f"verdict/{verdict.verdict}"]
            rule.confidence = round(min(rule.confidence, verdict.confidence) if verdict.verdict != "supported" else max(rule.confidence, verdict.confidence), 2)
            if verdict.verdict == "partial" and verdict.corrected_statement and verdict.corrected_statement.strip() != rule.statement:
                rule.rationale = f"{rule.rationale or ''}\n[verifier] original: {rule.statement} — {verdict.reason}".strip()
                rule.statement = verdict.corrected_statement.strip()
            elif verdict.verdict in ("unsupported", "technical"):
                rule.status = RuleStatus.REJECTED
                rule.rationale = f"{rule.rationale or ''}\n[verifier] {verdict.verdict}: {verdict.reason}".strip()
        return result

    def _language_of(self, rule: BusinessRule) -> str:
        ev_file = rule.evidence[0].file if rule.evidence else ""
        return {"java": "java", "py": "python", "ts": "typescript", "tsx": "typescript", "js": "javascript"}.get(ev_file.rsplit(".", 1)[-1], "")

    def _to_rule(self, r: _LLMRule, unit: CodeUnit, root: Path) -> BusinessRule:
        start = r.line_start if r.line_start and unit.line_start <= r.line_start <= unit.line_end else unit.line_start
        end = r.line_end if r.line_end and start <= r.line_end <= unit.line_end else min(start + 10, unit.line_end)
        evidence = Evidence(
            file=unit.file,
            line_start=start,
            line_end=end,
            unit=unit.fqn,
            snippet=self._slice(root / unit.file, start, end, numbered=False)[:600] or None,
        )
        return BusinessRule(
            id=rule_id(r.statement, unit.file),
            statement=r.statement.strip(),
            rationale=r.rationale,
            category=r.category.strip().lower().replace(" ", "_") or "general",
            domain_terms=[t.strip() for t in r.domain_terms if t.strip()],
            evidence=[evidence],
            confidence=max(0.0, min(1.0, r.confidence)),
            tags=[unit.stereotype] if unit.stereotype else [],
            source_unit=unit.fqn,
            model=self.provider.model,
        )

    # ------------------------------------------------------------ helpers
    def _prompt(self, analysis: ProjectAnalysis, unit: CodeUnit, code: str) -> str:
        return self.template.format(
            project_name=analysis.project_name,
            language=unit.language.value if unit.language else "code",
            unit_fqn=unit.fqn,
            unit_kind=unit.kind.value,
            stereotype=f", {unit.stereotype}" if unit.stereotype else "",
            file=unit.file,
            line_start=unit.line_start,
            line_end=unit.line_end,
            code=code,
            output_language=self.output_language,
        )

    def _lines(self, path: Path) -> list[str]:
        with self._lock:
            if path not in self._line_cache:
                self._line_cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
            return self._line_cache[path]

    def _slice(self, path: Path, start: int, end: int, numbered: bool = True) -> str:
        lines = self._lines(path)
        if not lines:
            return ""
        chunk = lines[max(0, start - 1) : end]
        if numbered:
            return "\n".join(f"{start + i:>5}| {ln}" for i, ln in enumerate(chunk))
        return "\n".join(chunk)

    def _cached_generate(self, prompt: str, provider: LLMProvider | None = None) -> str:
        provider = provider or self.provider
        if self.cache_dir is None:
            return provider.generate(prompt, system=SYSTEM_PROMPT)
        key = hashlib.sha256(f"{provider.name}|{provider.model}|{prompt}".encode()).hexdigest()
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.is_file():
            return json.loads(cache_file.read_text(encoding="utf-8"))["response"]
        response = provider.generate(prompt, system=SYSTEM_PROMPT)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Atomic write so concurrent workers never observe a half-written cache entry.
        tmp = cache_file.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"model": provider.model, "response": response}), encoding="utf-8")
        os.replace(tmp, cache_file)
        return response

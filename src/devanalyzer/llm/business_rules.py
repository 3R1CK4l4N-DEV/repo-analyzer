from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from devanalyzer.config import Settings, get_settings
from devanalyzer.core.models import (
    BusinessRule,
    CodeUnit,
    CodeUnitKind,
    Evidence,
    GlossaryTerm,
    ProjectAnalysis,
    RelationKind,
    RuleExtractionResult,
    RuleStatus,
)
from devanalyzer.llm.provider import LLMProvider

PROMPT_PATH = Path(__file__).parent / "prompts" / "business_rules.md"
VERIFY_PROMPT_PATH = Path(__file__).parent / "prompts" / "verify_rule.md"
TYPE_KINDS = {CodeUnitKind.CLASS, CodeUnitKind.INTERFACE, CodeUnitKind.ENUM, CodeUnitKind.RECORD, CodeUnitKind.MODULE}
DEFAULT_STEREOTYPES = {"service", "controller", "entity", "component", "advice", None}
SYSTEM_PROMPT = "You extract business rules from code and answer strictly with valid JSON."

STEREOTYPE_FOCUS: dict[str, str] = {
    "entity": "invariants of the domain object: mandatory fields, formats/patterns, ranges, uniqueness, valid state values and transitions, derived values. Validation annotations (@NotBlank, @Size, @Pattern, @Min/@Max, nullable=false) ARE business rules; ignore ids/timestamps/ORM mapping details.",
    "service": "policies, calculations, thresholds/limits, preconditions that throw or reject, state transitions, authorization checks, and the order in which steps must happen.",
    "controller": "input constraints beyond the DTO, authorization/ownership checks, which operations exist for which actor, redirects/decisions that depend on business state. Ignore HTTP plumbing, serialization and error pages.",
    "repository": "query constraints that encode business meaning (e.g. only active records, soft deletes, date windows). Plain CRUD/finders are NOT rules.",
    "component": "cross-cutting policies (scheduling windows, retries with business meaning, feature toggles that change behaviour for customers).",
    "advice": "how business errors are classified and surfaced; ignore technical exception mapping.",
}


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


_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "be",
    "is",
    "must",
    "el",
    "la",
    "los",
    "las",
    "de",
    "del",
    "un",
    "una",
    "debe",
    "ser",
    "que",
    "y",
    "o",
    "en",
    "para",
    "por",
    "se",
    "puede",
    "pueden",
}
_NEGATIONS = {"not", "no", "never", "cannot", "without", "nunca", "sin", "jamas", "tampoco"}
_CONSTRAINT_WORDS = set(
    "must may can should required optional only unless except before after least most less greater below above minimum maximum "
    "debe puede pueden obligatorio opcional solo salvo excepto antes despues menor mayor minimo maximo".split()
)
_SYMBOLS = re.compile(r"<=|>=|!=|==|[<>=≤≥%$€£]|(?<!\w)[+\-−](?=\d)")
# React hooks and component helpers describe UI state (toasts, body lock, mounted flags), never business rules.
UI_MODULE_RE = re.compile(r"(?:^|/)(?:hooks|components|ui)/|(?:^|/)use-[\w-]+\.[cm]?[jt]sx?$")


def _normalized_statement(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("’", "'")).encode("ascii", "ignore").decode().lower()
    return text.replace("can't", "cannot").replace("won't", "will not").replace("n't", " not")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _normalized_statement(text)) if t not in _STOPWORDS and (len(t) > 1 or t.isdigit())}


def _constraint_signature(text: str) -> tuple:
    """Conservative guards: similar wording alone does not establish equivalent constraints."""
    normalized = _normalized_statement(text)
    words = re.findall(r"[a-z]+", normalized)
    return (
        tuple(re.findall(r"(?<!\w)[$€£]?[+\-−]?\d+(?:[.,]\d+)*[%$€£]?", text)),
        tuple((word, tuple(words[i + 1 : i + 3])) for i, word in enumerate(words) if word in _NEGATIONS),
        tuple(word for word in words if word in _CONSTRAINT_WORDS),
        tuple(_SYMBOLS.findall(text)),
    )


def _term_key(term: str) -> str:
    return " ".join(term.split()).casefold()


def _register_term(glossary: dict[str, GlossaryTerm], term: str, definition: str = "") -> GlossaryTerm:
    """ "Entidad" and "entidad" are one term (and one note on case-insensitive file systems); a capitalised
    spelling wins over an all-lowercase one and the first non-empty definition is kept."""
    key = _term_key(term)
    entry = glossary.get(key)
    if entry is None:
        entry = glossary[key] = GlossaryTerm(term=term, definition=definition)
    else:
        if entry.term.islower() and not term.islower():
            entry.term = term
        if definition and not entry.definition:
            entry.definition = definition
    return entry


def _same_evidence(a: BusinessRule, b: BusinessRule, tolerance: int = 2) -> bool:
    for ea in a.evidence:
        for eb in b.evidence:
            if ea.file == eb.file and ea.line_start <= eb.line_end + tolerance and eb.line_start <= ea.line_end + tolerance:
                return True
    return False


def merge_near_duplicates(rules: list[BusinessRule], min_jaccard: float = 0.6) -> list[BusinessRule]:
    """Merge similarly worded rules in the same category with compatible constraint signatures.
    This is a conservative heuristic, not semantic verification. Keep all distinct evidence spans."""
    merged: list[BusinessRule] = []
    for rule in rules:
        toks = _tokens(rule.statement)
        signature = _constraint_signature(rule.statement)
        target = None
        for existing in merged:
            if existing.category != rule.category or _constraint_signature(existing.statement) != signature:
                continue
            etoks = _tokens(existing.statement)
            jaccard = len(toks & etoks) / len(toks | etoks) if toks | etoks else 0.0
            same_span = _same_evidence(rule, existing)
            same_unit = rule.source_unit is not None and rule.source_unit == existing.source_unit
            if jaccard >= min_jaccard and (same_span or same_unit or jaccard >= 0.8):
                target = existing
                break
        if target is None:
            merged.append(rule)
            continue
        _merge_rule_details(target, rule)
    return merged


def _merge_rule_details(target: BusinessRule, rule: BusinessRule) -> None:
    for ev in rule.evidence:
        if ev not in target.evidence:
            target.evidence.append(ev)
    target.confidence = max(target.confidence, rule.confidence)
    for term in rule.domain_terms:
        if term not in target.domain_terms:
            target.domain_terms.append(term)
    for tag in [*rule.tags, "merged"]:
        if tag not in target.tags:
            target.tags.append(tag)


def rule_id(statement: str, file: str) -> str:
    # Preserve existing IDs for ordinary prose while distinguishing >/<, signs, percentages and currencies.
    marked = _SYMBOLS.sub(lambda match: f" symbol_{match.group().encode('utf-8').hex()} ", statement)
    normalized = re.sub(r"\W+", " ", marked.lower()).strip()
    return "BR-" + hashlib.sha1(f"{normalized}|{file}".encode()).hexdigest()[:10]


def legacy_rule_id(statement: str, file: str) -> str:
    """ID scheme before symbols were significant; knowledge bases created with it are migrated on ingest."""
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
        retriever: Callable[[str, int], list] | None = None,
    ):
        self.provider = provider
        self.settings = settings or get_settings()
        self.cache_dir = cache_dir
        self.output_language = output_language
        self.progress = progress or (lambda _msg: None)
        # Optional semantic retriever (query, limit) -> hits with .doc_id/.kind/.title/.snippet (RAG context, R4+G1)
        self.retriever = retriever
        self.template = PROMPT_PATH.read_text(encoding="utf-8")
        self._line_cache: dict[Path, list[str]] = {}
        self._index_cache: tuple[ProjectAnalysis, dict[str, CodeUnit]] | None = None
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

        return list(walk(analysis.units)) + BusinessRuleExtractor._module_candidates(analysis, wanted, min_loc)

    @staticmethod
    def _module_candidates(analysis: ProjectAnalysis, wanted: set[str | None], min_loc: int) -> list[CodeUnit]:
        """Function-based code (Next.js route handlers, FastAPI modules, scripts) has no class to send to the model:
        one synthetic module unit per file spanning its functions. UI component files (.tsx/.jsx) only qualify
        when they host a route handler."""
        by_file: dict[str, list[CodeUnit]] = {}
        for u in analysis.units:
            if u.kind == CodeUnitKind.FUNCTION:
                by_file.setdefault(u.file, []).append(u)
        covered = {u.file for u in analysis.units if u.kind == CodeUnitKind.MODULE}
        out: list[CodeUnit] = []
        for file, functions in by_file.items():
            stereotype = next((f.stereotype for f in functions if f.stereotype), None)
            if file in covered or stereotype not in wanted or sum(f.loc for f in functions) < min_loc:
                continue
            if stereotype != "controller" and (file.endswith((".tsx", ".jsx")) or UI_MODULE_RE.search(file)):
                continue
            module = functions[0].fqn.rpartition(".")[0] or file
            out.append(
                CodeUnit(
                    kind=CodeUnitKind.MODULE,
                    name=module.rsplit("/", 1)[-1].rsplit(".", 1)[-1],
                    fqn=module,
                    file=file,
                    line_start=min(f.line_start for f in functions),
                    line_end=max(f.line_end for f in functions),
                    language=functions[0].language,
                    package=functions[0].package,
                    stereotype=stereotype,
                    children=functions,
                )
            )
        return out

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
        seen_rules: dict[str, BusinessRule] = {}

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
                return unit, self._generate_json(prompt, _LLMResponse), None
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
                if existing := seen_rules.get(rule.id):
                    _merge_rule_details(existing, rule)
                else:
                    seen_rules[rule.id] = rule
                    result.rules.append(rule)
                for term in rule.domain_terms:
                    _register_term(glossary, term)
            for t in parsed.glossary:
                entry = _register_term(glossary, t.term, t.definition)
                if unit.fqn not in entry.related_units:
                    entry.related_units.append(unit.fqn)

        result.rules = merge_near_duplicates(result.rules)
        # Removed duplicate IDs must not leave dangling glossary links.
        for term in glossary.values():
            term.rules = []
        for rule in result.rules:
            rule.domain_terms = list(dict.fromkeys(glossary[_term_key(t)].term for t in rule.domain_terms))
            for term in rule.domain_terms:
                glossary[_term_key(term)].rules.append(rule.id)
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
                return rule, self._generate_json(prompt, _LLMVerdict, judge), None
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
            focus=self._focus(unit),
            context=self._context(analysis, unit),
        )

    @staticmethod
    def _focus(unit: CodeUnit) -> str:
        """Stereotype-specific guidance (R3): what kind of rules usually live in this unit."""
        text = STEREOTYPE_FOCUS.get(unit.stereotype or "", "")
        return f"\nFocus for this {unit.stereotype or 'unit'}: {text}\n" if text else ""

    def _context(self, analysis: ProjectAnalysis, unit: CodeUnit, max_deps: int = 6, max_members: int = 8) -> str:
        """Signatures of project-internal dependencies plus the mapped DB table (R4), so calls into other
        classes and persisted fields are understandable without guessing."""
        index = self._unit_index(analysis)
        dep_fqns: list[str] = []
        # A synthetic module unit speaks for its functions.
        sources = {unit.fqn} | {c.fqn for c in unit.children if c.kind == CodeUnitKind.FUNCTION}
        # Classes depend on types; functions call and import other functions.
        kinds = {RelationKind.DEPENDS_ON, RelationKind.EXTENDS, RelationKind.IMPLEMENTS} | (
            {RelationKind.CALLS, RelationKind.IMPORTS} if unit.kind == CodeUnitKind.MODULE else set()
        )
        for rel in analysis.relations:
            if rel.source in sources and rel.kind in kinds and rel.target in index and rel.target not in dep_fqns and rel.target not in sources:
                dep_fqns.append(rel.target)
        blocks: list[str] = []
        for fqn in dep_fqns[:max_deps]:
            dep = index[fqn]
            members = [c for c in dep.children if c.kind in (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION)][:max_members]
            fields = [c for c in dep.children if c.kind == CodeUnitKind.FIELD][:max_members]
            sig = [f"{m.name}({', '.join(p.type or p.name for p in m.parameters)}){' -> ' + m.type if m.type else ''}" for m in members]
            line = f"- {dep.kind.value} {dep.name}" + (f" <{dep.stereotype}>" if dep.stereotype else "")
            if dep.kind == CodeUnitKind.FUNCTION:
                line += f"({', '.join(p.type or p.name for p in dep.parameters)})" + (f" -> {dep.type}" if dep.type else "")
            if dep.docstring:
                line += f": {dep.docstring.splitlines()[0][:100]}"
            if sig:
                line += "\n    methods: " + "; ".join(sig)
            if fields and dep.stereotype == "entity":
                line += "\n    fields: " + ", ".join(f"{f.type or '?'} {f.name}" for f in fields)
            blocks.append(line)
        tables = [t for t in analysis.db_schema.tables if unit.fqn in t.mapped_entities]
        for t in tables:
            cols = ", ".join(f"{c.name}{' PK' if c.primary_key else ''}{'' if c.nullable else ' NOT NULL'}" for c in t.columns[:16])
            fks = "; ".join(f"{fk.column} -> {fk.ref_table}" for fk in t.foreign_keys)
            blocks.append(f"- persisted in table `{t.name}`: {cols}" + (f"\n    foreign keys: {fks}" if fks else ""))
        if self.retriever is not None:
            query = " ".join(
                filter(
                    None,
                    [unit.name, unit.docstring or "", " ".join(c.name for c in unit.children if c.kind in (CodeUnitKind.METHOD, CodeUnitKind.FUNCTION))[:300]],
                )
            )
            try:
                hits = self.retriever(query, 8)
            except Exception:
                hits = []
            related = [
                h
                for h in hits
                if h.doc_id != unit.fqn
                and not h.doc_id.startswith(unit.fqn + ".")
                and h.kind in ("business_rule", "glossary_term", "class", "table", "endpoint")
            ][:5]
            if related:
                blocks.append("- semantically related knowledge (already known; avoid restating identical rules, use it for consistent domain terms):")
                blocks += [f"    · [{h.kind}] {h.title}: {h.snippet[:140]}" for h in related]
        if not blocks:
            return ""
        return (
            "\nProject context (signatures only; do not infer rules from the context alone, use it to understand the code above):\n" + "\n".join(blocks) + "\n"
        )

    def _unit_index(self, analysis: ProjectAnalysis) -> dict[str, CodeUnit]:
        with self._lock:
            if self._index_cache is None or self._index_cache[0] is not analysis:
                index: dict[str, CodeUnit] = {}
                stack = list(analysis.units)
                while stack:
                    u = stack.pop()
                    if u.kind in TYPE_KINDS or u.kind == CodeUnitKind.FUNCTION:
                        index[u.fqn] = u
                        stack.extend(c for c in u.children if c.kind in TYPE_KINDS)
                self._index_cache = (analysis, index)
            return self._index_cache[1]

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

    def _generate_json(self, prompt: str, model: type[BaseModel], provider: LLMProvider | None = None, retries: int = 1):
        """Generate + parse; on malformed JSON ask the model once to repair its own output (local 7b models
        occasionally break JSON on long prompts, which would otherwise lose every rule of that unit)."""
        raw = self._cached_generate(prompt, provider)
        try:
            return model.model_validate(parse_llm_json(raw))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            if retries <= 0:
                raise
            repair = (
                "The following text was supposed to be a single valid JSON object but failed to parse "
                f"({type(exc).__name__}: {str(exc)[:120]}). Return ONLY the corrected JSON object, nothing else.\n\n{raw[:6000]}"
            )
            fixed = self._cached_generate(repair, provider)
            return model.model_validate(parse_llm_json(fixed))

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

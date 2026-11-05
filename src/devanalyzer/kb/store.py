from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from devanalyzer.core.models import BusinessRule, GlossaryTerm, RuleExtractionResult, RuleStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    units_analyzed INTEGER DEFAULT 0,
    rules_new INTEGER DEFAULT 0,
    rules_seen INTEGER DEFAULT 0,
    rules_changed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    statement TEXT NOT NULL,
    rationale TEXT,
    category TEXT,
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'inferred',
    source_unit TEXT,
    model TEXT,
    tags TEXT,
    domain_terms TEXT,
    evidence TEXT,
    first_seen_run INTEGER,
    last_seen_run INTEGER,
    review_note TEXT,
    content_hash TEXT
);
CREATE TABLE IF NOT EXISTS rule_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    change TEXT NOT NULL,
    previous TEXT
);
CREATE TABLE IF NOT EXISTS terms (
    project TEXT NOT NULL,
    term TEXT NOT NULL,
    definition TEXT,
    aliases TEXT,
    related_units TEXT,
    rules TEXT,
    PRIMARY KEY (project, term)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_project ON rules(project);
CREATE INDEX IF NOT EXISTS idx_rules_unit ON rules(source_unit);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KnowledgeBase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> KnowledgeBase:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ ingest
    def ingest(self, result: RuleExtractionResult) -> dict[str, int]:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO analysis_runs (project, model, created_at, units_analyzed) VALUES (?, ?, ?, ?)",
            (result.project_name, result.model, _now(), result.units_analyzed),
        )
        run_id = cur.lastrowid
        counts = {"new": 0, "seen": 0, "changed": 0}
        for rule in result.rules:
            change = self._upsert_rule(cur, rule, result.project_name, run_id)
            counts[change] += 1
        for term in result.glossary:
            self._upsert_term(cur, term, result.project_name)
        cur.execute(
            "UPDATE analysis_runs SET rules_new=?, rules_seen=?, rules_changed=? WHERE id=?",
            (counts["new"], counts["seen"], counts["changed"], run_id),
        )
        self.conn.commit()
        return {"run_id": run_id, **counts}

    def _upsert_rule(self, cur: sqlite3.Cursor, rule: BusinessRule, project: str, run_id: int) -> str:
        content_hash = self._hash(rule)
        existing = cur.execute("SELECT * FROM rules WHERE id=?", (rule.id,)).fetchone()
        tags = json.dumps(rule.tags)
        terms = json.dumps(rule.domain_terms)
        evidence = json.dumps([e.model_dump() for e in rule.evidence])
        if existing is None:
            cur.execute(
                """INSERT INTO rules (id, project, statement, rationale, category, confidence, status, source_unit, model,
                   tags, domain_terms, evidence, first_seen_run, last_seen_run, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule.id, project, rule.statement, rule.rationale, rule.category, rule.confidence, rule.status.value,
                    rule.source_unit, rule.model, tags, terms, evidence, run_id, run_id, content_hash,
                ),
            )
            cur.execute("INSERT INTO rule_history (rule_id, run_id, change) VALUES (?, ?, 'new')", (rule.id, run_id))
            return "new"
        change = "seen" if existing["content_hash"] == content_hash else "changed"
        # Human review decisions (status/review_note) are preserved across runs.
        cur.execute(
            """UPDATE rules SET statement=?, rationale=?, category=?, confidence=?, source_unit=?, model=?,
               tags=?, domain_terms=?, evidence=?, last_seen_run=?, content_hash=? WHERE id=?""",
            (
                rule.statement, rule.rationale, rule.category, rule.confidence, rule.source_unit, rule.model,
                tags, terms, evidence, run_id, content_hash, rule.id,
            ),
        )
        if change == "changed":
            cur.execute(
                "INSERT INTO rule_history (rule_id, run_id, change, previous) VALUES (?, ?, 'changed', ?)",
                (rule.id, run_id, json.dumps(dict(existing), default=str)),
            )
        return change

    def _upsert_term(self, cur: sqlite3.Cursor, term: GlossaryTerm, project: str) -> None:
        existing = cur.execute("SELECT * FROM terms WHERE project=? AND term=?", (project, term.term)).fetchone()
        if existing is None:
            cur.execute(
                "INSERT INTO terms (project, term, definition, aliases, related_units, rules) VALUES (?, ?, ?, ?, ?, ?)",
                (project, term.term, term.definition, json.dumps(term.aliases), json.dumps(term.related_units), json.dumps(term.rules)),
            )
            return
        merged_units = sorted(set(json.loads(existing["related_units"] or "[]")) | set(term.related_units))
        merged_rules = sorted(set(json.loads(existing["rules"] or "[]")) | set(term.rules))
        definition = existing["definition"] or term.definition
        cur.execute(
            "UPDATE terms SET definition=?, related_units=?, rules=? WHERE project=? AND term=?",
            (definition, json.dumps(merged_units), json.dumps(merged_rules), project, term.term),
        )

    @staticmethod
    def _hash(rule: BusinessRule) -> str:
        import hashlib

        body = json.dumps(
            {"s": rule.statement, "c": rule.category, "e": [(e.file, e.line_start, e.line_end) for e in rule.evidence]},
            sort_keys=True,
        )
        return hashlib.sha1(body.encode()).hexdigest()

    # ------------------------------------------------------------ queries
    def rules(
        self,
        project: str | None = None,
        status: RuleStatus | str | None = None,
        category: str | None = None,
        unit: str | None = None,
        search: str | None = None,
    ) -> list[BusinessRule]:
        clauses, params = [], []
        if project:
            clauses.append("project=?"), params.append(project)
        if status:
            clauses.append("status=?"), params.append(status.value if isinstance(status, RuleStatus) else status)
        if category:
            clauses.append("category=?"), params.append(category)
        if unit:
            clauses.append("source_unit LIKE ?"), params.append(f"%{unit}%")
        if search:
            clauses.append("(statement LIKE ? OR rationale LIKE ? OR domain_terms LIKE ?)")
            params += [f"%{search}%"] * 3
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM rules {where} ORDER BY source_unit, id", params).fetchall()
        return [self._row_to_rule(r) for r in rows]

    def rule(self, rule_id: str) -> BusinessRule | None:
        row = self.conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        return self._row_to_rule(row) if row else None

    def set_status(self, rule_id: str, status: RuleStatus, note: str | None = None) -> bool:
        cur = self.conn.execute("UPDATE rules SET status=?, review_note=COALESCE(?, review_note) WHERE id=?", (status.value, note, rule_id))
        self.conn.commit()
        return cur.rowcount > 0

    def terms(self, project: str | None = None, search: str | None = None) -> list[GlossaryTerm]:
        clauses, params = [], []
        if project:
            clauses.append("project=?"), params.append(project)
        if search:
            clauses.append("(term LIKE ? OR definition LIKE ?)"), params.extend([f"%{search}%"] * 2)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM terms {where} ORDER BY term COLLATE NOCASE", params).fetchall()
        return [
            GlossaryTerm(
                term=r["term"], definition=r["definition"] or "", aliases=json.loads(r["aliases"] or "[]"),
                related_units=json.loads(r["related_units"] or "[]"), rules=json.loads(r["rules"] or "[]"),
            )
            for r in rows
        ]

    def runs(self, project: str | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE project=?", (project,)) if project else ("", ())
        return [dict(r) for r in self.conn.execute(f"SELECT * FROM analysis_runs {where} ORDER BY id DESC", params)]

    def history(self, rule_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM rule_history WHERE rule_id=? ORDER BY id", (rule_id,))]

    def add_decision(self, project: str, title: str, body: str) -> int:
        cur = self.conn.execute("INSERT INTO decisions (project, title, body, created_at) VALUES (?, ?, ?, ?)", (project, title, body, _now()))
        self.conn.commit()
        return cur.lastrowid

    def decisions(self, project: str | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE project=?", (project,)) if project else ("", ())
        return [dict(r) for r in self.conn.execute(f"SELECT * FROM decisions {where} ORDER BY id", params)]

    def summary(self, project: str | None = None) -> dict[str, Any]:
        where, params = ("WHERE project=?", (project,)) if project else ("", ())
        by_status = dict(self.conn.execute(f"SELECT status, COUNT(*) FROM rules {where} GROUP BY status", params).fetchall())
        by_category = dict(self.conn.execute(f"SELECT category, COUNT(*) FROM rules {where} GROUP BY category", params).fetchall())
        terms = self.conn.execute(f"SELECT COUNT(*) FROM terms {where}", params).fetchone()[0]
        runs = self.conn.execute(f"SELECT COUNT(*) FROM analysis_runs {where}", params).fetchone()[0]
        return {"rules": sum(by_status.values()), "by_status": by_status, "by_category": by_category, "terms": terms, "runs": runs}

    @staticmethod
    def _row_to_rule(r: sqlite3.Row) -> BusinessRule:
        from devanalyzer.core.models import Evidence

        return BusinessRule(
            id=r["id"], statement=r["statement"], rationale=r["rationale"], category=r["category"] or "general",
            domain_terms=json.loads(r["domain_terms"] or "[]"), evidence=[Evidence(**e) for e in json.loads(r["evidence"] or "[]")],
            confidence=r["confidence"] or 0.0, status=RuleStatus(r["status"]), tags=json.loads(r["tags"] or "[]"),
            source_unit=r["source_unit"], model=r["model"],
        )

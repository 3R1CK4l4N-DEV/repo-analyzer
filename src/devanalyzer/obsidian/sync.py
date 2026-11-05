"""Read human edits made in Obsidian rule notes back into the knowledge base.

Editable frontmatter fields on `30 Business Rules/*.md`:
  status: inferred | confirmed | rejected
  review_note: free text
  statement: corrected rule statement (rule id stays stable)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from devanalyzer.core.models import RuleStatus
from devanalyzer.kb.store import KnowledgeBase
from devanalyzer.obsidian.vault import FOLDERS

FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---", re.S)


@dataclass
class SyncResult:
    scanned: int = 0
    status_changes: list[tuple[str, str, str]] = field(default_factory=list)  # (rule_id, old, new)
    statement_changes: list[tuple[str, str]] = field(default_factory=list)  # (rule_id, new statement)
    note_changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.status_changes) + len(self.statement_changes) + len(self.note_changes)


def parse_frontmatter(text: str) -> dict[str, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    data: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].replace('\\"', '"')
        data[key.strip()] = value
    return data


def sync_vault_to_kb(vault_base: str | Path, kb: KnowledgeBase, project: str | None = None, dry_run: bool = False) -> SyncResult:
    result = SyncResult()
    folder = Path(vault_base) / FOLDERS["rules"]
    if not folder.is_dir():
        result.errors.append(f"no rules folder at {folder}")
        return result
    for note in sorted(folder.glob("*.md")):
        result.scanned += 1
        fm = parse_frontmatter(note.read_text(encoding="utf-8", errors="replace"))
        rule_id = fm.get("rule_id")
        if not rule_id:
            continue
        rule = kb.rule(rule_id)
        if rule is None:
            result.errors.append(f"{note.name}: rule {rule_id} not in knowledge base")
            continue
        new_status = fm.get("status", "").strip().lower()
        note_text = fm.get("review_note", "").strip() or None
        new_statement = fm.get("statement", "").strip()
        if new_status and new_status != rule.status.value:
            try:
                status = RuleStatus(new_status)
            except ValueError:
                result.errors.append(f"{note.name}: invalid status {new_status!r}")
            else:
                result.status_changes.append((rule_id, rule.status.value, status.value))
                if not dry_run:
                    kb.set_status(rule_id, status, note_text)
        elif note_text and not dry_run:
            stored = kb.conn.execute("SELECT review_note FROM rules WHERE id=?", (rule_id,)).fetchone()[0]
            if stored != note_text:
                kb.conn.execute("UPDATE rules SET review_note=?, reviewed_by='human' WHERE id=?", (note_text, rule_id))
                result.note_changes.append(rule_id)
        if new_statement and new_statement != rule.statement:
            result.statement_changes.append((rule_id, new_statement))
            if not dry_run:
                kb.set_statement(rule_id, new_statement)
    if not dry_run:
        kb.conn.commit()
    return result

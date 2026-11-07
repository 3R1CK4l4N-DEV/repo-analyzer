"""Debounced project watching with reusable parser results and incremental vault writes."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from devanalyzer.config import Settings, get_settings
from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import ProjectAnalysis
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.kb.store import KnowledgeBase
from devanalyzer.obsidian.sync import sync_vault_to_kb
from devanalyzer.obsidian.vault import GENERATED_START, ObsidianVault


@dataclass
class WatchUpdate:
    analysis: ProjectAnalysis
    added: list[str]
    modified: list[str]
    removed: list[str]
    updated_notes: list[Path]
    archived_notes: list[Path]
    elapsed: float


class ProjectWatcher:
    def __init__(
        self,
        path: str | Path,
        settings: Settings | None = None,
        *,
        out: str | Path | None = None,
        debounce: float = 0.5,
        vault: bool = True,
        graphml: bool = True,
    ):
        self.settings = settings or get_settings()
        self.root = Path(path).resolve()
        if not self.root.is_dir():
            raise ValueError(f"Project directory not found: {self.root}")
        self.out = (Path(out) if out is not None else self.root / self.settings.output_dir_name).resolve()
        if self.root.is_relative_to(self.out):
            raise ValueError("Watch output must be separate from the project root and its parents")
        if debounce < 0:
            raise ValueError("Debounce must be non-negative")
        self.debounce = debounce
        self.vault = vault
        self.graphml = graphml
        self.analyzer = ProjectAnalyzer(self.settings, incremental=True)
        self.analysis: ProjectAnalysis | None = None
        self._published: dict[str, str] | None = None
        self._pending: dict[str, str] | None = None
        self._changed_at = 0.0
        manifest = self.out / "watch-notes.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = []
        self._notes = {p for p in data if isinstance(p, str)} if isinstance(data, list) else set()

    def poll(self, *, now: float | None = None) -> WatchUpdate | None:
        """Read a snapshot; publish the first scan immediately, then only settled changes.

        A failed read/write does not advance the published snapshot, so the next poll retries.
        """
        started = time.monotonic()
        now = started if now is None else now
        files = self.analyzer.reader.read(self.root, exclude_paths=(self.out,))
        snapshot = {f.rel_path: hashlib.sha256(f.content.encode("utf-8")).hexdigest() for f in files}
        if snapshot == self._published:
            self._pending = None
            return None
        if snapshot != self._pending:
            self._pending = snapshot
            self._changed_at = now
        if self._published is not None and now - self._changed_at < self.debounce:
            return None

        previous = self._published or {}
        analysis = self.analyzer.run(self.root, files=files)
        graph = KnowledgeGraph.from_analysis(analysis)
        rules, glossary, decisions, groups = [], [], [], []
        vault_dir = self.out / "vault"
        kb_path = self.root / self.settings.output_dir_name / "kb.sqlite"
        if kb_path.is_file():
            with KnowledgeBase(kb_path) as kb:
                if self.vault and (vault_dir / "30 Business Rules").is_dir():
                    sync_vault_to_kb(vault_dir, kb, analysis.project_name)
                rules = [r for r in kb.rules(project=analysis.project_name) if r.status.value != "rejected"]
                glossary = kb.terms(project=analysis.project_name)
                decisions = kb.decisions(project=analysis.project_name)
                groups = kb.groups(project=analysis.project_name)
        self.out.mkdir(parents=True, exist_ok=True)
        updated_notes, archived_notes = [], []
        notes = self._notes
        # Stage complete artifacts before replacement, so MCP/readers never see truncated JSON.
        with TemporaryDirectory(prefix="watch-", dir=self.out) as staging_dir:
            staging = Path(staging_dir)
            graph.save(staging, graphml=self.graphml)
            (staging / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
            if self.vault:
                vault = ObsidianVault(vault_dir)
                written = vault.generate(analysis, graph, rules, glossary, decisions, groups)
                notes = {p.relative_to(vault_dir).as_posix() for p in written}
                updated_notes = vault.updated
                archived_notes = self._archive_removed(vault_dir, self._notes - notes)
                (staging / "watch-notes.json").write_text(json.dumps(sorted(notes), indent=2), encoding="utf-8")
            for artifact in staging.iterdir():
                artifact.replace(self.out / artifact.name)

        self.analysis = analysis
        self._published = snapshot
        self._pending = None
        self._notes = notes
        return WatchUpdate(
            analysis=analysis,
            added=sorted(snapshot.keys() - previous.keys()),
            modified=sorted(p for p in snapshot.keys() & previous.keys() if snapshot[p] != previous[p]),
            removed=sorted(previous.keys() - snapshot.keys()),
            updated_notes=updated_notes,
            archived_notes=archived_notes,
            elapsed=time.monotonic() - started,
        )

    @staticmethod
    def _archive_removed(vault: Path, removed: set[str]) -> list[Path]:
        archive = vault / "90 Archive" / uuid4().hex[:12]
        archived = []
        for rel in sorted(removed):
            path = (vault / rel).resolve()
            # The persisted note list is input, not authority to move arbitrary files.
            if not path.is_relative_to(vault.resolve()) or not path.is_file() or path.suffix != ".md":
                continue
            content = path.read_text(encoding="utf-8")
            if GENERATED_START not in content or "generated_by: devanalyzer\n" not in content:
                continue
            destination = archive / path.relative_to(vault.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.replace(destination)
            archived.append(destination)
        return archived

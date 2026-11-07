import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devanalyzer.cli import app
from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.watch import ProjectWatcher
from devanalyzer.graph.knowledge_graph import KnowledgeGraph
from devanalyzer.kb.store import KnowledgeBase
from devanalyzer.languages.java.kotlin_parser import KotlinParser
from devanalyzer.languages.java.parser import JavaParser
from devanalyzer.languages.node.parser import NodeParser
from devanalyzer.languages.python.parser import PythonParser
from devanalyzer.llm.business_rules import BusinessRuleExtractor
from devanalyzer.obsidian.vault import GENERATED_START
from tests.test_business_rules import FakeProvider


@pytest.mark.parametrize(
    ("fixture", "parser_class", "comment"),
    [
        ("java_spring", JavaParser, "//"),
        ("kotlin_spring", KotlinParser, "//"),
        ("node_nest", NodeParser, "//"),
        ("python_fastapi", PythonParser, "#"),
    ],
)
def test_incremental_parsing_reuses_unchanged_files_and_rebuilds_relations(settings, fixtures, tmp_path, monkeypatch, fixture, parser_class, comment):
    root = tmp_path / "project"
    shutil.copytree(fixtures / fixture, root)
    calls = []
    parse = parser_class.parse

    def counted(self, source):
        calls.append(source.path)
        return parse(self, source)

    monkeypatch.setattr(parser_class, "parse", counted)
    analyzer = ProjectAnalyzer(settings, incremental=True)
    first = analyzer.run(root)
    parsed_paths = calls[:]
    assert parsed_paths
    calls.clear()
    second = analyzer.run(root)
    # Cached parses are shared (read-only contract); re-applying classification must be idempotent.
    assert calls == [] and second == first == ProjectAnalyzer(settings).run(root)
    calls.clear()

    changed = next(Path(p) for p in parsed_paths if not p.endswith("__init__.py"))
    changed.write_text(changed.read_text(encoding="utf-8") + f"\n{comment} edit\n", encoding="utf-8")
    updated = analyzer.run(root)
    assert calls == [str(changed)]
    assert updated == ProjectAnalyzer(settings).run(root)
    calls.clear()

    original = changed.read_text(encoding="utf-8")
    changed.unlink()
    removed = analyzer.run(root)
    assert calls == []
    assert removed == ProjectAnalyzer(settings).run(root)
    calls.clear()
    changed.write_text(original, encoding="utf-8")
    restored = analyzer.run(root)
    assert calls == [str(changed)]
    assert restored == updated


def test_python_package_changes_invalidate_module_resolution(settings, tmp_path):
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "service.py").write_text("class Service:\n    pass\n", encoding="utf-8")
    analyzer = ProjectAnalyzer(settings, incremental=True)
    before = analyzer.run(tmp_path)
    assert any(u.fqn == "service.Service" for u in before.units)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    after = analyzer.run(tmp_path)
    assert any(u.fqn == "pkg.service.Service" for u in after.units)
    assert after == ProjectAnalyzer(settings).run(tmp_path)


def test_watch_debounces_changes_skips_own_output_and_archives_removed_notes(settings, fixtures, tmp_path):
    root = tmp_path / "project"
    shutil.copytree(fixtures / "java_spring", root)
    watcher = ProjectWatcher(root, settings, out=root / "documentation", debounce=0.5)
    initial = watcher.poll(now=0)
    assert initial and initial.added and initial.updated_notes
    assert watcher.poll(now=0.5) is None
    stable_note = watcher.out / "vault" / "20 Classes" / "AccountService.md"
    stable_content, stable_mtime = stable_note.read_bytes(), stable_note.stat().st_mtime_ns

    controller = root / "src/main/java/com/acme/bank/controller/ExtraController.java"
    controller.write_text(
        'package com.acme.bank.controller;\n@RestController public class ExtraController { @GetMapping("/ping") public String ping() { return "ok"; } }\n',
        encoding="utf-8",
    )
    assert watcher.poll(now=1) is None
    controller.write_text(controller.read_text(encoding="utf-8").replace('"/ping"', '"/pong"'), encoding="utf-8")
    assert watcher.poll(now=1.25) is None
    assert watcher.poll(now=1.6) is None
    updated = watcher.poll(now=1.8)
    assert updated and updated.added == [controller.relative_to(root).as_posix()]
    assert any(e.path == "/pong" for e in updated.analysis.endpoints)
    assert not any(e.path == "/ping" for e in updated.analysis.endpoints)
    assert stable_note.read_bytes() == stable_content and stable_note.stat().st_mtime_ns == stable_mtime
    assert watcher.poll(now=2) is None
    assert json.loads((watcher.out / "analysis.json").read_text(encoding="utf-8"))["project_name"] == updated.analysis.project_name

    note = watcher.out / "vault" / "20 Classes" / "ExtraController.md"
    note.write_text(note.read_text(encoding="utf-8") + "\nKeep my manual explanation.\n", encoding="utf-8")
    # Restarting the watch still knows which notes it owns.
    controller.unlink()
    restarted = ProjectWatcher(root, settings, out=watcher.out)
    result = restarted.poll(now=3)
    assert result and not note.exists()
    archived = next(p for p in result.archived_notes if p.name == "ExtraController.md")
    assert "Keep my manual explanation." in archived.read_text(encoding="utf-8")
    assert not any(e.path == "/pong" for e in result.analysis.endpoints)
    assert restarted.poll(now=4) is None


def test_watch_uses_content_hash_and_retries_failed_publication(settings, fixtures, tmp_path, monkeypatch):
    root = tmp_path / "project"
    shutil.copytree(fixtures / "node_next", root)
    watcher = ProjectWatcher(root, settings, debounce=0, vault=False)
    assert watcher.poll(now=0)
    graph_before = (watcher.out / "graph.json").read_bytes()
    manifest = root / "package.json"
    old = manifest.stat()
    content = manifest.read_bytes()
    data = json.loads(content)
    # Same byte length and mtime must not hide a change.
    modified = content.replace(data["name"].encode("utf-8"), b"x" * len(data["name"].encode("utf-8")))
    assert modified != content
    manifest.write_bytes(modified)
    os.utime(manifest, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert manifest.stat().st_size == old.st_size and manifest.stat().st_mtime_ns == old.st_mtime_ns
    save = KnowledgeGraph.save

    def broken(self, out, graphml=True):
        save(self, out, graphml)
        raise OSError("disk unavailable")

    monkeypatch.setattr(KnowledgeGraph, "save", broken)
    with pytest.raises(OSError, match="disk unavailable"):
        watcher.poll(now=1)
    assert (watcher.out / "graph.json").read_bytes() == graph_before
    monkeypatch.setattr(KnowledgeGraph, "save", save)
    result = watcher.poll(now=2)
    assert result and result.modified == ["package.json"]
    assert KnowledgeGraph.load(watcher.out).g.number_of_nodes() > 0
    assert not (watcher.out / "vault").exists()
    assert watcher.poll(now=3) is None


def test_watch_syncs_manual_rule_reviews_before_regeneration(settings, fixtures, tmp_path):
    root = tmp_path / "project"
    shutil.copytree(fixtures / "java_spring", root)
    analysis = ProjectAnalyzer(settings).run(root)
    result = BusinessRuleExtractor(FakeProvider()).extract(analysis, root, stereotypes={"service"})
    kb_path = root / settings.output_dir_name / "kb.sqlite"
    with KnowledgeBase(kb_path) as kb:
        kb.ingest(result)
    watcher = ProjectWatcher(root, settings, debounce=0)
    assert watcher.poll(now=0)
    note = next((watcher.out / "vault" / "30 Business Rules").glob("*.md"))
    original = note.read_text(encoding="utf-8")
    note.write_text(original.replace("status: inferred", "status: confirmed") + "\nHuman insight.\n", encoding="utf-8")
    source = next(root.rglob("AccountService.java"))
    source.write_text(source.read_text(encoding="utf-8") + "\n// edit\n", encoding="utf-8")
    assert watcher.poll(now=1)
    assert "status: confirmed" in note.read_text(encoding="utf-8") and "Human insight." in note.read_text(encoding="utf-8")
    with KnowledgeBase(kb_path) as kb:
        assert len(kb.rules(status="confirmed")) == 1


def test_watch_manifest_cannot_archive_files_outside_the_vault(tmp_path, settings):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "keep.md"
    outside.write_text("generated_by: devanalyzer\n" + GENERATED_START, encoding="utf-8")
    assert ProjectWatcher._archive_removed(vault, {"../keep.md", str(outside)}) == []
    assert outside.exists()
    with pytest.raises(ValueError, match="separate"):
        ProjectWatcher(tmp_path, settings, out=tmp_path)


def test_watch_cli_once_and_interrupt(settings, fixtures, tmp_path, monkeypatch):
    root = tmp_path / "project"
    shutil.copytree(fixtures / "python_fastapi", root)
    monkeypatch.setattr("devanalyzer.cli.get_settings", lambda: settings)
    runner = CliRunner()
    result = runner.invoke(app, ["watch", str(root), "--once", "--no-vault", "--no-graphml"])
    assert result.exit_code == 0, result.output
    assert "notes updated" in result.output
    assert (root / ".devanalyzer" / "graph.json").is_file()
    assert not (root / ".devanalyzer" / "graph.graphml").exists()

    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(ProjectWatcher, "poll", interrupted)
    result = runner.invoke(app, ["watch", str(root)])
    assert result.exit_code == 0 and "Watch stopped." in result.output

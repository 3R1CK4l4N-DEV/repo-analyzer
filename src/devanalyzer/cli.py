from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from devanalyzer import __version__
from devanalyzer.config import get_settings
from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import CodeUnitKind, ProjectAnalysis
from devanalyzer.graph.knowledge_graph import KnowledgeGraph

app = typer.Typer(help="DevAnalyzer: analyze projects, build a knowledge graph, extract business rules.", no_args_is_help=True)
graph_app = typer.Typer(help="Build and query the project knowledge graph.", no_args_is_help=True)
rules_app = typer.Typer(help="Extract and review AI-inferred business rules.", no_args_is_help=True)
app.add_typer(graph_app, name="graph")
app.add_typer(rules_app, name="rules")
console = Console()


def _version(value: bool):
    if value:
        console.print(f"devanalyzer {__version__}")
        raise typer.Exit()


@app.callback()
def main(version: bool = typer.Option(False, "--version", callback=_version, is_eager=True)):
    pass


def _render_summary(analysis: ProjectAnalysis) -> None:
    console.rule(f"[bold]{analysis.project_name}[/bold]")
    console.print(f"Path: {analysis.project_path}")
    console.print(f"Files: {analysis.metrics.files}  LOC: {analysis.metrics.loc}")

    t = Table(title="Languages")
    t.add_column("Language"), t.add_column("Confidence"), t.add_column("Files"), t.add_column("LOC")
    for lang in analysis.languages:
        t.add_row(lang.language.value, f"{lang.confidence:.2f}", str(lang.files), str(lang.loc))
    console.print(t)

    t = Table(title="Build systems")
    t.add_column("System"), t.add_column("Manifest"), t.add_column("Versions")
    for b in analysis.builds:
        t.add_row(b.system.value, b.manifest_path, ", ".join(f"{k}={v}" for k, v in b.versions.items()) or "-")
    console.print(t)

    if analysis.frameworks:
        t = Table(title="Frameworks")
        t.add_column("Name"), t.add_column("Version")
        for f in analysis.frameworks:
            t.add_row(f.name, f.version or "managed")
        console.print(t)

    if analysis.metrics.stereotypes:
        t = Table(title="Stereotypes")
        t.add_column("Stereotype"), t.add_column("Count")
        for k, v in sorted(analysis.metrics.stereotypes.items()):
            t.add_row(k, str(v))
        console.print(t)


def _render_dependencies(analysis: ProjectAnalysis) -> None:
    t = Table(title="Dependencies")
    t.add_column("Name"), t.add_column("Version"), t.add_column("Scope"), t.add_column("Manifest")
    for d in analysis.dependencies:
        t.add_row(d.name, d.version or "managed", d.scope, d.source_file or "")
    console.print(t)


def _render_units(analysis: ProjectAnalysis) -> None:
    t = Table(title="Code units")
    t.add_column("Kind"), t.add_column("FQN"), t.add_column("Stereotype"), t.add_column("File"), t.add_column("Lines")
    for u in analysis.units:
        t.add_row(u.kind.value, u.fqn, u.stereotype or "", u.file, f"{u.line_start}-{u.line_end}")
        for c in u.children:
            if c.kind == CodeUnitKind.METHOD:
                t.add_row("  method", f"  {c.name}({', '.join(p.type or '?' for p in c.parameters)})", "", "", f"{c.line_start}-{c.line_end}")
    console.print(t)


def _render_endpoints(analysis: ProjectAnalysis) -> None:
    t = Table(title="Endpoints")
    t.add_column("Method"), t.add_column("Path"), t.add_column("Handler"), t.add_column("Location")
    for e in analysis.endpoints:
        t.add_row(e.http_method, e.path, e.handler, f"{e.file}:{e.line}")
    console.print(t)


@app.command()
def analyze(
    path: Path = typer.Argument(..., exists=True, help="Project root to analyze"),
    show: list[str] = typer.Option([], "--show", "-s", help="Extra sections: deps, units, endpoints, all"),
    json_out: Path | None = typer.Option(None, "--json", help="Write full analysis as JSON to this file"),
):
    """Analyze a project: languages, build system, versions, frameworks, structure."""
    analysis = ProjectAnalyzer().run(path)
    _render_summary(analysis)
    sections = set(show)
    if "all" in sections:
        sections = {"deps", "units", "endpoints"}
    if "deps" in sections:
        _render_dependencies(analysis)
    if "units" in sections:
        _render_units(analysis)
    if "endpoints" in sections:
        _render_endpoints(analysis)
    if json_out:
        json_out.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[green]Analysis written to {json_out}[/green]")


@app.command("llm-ping")
def llm_ping(prompt: str = typer.Argument("Reply with the single word: pong")):
    """Verify the configured LLM provider responds."""
    from devanalyzer.llm.provider import get_provider

    settings = get_settings()
    provider = get_provider(settings)
    console.print(f"Provider: {provider.name}  Model: {provider.model}")
    console.print(provider.generate(prompt))


def _default_out(path: Path) -> Path:
    return path / get_settings().output_dir_name


@graph_app.command("build")
def graph_build(
    path: Path = typer.Argument(..., exists=True, help="Project root"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output directory (default: <project>/.devanalyzer)"),
    no_graphml: bool = typer.Option(False, "--no-graphml", help="Skip the GraphML export (faster on very large projects)"),
):
    """Analyze a project and persist its knowledge graph (JSON + GraphML)."""
    analysis = ProjectAnalyzer().run(path)
    kg = KnowledgeGraph.from_analysis(analysis)
    out_dir = out or _default_out(path.resolve())
    paths = kg.save(out_dir, graphml=not no_graphml)
    (out_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    console.print(json.dumps(kg.stats(), indent=2))
    for fmt, p in paths.items():
        console.print(f"[green]{fmt}: {p}[/green]")


def _load_graph(graph: Path) -> KnowledgeGraph:
    candidates = [graph, graph / "graph.json", graph / get_settings().output_dir_name / "graph.json"]
    target = next((c for c in candidates if c.is_file()), None)
    if target is None:
        console.print(f"[red]No graph.json found under {graph}. Run `devanalyzer graph build` first.[/red]")
        raise typer.Exit(1)
    return KnowledgeGraph.load(target)


@graph_app.command("stats")
def graph_stats(graph: Path = typer.Argument(..., help="Project root, .devanalyzer dir or graph.json")):
    """Show node/edge counts of a saved graph."""
    console.print(json.dumps(_load_graph(graph).stats(), indent=2))


@graph_app.command("query")
def graph_query(
    graph: Path = typer.Argument(..., help="Project root, .devanalyzer dir or graph.json"),
    neighbors: str | None = typer.Option(None, "--neighbors", "-n", help="Node name or FQN to inspect"),
    depth: int = typer.Option(1, "--depth", "-d"),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter edges by relation kind"),
    top: int | None = typer.Option(None, "--top", help="Show the N most referenced types"),
    path_between: tuple[str, str] | None = typer.Option(None, "--path", help="Shortest path between two nodes"),
):
    """Query a saved knowledge graph."""
    kg = _load_graph(graph)
    if neighbors:
        matches = kg.find(neighbors)
        if not matches:
            console.print(f"[red]No node matches {neighbors!r}[/red]")
            raise typer.Exit(1)
        node = matches[0]
        console.print(f"[bold]{node}[/bold] {dict(kg.g.nodes[node])}")
        sub = kg.subgraph_for(node, depth)
        t = Table(title=f"Relations within depth {depth}")
        t.add_column("Source"), t.add_column("Kind"), t.add_column("Target")
        for u, v, k in sub.g.edges(keys=True):
            if kind is None or k == kind:
                t.add_row(u, k, v)
        console.print(t)
    if top:
        t = Table(title=f"Top {top} by in-degree")
        t.add_column("Node"), t.add_column("In-degree"), t.add_column("PageRank")
        pr = dict(kg.pagerank(n=10_000))
        for node, deg in kg.top_by_degree(top):
            t.add_row(node, str(deg), f"{pr.get(node, 0):.4f}")
        console.print(t)
    if path_between:
        a = kg.find(path_between[0])
        b = kg.find(path_between[1])
        if not a or not b:
            console.print("[red]One of the nodes was not found[/red]")
            raise typer.Exit(1)
        route = kg.shortest_path(a[0], b[0])
        console.print(" -> ".join(route) if route else "[yellow]No path[/yellow]")


def _kb_path(project: Path) -> Path:
    return _default_out(project.resolve()) / "kb.sqlite"


def _render_rules(rules, title: str = "Business rules") -> None:
    t = Table(title=title)
    t.add_column("ID"), t.add_column("Status"), t.add_column("Cat."), t.add_column("Conf."), t.add_column("Statement"), t.add_column("Evidence")
    for r in rules:
        ev = r.evidence[0] if r.evidence else None
        t.add_row(r.id, r.status.value, r.category, f"{r.confidence:.2f}", r.statement, f"{ev.file}:{ev.line_start}-{ev.line_end}" if ev else "")
    console.print(t)


@rules_app.command("extract")
def rules_extract(
    path: Path = typer.Argument(..., exists=True, help="Project root"),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Max units to send to the LLM"),
    stereotype: list[str] = typer.Option([], "--stereotype", "-s", help="Only these stereotypes (service, controller, entity...)"),
    language: str = typer.Option("English", "--lang", help="Language for the rule statements (e.g. Spanish)"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore cached LLM responses"),
    workers: int | None = typer.Option(None, "--workers", "-w", help="Parallel LLM calls (default: 1 for ollama, 4 for cloud providers)"),
    verify: bool = typer.Option(False, "--verify", help="Second LLM pass that audits each rule against its evidence (rejects unsupported/technical ones)"),
):
    """Infer business rules with the configured LLM and store them in the knowledge base."""
    import itertools

    from devanalyzer.kb.store import KnowledgeBase
    from devanalyzer.llm.business_rules import BusinessRuleExtractor
    from devanalyzer.llm.provider import get_provider

    settings = get_settings()
    analysis = ProjectAnalyzer(settings).run(path)
    out_dir = _default_out(path.resolve())
    provider = get_provider(settings)
    workers = workers or (1 if provider.name == "ollama" else 4)
    counter = itertools.count(1)
    extractor = BusinessRuleExtractor(
        provider,
        settings,
        cache_dir=None if no_cache else out_dir / "cache",
        output_language=language,
        progress=lambda fqn: console.print(f"[dim][{next(counter)}] {fqn}[/dim]"),
    )
    console.print(f"Provider: {provider.name}  Model: {provider.model}  Workers: {workers}")
    result = extractor.extract(analysis, path, stereotypes=set(stereotype) or None, limit=limit, workers=workers)
    if verify:
        from devanalyzer.llm.provider import get_verify_provider

        judge = get_verify_provider(settings)
        console.print(f"Verifier: {judge.name}/{judge.model}")
        result = extractor.verify(result, root=path, workers=workers, judge=judge)
        rejected = sum(1 for r in result.rules if r.status.value == "rejected")
        console.print(f"Verifier: {len(result.rules) - rejected} kept, {rejected} rejected as unsupported/technical")
    (out_dir / "rules.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    with KnowledgeBase(out_dir / "kb.sqlite") as kb:
        counts = kb.ingest(result)
    _render_rules(result.rules)
    console.print(f"Units analyzed: {result.units_analyzed}  skipped: {result.units_skipped}  errors: {len(result.errors)}")
    console.print(f"KB run #{counts['run_id']}: new={counts['new']} seen={counts['seen']} changed={counts['changed']}")
    for err in result.errors[:10]:
        console.print(f"[yellow]{err}[/yellow]")


@rules_app.command("list")
def rules_list(
    path: Path = typer.Argument(..., exists=True, help="Project root"),
    status: str | None = typer.Option(None, "--status"),
    category: str | None = typer.Option(None, "--category"),
    unit: str | None = typer.Option(None, "--unit", help="Filter by source unit (substring)"),
    search: str | None = typer.Option(None, "--search", "-q"),
):
    """List business rules stored in the knowledge base."""
    from devanalyzer.kb.store import KnowledgeBase

    with KnowledgeBase(_kb_path(path)) as kb:
        _render_rules(kb.rules(status=status, category=category, unit=unit, search=search))
        console.print(json.dumps(kb.summary(), indent=2))


@rules_app.command("review")
def rules_review(
    path: Path = typer.Argument(..., exists=True, help="Project root"),
    rule_id: str = typer.Argument(..., help="Rule id, e.g. BR-1a2b3c4d5e"),
    status: str = typer.Option(..., "--status", help="confirmed | rejected | inferred"),
    note: str | None = typer.Option(None, "--note"),
):
    """Record a human review decision for a rule (persists across re-extractions)."""
    from devanalyzer.core.models import RuleStatus
    from devanalyzer.kb.store import KnowledgeBase

    with KnowledgeBase(_kb_path(path)) as kb:
        ok = kb.set_status(rule_id, RuleStatus(status), note)
    console.print("[green]updated[/green]" if ok else f"[red]rule {rule_id} not found[/red]")


@rules_app.command("eval")
def rules_eval(
    path: Path = typer.Argument(..., exists=True, help="Project root"),
    gold: Path | None = typer.Option(None, "--gold", "-g", help="Gold file (default: <project>/.devanalyzer/gold_rules.json)"),
    source: Path | None = typer.Option(None, "--source", help="rules.json to evaluate (default: knowledge base)"),
    include_rejected: bool = typer.Option(False, "--include-rejected", help="Count rejected rules as predictions too"),
    json_out: Path | None = typer.Option(None, "--json"),
):
    """Measure precision/recall of extracted rules against a hand-labelled gold set (no LLM involved)."""
    from devanalyzer.core.models import RuleExtractionResult
    from devanalyzer.llm.evaluation import GoldSet, evaluate

    gold_path = gold or (_default_out(path.resolve()) / "gold_rules.json")
    if not gold_path.is_file():
        console.print(f"[red]No gold file at {gold_path}. See src/devanalyzer/llm/evaluation.py for the format.[/red]")
        raise typer.Exit(1)
    gold_set = GoldSet.load(gold_path)
    if source:
        result = RuleExtractionResult.model_validate_json(source.read_text(encoding="utf-8"))
        rules, model = result.rules, result.model
    else:
        from devanalyzer.kb.store import KnowledgeBase

        with KnowledgeBase(_kb_path(path)) as kb:
            rules = kb.rules()
        model = next((r.model for r in rules if r.model), "?")
    report = evaluate(rules, gold_set, model=model or "?", include_rejected=include_rejected)
    console.rule(f"[bold]eval · {report.model}[/bold]")
    console.print(report.summary_line())
    if report.category_accuracy is not None:
        console.print(f"Category accuracy on matches: {report.category_accuracy:.0%}")
    if report.matches:
        t = Table(title="Matches")
        t.add_column("Gold"), t.add_column("Rule"), t.add_column("Score"), t.add_column("Statement")
        for m in report.matches:
            t.add_row(m.gold_id, m.rule_id, f"{m.score:.2f}", m.statement[:90])
        console.print(t)
    for title, items, color in (("Missed (false negatives)", report.missed, "yellow"), ("Spurious (false positives)", report.spurious, "red")):
        if items:
            console.print(f"[{color}]{title}:[/{color}]")
            for i in items:
                console.print(f"  - {i[:120]}")
    if json_out:
        json_out.write_text(report.model_dump_json(indent=2), encoding="utf-8")


@rules_app.command("glossary")
def rules_glossary(path: Path = typer.Argument(..., exists=True), search: str | None = typer.Option(None, "-q")):
    """Show the domain glossary inferred from the code."""
    from devanalyzer.kb.store import KnowledgeBase

    with KnowledgeBase(_kb_path(path)) as kb:
        t = Table(title="Glossary")
        t.add_column("Term"), t.add_column("Definition"), t.add_column("Units"), t.add_column("Rules")
        for term in kb.terms(search=search):
            t.add_row(term.term, term.definition, ", ".join(u.rsplit(".", 1)[-1] for u in term.related_units), str(len(term.rules)))
        console.print(t)


@app.command("vault")
def vault_generate(
    path: Path = typer.Argument(..., exists=True, help="Project root"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Obsidian vault directory (default: <project>/.devanalyzer/vault)"),
    folder: str | None = typer.Option(None, "--folder", help="Sub-folder inside an existing vault to write into"),
    include_rejected: bool = typer.Option(False, "--include-rejected"),
):
    """Generate or refresh an Obsidian vault with architecture, rules and glossary notes."""
    from devanalyzer.kb.store import KnowledgeBase
    from devanalyzer.obsidian.vault import ObsidianVault

    analysis = ProjectAnalyzer().run(path)
    kg = KnowledgeGraph.from_analysis(analysis)
    kb_path = _kb_path(path)
    rules, glossary, decisions = [], [], []
    if kb_path.exists():
        with KnowledgeBase(kb_path) as kb:
            rules = [r for r in kb.rules(project=analysis.project_name) if include_rejected or r.status.value != "rejected"]
            glossary = kb.terms(project=analysis.project_name)
            decisions = kb.decisions(project=analysis.project_name)
    vault_dir = out or (_default_out(path.resolve()) / "vault")
    written = ObsidianVault(vault_dir, folder).generate(analysis, kg, rules, glossary, decisions)
    console.print(f"[green]{len(written)} notes written to {vault_dir}[/green]  (rules: {len(rules)}, terms: {len(glossary)})")
    console.print("Open the folder as a vault in Obsidian; start from the '<project> - Home' note.")


def _load_kb_data(path: Path, project_name: str):
    kb_path = _kb_path(path)
    if not kb_path.exists():
        return [], []
    from devanalyzer.kb.store import KnowledgeBase

    with KnowledgeBase(kb_path) as kb:
        return kb.rules(project=project_name), kb.terms(project=project_name)


@app.command("report")
def report(
    path: Path = typer.Argument(..., exists=True, help="Project root"),
    fmt: str = typer.Option("html", "--format", "-f", help="html | md"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output file (default: <project>/.devanalyzer/report.<fmt>)"),
):
    """Render a self-contained HTML or Markdown report (architecture, endpoints, rules, glossary)."""
    from devanalyzer.reporting.html_report import render_html, render_markdown

    analysis = ProjectAnalyzer().run(path)
    kg = KnowledgeGraph.from_analysis(analysis)
    rules, glossary = _load_kb_data(path, analysis.project_name)
    rules = [r for r in rules if r.status.value != "rejected"]
    if fmt not in ("html", "md"):
        console.print("[red]--format must be html or md[/red]")
        raise typer.Exit(1)
    content = render_html(analysis, kg, rules, glossary) if fmt == "html" else render_markdown(analysis, kg, rules, glossary)
    target = out or (_default_out(path.resolve()) / f"report.{fmt}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    console.print(f"[green]Report written to {target}[/green]")


@app.command("snapshot")
def snapshot(path: Path = typer.Argument(..., exists=True, help="Project root")):
    """Save the current analysis as the baseline for `devanalyzer diff`."""
    analysis = ProjectAnalyzer().run(path)
    out_dir = _default_out(path.resolve())
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"[green]Snapshot saved to {out_dir / 'analysis.json'}[/green]")


@app.command("diff")
def diff(
    path: Path = typer.Argument(..., exists=True, help="Project root"),
    baseline: Path | None = typer.Option(None, "--baseline", "-b", help="analysis.json to compare against (default: <project>/.devanalyzer/analysis.json)"),
    update: bool = typer.Option(False, "--update", help="Overwrite the baseline with the current analysis afterwards"),
):
    """Compare the current code structure against the last snapshot (types, methods, endpoints, deps, versions)."""
    from devanalyzer.reporting.diff import diff_analyses, render_diff_markdown

    base_path = baseline or (_default_out(path.resolve()) / "analysis.json")
    if not base_path.is_file():
        console.print(f"[red]No baseline at {base_path}. Run `devanalyzer snapshot {path}` or `graph build` first.[/red]")
        raise typer.Exit(1)
    before = ProjectAnalysis.model_validate_json(base_path.read_text(encoding="utf-8"))
    after = ProjectAnalyzer().run(path)
    result = diff_analyses(before, after)
    console.print(render_diff_markdown(result))
    if update:
        base_path.write_text(after.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[green]Baseline updated: {base_path}[/green]")


@app.command("serve")
def serve(
    path: Path = typer.Argument(..., exists=True, help="Project root"),
    sse: bool = typer.Option(False, "--sse", help="Serve over HTTP/SSE instead of stdio"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
):
    """Start the MCP server so AI agents can query the graph, rules and glossary."""
    try:
        from devanalyzer.mcp_server.server import run
    except ImportError:
        console.print("[red]Install the optional dependency: pip install 'devanalyzer[mcp]'[/red]")
        raise typer.Exit(1) from None
    run(path, transport="sse" if sse else "stdio", host=host, port=port)


if __name__ == "__main__":
    app()

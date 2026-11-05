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
app.add_typer(graph_app, name="graph")
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
):
    """Analyze a project and persist its knowledge graph (JSON + GraphML)."""
    analysis = ProjectAnalyzer().run(path)
    kg = KnowledgeGraph.from_analysis(analysis)
    out_dir = out or _default_out(path.resolve())
    paths = kg.save(out_dir)
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


if __name__ == "__main__":
    app()

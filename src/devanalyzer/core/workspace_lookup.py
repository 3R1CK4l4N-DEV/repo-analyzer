"""Locate a previously computed workspace analysis for a given project."""

from __future__ import annotations

import os
from pathlib import Path

from devanalyzer.config import get_settings
from devanalyzer.core.workspace import ServiceLink, WorkspaceAnalysis


def find_workspace(project_root: str | Path) -> WorkspaceAnalysis | None:
    """`DEVANALYZER_WORKSPACE` (dir or workspace.json) wins; otherwise walk up to 3 parents looking for
    `<dir>/.devanalyzer/workspace.json` that mentions this project path."""
    root = Path(project_root).resolve()
    out_name = get_settings().output_dir_name
    candidates: list[Path] = []
    if env := os.environ.get("DEVANALYZER_WORKSPACE"):
        p = Path(env)
        candidates.append(p if p.suffix == ".json" else p / out_name / "workspace.json")
    parent = root.parent
    for _ in range(3):
        candidates.append(parent / out_name / "workspace.json")
        if parent.parent == parent:
            break
        parent = parent.parent
    for c in candidates:
        if c.is_file():
            ws = WorkspaceAnalysis.model_validate_json(c.read_text(encoding="utf-8"))
            if any(Path(p["path"]).resolve() == root for p in ws.projects) or os.environ.get("DEVANALYZER_WORKSPACE"):
                return ws
    return None


def project_name_in(ws: WorkspaceAnalysis, project_root: str | Path) -> str | None:
    root = Path(project_root).resolve()
    return next((p["name"] for p in ws.projects if Path(p["path"]).resolve() == root), None)


def links_for(ws: WorkspaceAnalysis, name: str) -> dict[str, list[ServiceLink]]:
    return {
        "outgoing": [link for link in ws.links if link.source == name],
        "incoming": [link for link in ws.links if link.target == name],
    }

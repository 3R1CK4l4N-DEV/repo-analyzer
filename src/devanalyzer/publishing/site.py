from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from devanalyzer.config import Settings
from devanalyzer.publishing.bundle import PublishManifest, PublishSource, build_bundle, sanitize_slug


@dataclass(frozen=True)
class RepoSpec:
    url: str
    slug: str
    ref: str | None = None


class RepoEntry(BaseModel):
    repo: str
    ref: str | None = None
    slug: str | None = None
    rules: str | None = None
    kb: str | None = None
    subdir: str | None = None


class SiteConfig(BaseModel):
    workspace: bool = False
    projects: list[RepoEntry] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> SiteConfig:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def parse_repo(spec: str) -> RepoSpec:
    value = spec.strip().rstrip("/")
    ref = None
    if "@" in value:
        value, ref = value.rsplit("@", 1)
        if not ref:
            raise ValueError(f"Invalid repository ref: {spec}")
    if re.fullmatch(r"[^/\s]+/[^/\s]+", value):
        owner, name = value.split("/", 1)
        return RepoSpec(f"https://github.com/{owner}/{name}", name, ref)
    match = re.fullmatch(r"https://github\.com/([^/\s]+)/([^/\s]+)", value, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"Expected owner/name or a GitHub HTTPS URL: {spec}")
    owner, name = match.groups()
    name = name.removesuffix(".git")
    return RepoSpec(f"https://github.com/{owner}/{name}", name, ref)


def clone(url: str, ref: str | None, dest: Path) -> str:
    if dest.exists():
        shutil.rmtree(dest)
    command = ["git", "clone", "--depth", "1"]
    if ref:
        command += ["--branch", ref]
    command += [url, str(dest)]
    subprocess.run(command, check=True)
    return subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def _local_commit(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def fetch_baseline(base_url: str, slug: str, dest: Path) -> Path | None:
    url = f"{base_url.rstrip('/')}/p/{slug}/analysis.json"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            data = response.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest
    except Exception:
        return None


def write_index(data_dir: Path) -> Path:
    projects = []
    for path in (data_dir / "p").glob("*/manifest.json"):
        manifest = PublishManifest.model_validate_json(path.read_text(encoding="utf-8"))
        projects.append({**manifest.model_dump(mode="json"), "base": f"p/{manifest.slug}/"})
    projects.sort(key=lambda project: project["name"].casefold())
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "projects": projects,
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "projects.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


@dataclass
class _PreparedRepo:
    entry: RepoEntry
    slug: str
    root: Path
    project: Path
    source: PublishSource


def _path_like(value: str) -> bool:
    path = Path(value)
    return path.is_absolute() or value.startswith((".", "/", "\\")) or "\\" in value


def _prepare(entry: RepoEntry, config_dir: Path, work_root: Path) -> _PreparedRepo:
    candidate = Path(entry.repo)
    local = candidate if candidate.is_absolute() else config_dir / candidate
    if local.exists():
        root = local.resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)
        slug = sanitize_slug(entry.slug or root.name)
        source = PublishSource(ref=entry.ref, commit=_local_commit(root), subdir=entry.subdir)
    else:
        if _path_like(entry.repo):
            raise FileNotFoundError(local)
        spec = parse_repo(entry.repo)
        slug = sanitize_slug(entry.slug or spec.slug)
        ref = entry.ref or spec.ref
        root = work_root / slug
        commit = clone(spec.url, ref, root)
        source = PublishSource(repo_url=spec.url, ref=ref, commit=commit, subdir=entry.subdir)
    project = (root / entry.subdir).resolve() if entry.subdir else root
    if not project.is_dir() or not project.is_relative_to(root):
        raise FileNotFoundError(f"Invalid subdir {entry.subdir!r} in {root}")
    return _PreparedRepo(entry=entry, slug=slug, root=root, project=project, source=source)


def _workspace(prepared: list[_PreparedRepo], work_root: Path, out: Path, settings: Settings | None = None) -> None:
    from devanalyzer.core.workspace import WorkspaceAnalyzer, WorkspaceConfig, workspace_markdown

    config_path = work_root / "devanalyzer.workspace.json"
    config_path.write_text(
        json.dumps({"name": "published-projects", "projects": [{"name": item.slug, "path": str(item.project)} for item in prepared]}, indent=2),
        encoding="utf-8",
    )
    workspace = WorkspaceAnalyzer(settings).run(WorkspaceConfig.load(config_path))
    workspace_dir = work_root / ".devanalyzer"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    workspace_json = workspace_dir / "workspace.json"
    workspace_md = workspace_dir / "workspace.md"
    workspace_json.write_text(workspace.model_dump_json(indent=2), encoding="utf-8")
    workspace_md.write_text(workspace_markdown(workspace), encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    published_workspace = workspace.model_copy(deep=True)
    display_paths = {item.slug: item.source.repo_url or item.slug for item in prepared}
    for project in published_workspace.projects:
        project["path"] = display_paths.get(project["name"], project["name"])
    (out / "workspace.json").write_text(published_workspace.model_dump_json(indent=2), encoding="utf-8")
    shutil.copy2(workspace_md, out / "workspace.md")


def publish_site(
    config_path: Path,
    out: Path,
    *,
    extra: list[str] = (),
    baseline_url: str | None = None,
    work_dir: Path | None = None,
    graphml: bool = True,
    progress: Callable[[str], None] | None = None,
    settings: Settings | None = None,
) -> list[PublishManifest]:
    config_path = config_path.resolve()
    config = SiteConfig.load(config_path)
    entries = list(config.projects)
    for value in extra:
        spec = parse_repo(value)
        entries.append(RepoEntry(repo=spec.url, ref=spec.ref, slug=spec.slug))

    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    own_work_dir = work_dir is None
    work_root = Path(tempfile.mkdtemp(prefix="devanalyzer-publish-")) if own_work_dir else work_dir.resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    notify = progress or (lambda _message: None)
    prepared: list[_PreparedRepo] = []
    failures: list[tuple[str, Exception]] = []
    previous_workspace = os.environ.get("DEVANALYZER_WORKSPACE")
    workspace_override = False
    try:
        for entry in entries:
            label = entry.slug or entry.repo
            try:
                notify(f"Preparing {label}")
                prepared.append(_prepare(entry, config_path.parent, work_root))
            except Exception as exc:
                failures.append((label, exc))
                notify(f"ERROR {label}: {exc}")

        if config.workspace and len(prepared) >= 2:
            try:
                notify("Analyzing workspace")
                _workspace(prepared, work_root, out, settings)
                os.environ["DEVANALYZER_WORKSPACE"] = str(work_root / ".devanalyzer" / "workspace.json")
                workspace_override = True
            except Exception as exc:
                notify(f"WARNING workspace: {exc}")

        manifests = []
        for item in prepared:
            try:
                notify(f"Analyzing {item.slug}")
                baseline = None
                if baseline_url:
                    baseline = fetch_baseline(baseline_url, item.slug, work_root / ".baselines" / f"{item.slug}.json")
                rules_path = (config_path.parent / item.entry.rules).resolve() if item.entry.rules else None
                kb_path = (config_path.parent / item.entry.kb).resolve() if item.entry.kb else None
                manifest = build_bundle(
                    item.project,
                    out / "p" / item.slug,
                    slug=item.slug,
                    source=item.source,
                    rules_path=rules_path,
                    kb_path=kb_path,
                    baseline=baseline,
                    graphml=graphml,
                    settings=settings,
                )
                manifests.append(manifest)
                notify(f"Published {item.slug}")
            except Exception as exc:
                failures.append((item.slug, exc))
                notify(f"ERROR {item.slug}: {exc}")

        if not manifests:
            details = "; ".join(f"{label}: {exc}" for label, exc in failures) or "no projects configured"
            raise RuntimeError(f"All projects failed: {details}")
        write_index(out)
        return manifests
    finally:
        if workspace_override:
            if previous_workspace is None:
                os.environ.pop("DEVANALYZER_WORKSPACE", None)
            else:
                os.environ["DEVANALYZER_WORKSPACE"] = previous_workspace
        if own_work_dir:
            shutil.rmtree(work_root, ignore_errors=True)

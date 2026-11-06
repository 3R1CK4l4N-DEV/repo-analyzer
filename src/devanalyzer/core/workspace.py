"""Multi-repository workspaces: analyze several projects and discover the links between them.

Workspace definition (`<dir>/devanalyzer.workspace.json`):
{ "name": "shop", "projects": [{"name": "orders", "path": "orders-service"}, {"path": "../billing"}] }
Without a file, every direct sub-directory containing a build manifest is a project.

Link detection (static heuristics, each link keeps its evidence):
- http:   string literals in project A that match an endpoint path of project B (path params normalised),
          plus Spring @FeignClient(name=...) / Micronaut @Client("...") targeting B's name or base URL.
- event:  producers/consumers sharing a topic/queue name (@KafkaListener, kafkaTemplate.send, @RabbitListener,
          @Incoming/@Outgoing, @StreamListener, SQS/SNS/EventBridge names, channel.publish/subscribe).
- table:  two projects persisting to the same table name (shared database).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from devanalyzer.config import Settings, get_settings
from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import ProjectAnalysis
from devanalyzer.core.reader import ProjectReader
from devanalyzer.core.source_text import without_comments

MANIFESTS = ("pom.xml", "build.gradle", "build.gradle.kts", "package.json", "pyproject.toml", "requirements.txt")
PATH_LITERAL_RE = re.compile(r"""["'`]((?:https?://[\w.\-:]+)?(/[A-Za-z0-9_\-{}:$./]*[A-Za-z0-9_}])+)["'`]""")
FEIGN_RE = re.compile(r"""@FeignClient\s*\(([^)]*)\)""")
NAME_KW_RE = re.compile(r"""(?:name|value|url)\s*=\s*["']([^"']+)["']""")
PRODUCER_RES = [
    re.compile(r"""\.send\s*\(\s*["']([\w.\-]+)["']"""),  # kafkaTemplate.send("topic", ...)
    re.compile(r"""convertAndSend\s*\(\s*["']([\w.\-/]+)["']"""),  # rabbitTemplate
    re.compile(r"""@Outgoing\s*\(\s*["']([\w.\-]+)["']"""),  # Quarkus
    re.compile(r"""@SendTo\s*\(\s*["']([\w.\-]+)["']"""),
    re.compile(r"""\.(?:publish|emit|produce)\s*\(\s*["']([\w.\-:/]+)["']"""),  # node/python clients
    re.compile(r"""TopicArn\s*=\s*["']([\w.\-:]+)["']"""),
]
CONSUMER_RES = [
    re.compile(r"""@KafkaListener\s*\([^)]*topics\s*=\s*\{?\s*["']([\w.\-]+)["']"""),
    re.compile(r"""@RabbitListener\s*\([^)]*queues\s*=\s*\{?\s*["']([\w.\-/]+)["']"""),
    re.compile(r"""@SqsListener\s*\(\s*(?:value\s*=\s*)?["']([\w.\-]+)["']"""),
    re.compile(r"""@Incoming\s*\(\s*["']([\w.\-]+)["']"""),  # Quarkus
    re.compile(r"""@StreamListener\s*\(\s*["']([\w.\-]+)["']"""),
    re.compile(r"""\.(?:subscribe|consume|on)\s*\(\s*["']([\w.\-:/]+)["']"""),
    re.compile(r"""@EventPattern\s*\(\s*["']([\w.\-]+)["']"""),  # NestJS microservices
    re.compile(r"""@MessagePattern\s*\(\s*["']([\w.\-]+)["']"""),
]
GENERIC_EVENT_WORDS = {"on", "error", "data", "end", "close", "message", "connect", "exit", "click", "change", "submit"}


class ProjectRef(BaseModel):
    name: str
    path: str


class WorkspaceConfig(BaseModel):
    name: str = "workspace"
    projects: list[ProjectRef] = Field(default_factory=list)

    @classmethod
    def load(cls, directory: str | Path) -> WorkspaceConfig:
        directory = Path(directory)
        cfg = directory if directory.is_file() else directory / "devanalyzer.workspace.json"
        directory = cfg.parent
        if cfg.is_file():
            data = json.loads(cfg.read_text(encoding="utf-8"))
            projects = []
            for p in data.get("projects", []):
                path = (directory / p["path"]).resolve()
                projects.append(ProjectRef(name=p.get("name") or path.name, path=str(path)))
            return cls(name=data.get("name") or directory.name, projects=projects)
        projects = [
            ProjectRef(name=sub.name, path=str(sub.resolve()))
            for sub in sorted(directory.iterdir())
            if sub.is_dir() and not sub.name.startswith(".") and any((sub / m).is_file() for m in MANIFESTS)
        ]
        return cls(name=directory.name, projects=projects)


class ServiceLink(BaseModel):
    kind: str  # http | event | table
    source: str  # project name (caller / producer / owner)
    target: str  # project name (callee / consumer / co-owner)
    label: str  # endpoint, topic or table
    evidence_file: str | None = None
    evidence_line: int | None = None
    detail: str | None = None


class WorkspaceAnalysis(BaseModel):
    name: str
    projects: list[dict] = Field(default_factory=list)  # summaries
    links: list[ServiceLink] = Field(default_factory=list)
    topics: dict[str, dict[str, list[str]]] = Field(default_factory=dict)  # topic -> {"producers": [...], "consumers": [...]}
    unresolved_calls: list[dict] = Field(default_factory=list)  # external URLs/paths that matched no project


def _norm_path(path: str) -> str:
    path = re.sub(r"https?://[^/]+", "", path)
    path = re.sub(r"\$\{[^}]*\}|\{[^}]*\}|:[A-Za-z_]\w*", "*", path)
    return re.sub(r"/+", "/", path).rstrip("/") or "/"


def _path_matches(call: str, endpoint: str) -> bool:
    c, e = _norm_path(call).strip("/").split("/"), _norm_path(endpoint).strip("/").split("/")
    if not e or e == [""] or len(c) < len(e):
        return False
    # the literal may carry a prefix (/api/v1); align on the endpoint's length from the end
    tail = c[-len(e) :]
    pairs = list(zip(tail, e))
    # at least one concrete (non-wildcard) segment must agree, so "/{id}" alone never matches anything
    return all(a == b or a == "*" or b == "*" for a, b in pairs) and any(a == b and a != "*" for a, b in pairs)


class WorkspaceAnalyzer:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.analyses: dict[str, ProjectAnalysis] = {}
        self.files: dict[str, list] = {}

    def run(self, config: WorkspaceConfig) -> WorkspaceAnalysis:
        reader = ProjectReader(self.settings)
        for ref in config.projects:
            self.analyses[ref.name] = ProjectAnalyzer(self.settings).run(ref.path)
            self.files[ref.name] = [
                f.model_copy(update={"content": without_comments(f.content, f.language, f.extension)})
                for f in reader.read(ref.path)
                if f.language is not None or f.extension in (".yml", ".yaml", ".properties", ".json")
            ]
        ws = WorkspaceAnalysis(name=config.name)
        for ref in config.projects:
            a = self.analyses[ref.name]
            ws.projects.append(
                {
                    "name": ref.name,
                    "path": ref.path,
                    "languages": [lang.language.value for lang in a.languages],
                    "frameworks": [f.name for f in a.frameworks],
                    "endpoints": len(a.endpoints),
                    "tables": [t.name for t in a.db_schema.tables],
                    "loc": a.metrics.loc,
                }
            )
        ws.links += self._http_links(config, ws)
        ws.links += self._event_links(config, ws)
        ws.links += self._table_links(config)
        return ws

    # ------------------------------------------------------------ http
    def _http_links(self, config: WorkspaceConfig, ws: WorkspaceAnalysis) -> list[ServiceLink]:
        links: list[ServiceLink] = []
        endpoints_by_project = {ref.name: [(f"{e.http_method} {e.path}", e.path) for e in self.analyses[ref.name].endpoints] for ref in config.projects}
        names = {ref.name.lower(): ref.name for ref in config.projects}
        for ref in config.projects:
            own_paths = {_norm_path(p) for _, p in endpoints_by_project[ref.name]}
            seen: set[tuple[str, str]] = set()
            for f in self.files[ref.name]:
                content = f.content
                for m in FEIGN_RE.finditer(content):
                    for nm in NAME_KW_RE.finditer(m.group(1)):
                        target = self._project_for_name(nm.group(1), names)
                        if target and target != ref.name and (target, "feign") not in seen:
                            seen.add((target, "feign"))
                            links.append(
                                ServiceLink(
                                    kind="http",
                                    source=ref.name,
                                    target=target,
                                    label="@FeignClient",
                                    evidence_file=f.rel_path,
                                    evidence_line=content.count("\n", 0, m.start()) + 1,
                                    detail=nm.group(1),
                                )
                            )
                for m in PATH_LITERAL_RE.finditer(content):
                    literal = m.group(1)
                    norm = _norm_path(literal)
                    if norm.count("/") < 1 or literal.endswith((".js", ".css", ".png", ".html", ".svg", ".ts", ".java", ".py")):
                        continue
                    host_target = None
                    if literal.startswith("http"):
                        host_target = self._project_for_name(literal, names)
                        if host_target is None:
                            ws.unresolved_calls.append({"project": ref.name, "url": literal, "file": f.rel_path})
                            continue
                    elif norm in own_paths:
                        continue
                    matched = False
                    for other in config.projects:
                        if other.name == ref.name or (host_target and other.name != host_target):
                            continue
                        for label, path in endpoints_by_project[other.name]:
                            if _path_matches(literal, path):
                                key = (other.name, label)
                                if key not in seen:
                                    seen.add(key)
                                    links.append(
                                        ServiceLink(
                                            kind="http",
                                            source=ref.name,
                                            target=other.name,
                                            label=label,
                                            evidence_file=f.rel_path,
                                            evidence_line=content.count("\n", 0, m.start()) + 1,
                                            detail=literal,
                                        )
                                    )
                                matched = True
                    if not matched and literal.startswith("http") and host_target is None:
                        ws.unresolved_calls.append({"project": ref.name, "url": literal, "file": f.rel_path})
        return links

    @staticmethod
    def _project_for_name(name: str, names: dict[str, str]) -> str | None:
        try:
            key = (urlsplit(name if "://" in name else "//" + name).hostname or "").lower()
        except ValueError:
            return None
        if key in names:
            return names[key]

        def alias(value: str) -> str:
            return value.replace("_", "-").removesuffix("-service")

        matches = {real for low, real in names.items() if alias(low) == alias(key)}
        return next(iter(matches)) if len(matches) == 1 else None

    # ------------------------------------------------------------ events
    def _event_links(self, config: WorkspaceConfig, ws: WorkspaceAnalysis) -> list[ServiceLink]:
        producers: dict[str, list[tuple[str, str, int]]] = {}
        consumers: dict[str, list[tuple[str, str, int]]] = {}
        for ref in config.projects:
            for f in self.files[ref.name]:
                for rx in PRODUCER_RES:
                    for m in rx.finditer(f.content):
                        topic = m.group(1)
                        if topic.lower() not in GENERIC_EVENT_WORDS:
                            producers.setdefault(topic, []).append((ref.name, f.rel_path, f.content.count("\n", 0, m.start()) + 1))
                for rx in CONSUMER_RES:
                    for m in rx.finditer(f.content):
                        topic = m.group(1)
                        if topic.lower() not in GENERIC_EVENT_WORDS:
                            consumers.setdefault(topic, []).append((ref.name, f.rel_path, f.content.count("\n", 0, m.start()) + 1))
        links: list[ServiceLink] = []
        for topic in sorted(set(producers) | set(consumers)):
            prods = producers.get(topic, [])
            cons = consumers.get(topic, [])
            ws.topics[topic] = {"producers": sorted({p[0] for p in prods}), "consumers": sorted({c[0] for c in cons})}
            for p_proj, p_file, p_line in prods:
                for c_proj, _, _ in cons:
                    if c_proj != p_proj and not any(
                        link.kind == "event" and link.source == p_proj and link.target == c_proj and link.label == topic for link in links
                    ):
                        links.append(ServiceLink(kind="event", source=p_proj, target=c_proj, label=topic, evidence_file=p_file, evidence_line=p_line))
        return links

    # ------------------------------------------------------------ shared tables
    def _table_links(self, config: WorkspaceConfig) -> list[ServiceLink]:
        owners: dict[str, list[str]] = {}
        for ref in config.projects:
            for t in self.analyses[ref.name].db_schema.tables:
                owners.setdefault(t.name, []).append(ref.name)
        links = []
        for table, projects in sorted(owners.items()):
            uniq = sorted(set(projects))
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    links.append(ServiceLink(kind="table", source=uniq[i], target=uniq[j], label=table, detail="shared database table"))
        return links


def workspace_mermaid(ws: WorkspaceAnalysis) -> str:
    def mid(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", name)

    lines = ["```mermaid", "flowchart LR"]
    for p in ws.projects:
        langs = "/".join(p["languages"]) or "?"
        lines.append(f'    {mid(p["name"])}["{p["name"]}<br/><small>{langs} · {p["endpoints"]} endpoints</small>"]')
    for topic, parts in ws.topics.items():
        if parts["producers"] and parts["consumers"]:
            lines.append(f'    topic_{mid(topic)}(["{topic}"]):::topic')
    seen: set[tuple] = set()
    for link in ws.links:
        if link.kind == "http":
            key = (link.source, link.target, "http")
            if key not in seen:
                seen.add(key)
                n = sum(1 for x in ws.links if x.kind == "http" and x.source == link.source and x.target == link.target)
                lines.append(f"    {mid(link.source)} -->|HTTP ×{n}| {mid(link.target)}")
        elif link.kind == "table":
            key = (link.source, link.target, link.label)
            if key not in seen:
                seen.add(key)
                lines.append(f"    {mid(link.source)} <-.->|table {link.label}| {mid(link.target)}")
    for topic, parts in ws.topics.items():
        if parts["producers"] and parts["consumers"]:
            for p in parts["producers"]:
                lines.append(f"    {mid(p)} -.-> topic_{mid(topic)}")
            for c in parts["consumers"]:
                lines.append(f"    topic_{mid(topic)} -.-> {mid(c)}")
    lines += ["    classDef topic fill:#e0a830,color:#fff", "```"]
    return "\n".join(lines)


def workspace_markdown(ws: WorkspaceAnalysis) -> str:
    lines = [f"# Workspace: {ws.name}", "", "| Project | Languages | Frameworks | Endpoints | Tables | LOC |", "|---|---|---|---|---|---|"]
    for p in ws.projects:
        lines.append(
            f"| **{p['name']}** | {', '.join(p['languages'])} | {', '.join(p['frameworks'][:5])} | {p['endpoints']} | {len(p['tables'])} | {p['loc']} |"
        )
    lines += ["", "## Service map", "", workspace_mermaid(ws), ""]
    http = [link for link in ws.links if link.kind == "http"]
    if http:
        lines += ["## HTTP dependencies", "", "| Caller | Callee | Endpoint | Evidence |", "|---|---|---|---|"]
        lines += [f"| {x.source} | {x.target} | `{x.label}` | `{x.evidence_file}:{x.evidence_line}` {('— ' + x.detail) if x.detail else ''} |" for x in http]
        lines.append("")
    if ws.topics:
        lines += ["## Events", "", "| Topic / queue | Producers | Consumers |", "|---|---|---|"]
        lines += [f"| `{t}` | {', '.join(v['producers']) or '—'} | {', '.join(v['consumers']) or '—'} |" for t, v in ws.topics.items()]
        lines.append("")
    tables = [link for link in ws.links if link.kind == "table"]
    if tables:
        lines += ["## Shared tables (database coupling)", ""] + [f"- `{x.label}`: {x.source} ↔ {x.target}" for x in tables] + [""]
    if ws.unresolved_calls:
        lines += ["## External calls (no project matched)", ""] + [f"- {u['project']}: `{u['url']}` ({u['file']})" for u in ws.unresolved_calls[:30]] + [""]
    return "\n".join(lines)

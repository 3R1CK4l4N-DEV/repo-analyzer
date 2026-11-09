import { artifactUrl, loadArtifact, loadProjects } from "../api";
import { createGraph } from "../components/graph";
import { createDataTable } from "../components/table";
import { createTabs, type TabItem } from "../components/tabs";
import { formatBytes, formatDate, formatNumber, shortCommit } from "../lib/format";
import type { ProjectTab } from "../router";
import type {
  AnalysisDiff,
  BuildInfo,
  BusinessRule,
  DatabaseTable,
  Dependency,
  Endpoint,
  GraphData,
  KnowledgeData,
  Manifest,
  ProjectAnalysis,
  Technology,
} from "../types";

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function allEndpoints(analysis: ProjectAnalysis): Endpoint[] {
  return analysis.languages.flatMap((language) => language.endpoints);
}

function allDependencies(analysis: ProjectAnalysis): Dependency[] {
  return analysis.builds.flatMap((build) => build.dependencies);
}

function allFrameworks(analysis: ProjectAnalysis): Technology[] {
  const frameworks = new Map<string, Technology>();
  for (const framework of analysis.builds.flatMap((build) => build.frameworks)) {
    if (!frameworks.has(framework.name)) frameworks.set(framework.name, framework);
  }
  return [...frameworks.values()];
}

function artifactButtons(manifest: Manifest): HTMLElement {
  const group = element("div", "download-list");
  const labels: Array<[keyof Manifest["artifacts"], string]> = [
    ["vault_zip", "Vault ZIP"],
    ["report_html", "Reporte HTML"],
    ["report_md", "Reporte Markdown"],
    ["graphml", "GraphML"],
    ["graph", "Grafo JSON"],
    ["analysis", "Análisis JSON"],
    ["knowledge", "Conocimiento JSON"],
    ["changes_md", "Cambios Markdown"],
    ["kb", "KB SQLite"],
  ];
  for (const [key, label] of labels) {
    const filename = manifest.artifacts[key];
    if (!filename) continue;
    const link = element("a", "download-link");
    link.href = artifactUrl(manifest, filename);
    link.download = "";
    link.textContent = `${label} · ${formatBytes(manifest.sizes[filename] ?? 0)}`;
    group.append(link);
  }
  return group;
}

function repoDescription(manifest: Manifest): HTMLElement {
  const line = element("p", "project-source");
  if (!manifest.source.repo_url) {
    line.textContent = manifest.slug;
    return line;
  }
  const link = element("a");
  link.href = manifest.source.repo_url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = manifest.source.repo_url.replace("https://github.com/", "");
  line.append(link);
  if (manifest.source.ref) line.append(document.createTextNode(` @${manifest.source.ref}`));
  if (manifest.source.commit) line.append(document.createTextNode(` · ${shortCommit(manifest.source.commit)}`));
  return line;
}

function summaryCards(manifest: Manifest): HTMLElement {
  const cards = element("div", "metric-grid");
  const values: Array<[string, string]> = [
    ["Archivos", formatNumber(manifest.summary.files)],
    ["LOC", formatNumber(manifest.summary.loc)],
    ["Tipos", formatNumber(manifest.summary.units)],
    ["Endpoints", formatNumber(manifest.summary.endpoints)],
    ["Tablas", formatNumber(manifest.summary.tables)],
    ["Dependencias", formatNumber(manifest.summary.dependencies)],
    ["Reglas", formatNumber(manifest.summary.rules)],
    ["Nodos / aristas", `${formatNumber(manifest.summary.graph.nodes)} / ${formatNumber(manifest.summary.graph.edges)}`],
  ];
  for (const [label, value] of values) {
    const card = element("div", "metric-card");
    card.append(element("strong", undefined, value), element("span", undefined, label));
    cards.append(card);
  }
  return cards;
}

function keyValueTable(values: Record<string, string | number>, firstLabel: string): HTMLElement {
  return createDataTable(
    Object.entries(values),
    [
      { label: firstLabel, value: ([key]) => key },
      { label: "Valor", value: ([, value]) => String(value) },
    ],
    { placeholder: `Filtrar ${firstLabel.toLocaleLowerCase("es")}…` },
  );
}

function renderSummary(manifest: Manifest, analysis: ProjectAnalysis): HTMLElement {
  const content = element("div", "tab-content");
  content.append(summaryCards(manifest));
  const columns = element("div", "summary-grid");
  const languages = element("section", "panel");
  languages.append(element("h2", undefined, "Lenguajes"));
  languages.append(
    createDataTable(manifest.summary.languages, [
      { label: "Lenguaje", value: (item) => item.language },
      { label: "Confianza", value: (item) => `${Math.round(item.confidence * 100)} %` },
      { label: "Archivos", value: (item) => String(item.files) },
      { label: "LOC", value: (item) => formatNumber(item.loc) },
    ]),
  );
  const builds = element("section", "panel");
  builds.append(element("h2", undefined, "Sistemas de build"));
  builds.append(
    createDataTable<BuildInfo>(analysis.builds, [
      { label: "Sistema", value: (build) => build.system },
      { label: "Manifiesto", value: (build) => build.manifest_path },
      { label: "Versiones", value: (build) => Object.entries(build.versions).map(([key, value]) => `${key}=${value}`).join(", ") || "—" },
    ]),
  );
  const frameworks = element("section", "panel");
  frameworks.append(element("h2", undefined, "Frameworks"));
  frameworks.append(
    createDataTable(allFrameworks(analysis), [
      { label: "Nombre", value: (framework) => framework.name },
      { label: "Versión", value: (framework) => framework.version ?? "managed" },
    ]),
  );
  const versions = element("section", "panel");
  versions.append(element("h2", undefined, "Versiones"));
  versions.append(keyValueTable(manifest.summary.versions, "Tecnología"));
  const stereotypes = element("section", "panel");
  stereotypes.append(element("h2", undefined, "Estereotipos"));
  stereotypes.append(keyValueTable(manifest.summary.stereotypes, "Estereotipo"));
  columns.append(languages, builds, frameworks, versions, stereotypes);
  content.append(columns);
  return content;
}

function renderEndpoints(analysis: ProjectAnalysis): HTMLElement {
  const methods = [...new Set(allEndpoints(analysis).map((endpoint) => endpoint.http_method))].sort();
  const method = document.createElement("select");
  method.setAttribute("aria-label", "Filtrar por método");
  method.append(new Option("Todos los métodos", ""));
  for (const value of methods) method.append(new Option(value, value));
  return createDataTable<Endpoint>(
    allEndpoints(analysis),
    [
      { label: "Método", value: (endpoint) => endpoint.http_method },
      { label: "Ruta", value: (endpoint) => endpoint.path },
      { label: "Handler", value: (endpoint) => endpoint.handler },
      { label: "Ubicación", value: (endpoint) => `${endpoint.file}:${endpoint.line}` },
    ],
    {
      placeholder: "Filtrar endpoints…",
      controls: [method],
      filter: (endpoint, query) => {
        const matchesMethod = !method.value || endpoint.http_method === method.value;
        const text = `${endpoint.http_method} ${endpoint.path} ${endpoint.handler} ${endpoint.file}`.toLocaleLowerCase("es");
        return matchesMethod && (!query || text.includes(query));
      },
    },
  );
}

function renderDependencies(analysis: ProjectAnalysis): HTMLElement {
  return createDataTable<Dependency>(
    allDependencies(analysis),
    [
      { label: "Nombre", value: (dependency) => dependency.name },
      { label: "Versión", value: (dependency) => dependency.version ?? "managed" },
      { label: "Scope", value: (dependency) => dependency.scope },
      { label: "Manifiesto", value: (dependency) => dependency.source_file ?? "—" },
    ],
    {
      placeholder: "Filtrar dependencias…",
      filter: (dependency, query) =>
        !query || `${dependency.name} ${dependency.version ?? ""} ${dependency.scope} ${dependency.source_file ?? ""}`.toLocaleLowerCase("es").includes(query),
    },
  );
}

function tableCard(table: DatabaseTable): HTMLElement {
  const card = element("article", "panel data-card");
  card.append(element("h2", undefined, table.name));
  const metadata = element("p", "muted");
  const foreignKeys = table.foreign_keys.map((key) => `${key.column} → ${key.ref_table}`).join(", ") || "Sin claves foráneas";
  const entities = table.mapped_entities.map((entity) => entity.split(".").at(-1)).join(", ") || "Sin entidades mapeadas";
  metadata.textContent = `${table.columns.length} columnas · ${foreignKeys} · ${entities} · ${table.source_file}:${table.line}`;
  card.append(metadata);
  const details = element("details");
  details.append(element("summary", undefined, "Ver columnas"));
  details.append(
    createDataTable(table.columns, [
      { label: "Columna", value: (column) => column.name },
      { label: "Tipo", value: (column) => column.type ?? "—" },
      { label: "Nullable", value: (column) => (column.nullable ? "Sí" : "No") },
      { label: "PK", value: (column) => (column.primary_key ? "Sí" : "No") },
    ]),
  );
  card.append(details);
  return card;
}

function renderData(analysis: ProjectAnalysis): HTMLElement {
  const content = element("div", "data-grid");
  for (const table of analysis.db_schema.tables) content.append(tableCard(table));
  return content;
}

function statusBadge(rule: BusinessRule): HTMLElement {
  return element("span", `badge ${rule.status}`, rule.status);
}

function renderRules(knowledge: KnowledgeData): HTMLElement {
  const content = element("div", "tab-content");
  content.append(
    createDataTable<BusinessRule>(
      knowledge.rules,
      [
        { label: "ID", value: (rule) => rule.id },
        { label: "Estado", value: statusBadge, search: (rule) => rule.status },
        { label: "Categoría", value: (rule) => rule.category },
        { label: "Confianza", value: (rule) => `${Math.round(rule.confidence * 100)} %` },
        { label: "Enunciado", value: (rule) => rule.statement },
        {
          label: "Evidencia",
          value: (rule) => {
            const evidence = rule.evidence[0];
            return evidence ? `${evidence.file}:${evidence.line_start}-${evidence.line_end}` : "—";
          },
        },
        { label: "Términos", value: (rule) => rule.domain_terms.join(", ") || "—" },
      ],
      {
        placeholder: "Filtrar reglas…",
        filter: (rule, query) =>
          !query ||
          `${rule.id} ${rule.status} ${rule.category} ${rule.statement} ${rule.domain_terms.join(" ")}`.toLocaleLowerCase("es").includes(query),
      },
    ),
  );
  if (knowledge.glossary.length) {
    content.append(element("h2", undefined, "Glosario"));
    content.append(
      createDataTable(knowledge.glossary, [
        { label: "Término", value: (term) => term.term },
        { label: "Definición", value: (term) => term.definition },
        { label: "Alias", value: (term) => term.aliases.join(", ") || "—" },
      ]),
    );
  }
  return content;
}

function changeSection(title: string, added: string[], removed: string[], changed: string[] = []): HTMLElement | null {
  if (!added.length && !removed.length && !changed.length) return null;
  const section = element("section", "panel change-section");
  section.append(element("h2", undefined, title));
  const append = (label: string, values: string[], className: string) => {
    if (!values.length) return;
    section.append(element("h3", className, `${label} (${values.length})`));
    const list = element("ul");
    for (const value of values) list.append(element("li", undefined, value));
    section.append(list);
  };
  append("Añadidos", added, "added");
  append("Eliminados", removed, "removed");
  append("Cambiados", changed, "changed");
  return section;
}

function renderChanges(diff: AnalysisDiff): HTMLElement {
  const content = element("div", "tab-content");
  const summary = element("div", "metric-grid");
  for (const [label, value] of [
    ["LOC", `${diff.loc_before} → ${diff.loc_after}`],
    ["Relaciones añadidas", `+${diff.relations_added}`],
    ["Relaciones eliminadas", `-${diff.relations_removed}`],
  ]) {
    const card = element("div", "metric-card");
    card.append(element("strong", undefined, value), element("span", undefined, label));
    summary.append(card);
  }
  content.append(summary);
  const sections = [
    changeSection("Tipos", diff.units_added, diff.units_removed),
    changeSection("Métodos", diff.methods_added, diff.methods_removed),
    changeSection("Endpoints", diff.endpoints_added, diff.endpoints_removed),
    changeSection("Dependencias", diff.dependencies_added, diff.dependencies_removed, diff.dependencies_changed),
    changeSection("Versiones", [], [], diff.versions_changed),
  ].filter((section): section is HTMLElement => section !== null);
  if (!sections.length && !diff.relations_added && !diff.relations_removed && diff.loc_before === diff.loc_after) {
    content.append(element("div", "empty-state", "Sin cambios estructurales respecto a la publicación anterior"));
  } else {
    content.append(...sections);
  }
  return content;
}

async function loadTab(manifest: Manifest, tab: ProjectTab): Promise<HTMLElement> {
  if (tab === "arquitectura") {
    const graph = await loadArtifact<GraphData>(manifest, manifest.artifacts.graph);
    return createGraph(graph);
  }
  if (tab === "reglas" && manifest.artifacts.knowledge) {
    const knowledge = await loadArtifact<KnowledgeData>(manifest, manifest.artifacts.knowledge);
    return renderRules(knowledge);
  }
  if (tab === "reporte") {
    const content = element("div", "report-view");
    const open = element("a", "button secondary", "Abrir reporte en otra pestaña");
    open.href = artifactUrl(manifest, manifest.artifacts.report_html);
    open.target = "_blank";
    open.rel = "noreferrer";
    const frame = document.createElement("iframe");
    frame.src = open.href;
    frame.loading = "lazy";
    frame.title = `Reporte de ${manifest.name}`;
    content.append(open, frame);
    return content;
  }
  if (tab === "cambios" && manifest.artifacts.changes_json) {
    return renderChanges(await loadArtifact<AnalysisDiff>(manifest, manifest.artifacts.changes_json));
  }
  const analysis = await loadArtifact<ProjectAnalysis>(manifest, manifest.artifacts.analysis);
  if (tab === "endpoints") return renderEndpoints(analysis);
  if (tab === "dependencias") return renderDependencies(analysis);
  if (tab === "datos") return renderData(analysis);
  return renderSummary(manifest, analysis);
}

function availableTabs(manifest: Manifest): TabItem[] {
  const tabs: TabItem[] = [
    { id: "resumen", label: "Resumen" },
    { id: "arquitectura", label: "Arquitectura" },
    { id: "endpoints", label: "Endpoints" },
    { id: "dependencias", label: "Dependencias" },
  ];
  if (manifest.summary.tables) tabs.push({ id: "datos", label: "Datos" });
  if (manifest.artifacts.knowledge) tabs.push({ id: "reglas", label: "Reglas" });
  tabs.push({ id: "reporte", label: "Reporte" });
  if (manifest.artifacts.changes_json) tabs.push({ id: "cambios", label: "Cambios" });
  return tabs;
}

export async function renderProject(container: HTMLElement, slug: string, requestedTab: ProjectTab): Promise<void> {
  container.replaceChildren(element("div", "loading", "Cargando análisis…"));
  try {
    const index = await loadProjects();
    const manifest = index.projects.find((project) => project.slug === slug);
    if (!manifest) throw new Error(`No existe el proyecto «${slug}»`);
    const tabs = availableTabs(manifest);
    const tab = tabs.some((item) => item.id === requestedTab) ? requestedTab : "resumen";
    const header = element("header", "site-header project-header");
    const headerContent = element("div", "header-content");
    const back = element("a", "back-link", "Volver a proyectos");
    back.href = "#/";
    headerContent.append(
      back,
      element("h1", undefined, manifest.name),
      repoDescription(manifest),
      element("p", "header-meta", `Generado ${formatDate(manifest.generated_at)} · DevAnalyzer ${manifest.devanalyzer_version}`),
      artifactButtons(manifest),
    );
    header.append(headerContent);
    const main = element("main", "page project-page");
    main.append(createTabs(manifest.slug, tab, tabs));
    const panel = element("section", "tab-panel");
    panel.append(element("div", "loading", "Cargando sección…"));
    main.append(panel);
    container.replaceChildren(header, main);
    try {
      panel.replaceChildren(await loadTab(manifest, tab));
    } catch (error) {
      panel.replaceChildren(element("div", "error-state", `No se pudo cargar la sección: ${error instanceof Error ? error.message : String(error)}`));
    }
  } catch (error) {
    container.replaceChildren(element("div", "error-state", error instanceof Error ? error.message : String(error)));
  }
}

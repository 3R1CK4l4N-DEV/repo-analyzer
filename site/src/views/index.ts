import { artifactUrl, loadProjects, loadWorkspace } from "../api";
import { createDataTable } from "../components/table";
import { formatDate, formatNumber, shortCommit } from "../lib/format";
import { projectHref } from "../router";
import type { Manifest, ServiceLink, WorkspaceData } from "../types";

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function chip(text: string): HTMLElement {
  return element("span", "chip", text);
}

function projectCard(project: Manifest): HTMLElement {
  const card = element("article", "project-card");
  const title = element("h2", undefined, project.name);
  const slug = element("p", "muted", project.slug);
  card.append(title, slug);
  if (project.source.repo_url) {
    const repository = element("a", "repo-link");
    repository.href = project.source.repo_url;
    repository.target = "_blank";
    repository.rel = "noreferrer";
    const ref = project.source.ref ? `@${project.source.ref}` : "";
    const commit = project.source.commit ? ` · ${shortCommit(project.source.commit)}` : "";
    repository.textContent = `${project.source.repo_url.replace("https://github.com/", "")}${ref}${commit}`;
    card.append(repository);
  }

  const languages = element("div", "chip-list");
  for (const language of project.summary.languages) languages.append(chip(`${language.language} · ${formatNumber(language.loc)} LOC`));
  const technology = element("div", "chip-list");
  for (const system of project.summary.build_systems) technology.append(chip(system));
  for (const framework of project.summary.frameworks.slice(0, 4)) technology.append(chip(framework.name));
  if (project.summary.frameworks.length > 4) technology.append(chip(`+${project.summary.frameworks.length - 4}`));
  card.append(languages, technology);

  const counters = element("dl", "counter-grid");
  const values: Array<[string, number]> = [
    ["Archivos", project.summary.files],
    ["LOC", project.summary.loc],
    ["Tipos", project.summary.units],
    ["Endpoints", project.summary.endpoints],
    ["Tablas", project.summary.tables],
    ["Deps", project.summary.dependencies],
    ["Reglas", project.summary.rules],
  ];
  for (const [label, value] of values) {
    const item = element("div");
    item.append(element("dt", undefined, label), element("dd", undefined, formatNumber(value)));
    counters.append(item);
  }
  card.append(counters);

  const actions = element("div", "actions");
  const vault = element("a", "button secondary", "Descargar vault");
  vault.href = artifactUrl(project, project.artifacts.vault_zip);
  vault.download = "";
  const open = element("a", "button", "Ver análisis");
  open.href = projectHref(project.slug);
  actions.append(vault, open);
  card.append(actions);
  return card;
}

function workspaceSection(workspace: WorkspaceData): HTMLElement {
  const section = element("section", "section");
  section.append(element("h2", undefined, "Servicios relacionados"));
  section.append(
    createDataTable<ServiceLink>(
      workspace.links,
      [
        { label: "Tipo", value: (link) => link.kind },
        { label: "Relación", value: (link) => `${link.source} → ${link.target}` },
        { label: "Etiqueta", value: (link) => link.label },
        {
          label: "Evidencia",
          value: (link) => (link.evidence_file ? `${link.evidence_file}:${link.evidence_line ?? ""}` : "—"),
        },
      ],
      {
        placeholder: "Filtrar servicios…",
        filter: (link, query) => !query || Object.values(link).some((value) => String(value ?? "").toLocaleLowerCase("es").includes(query)),
      },
    ),
  );
  const topics = Object.entries(workspace.topics);
  if (topics.length) {
    section.append(element("h3", undefined, "Topics"));
    const list = element("ul", "topic-list");
    for (const [name, participants] of topics) {
      list.append(
        element(
          "li",
          undefined,
          `${name}: productores ${participants.producers.join(", ") || "—"} · consumidores ${participants.consumers.join(", ") || "—"}`,
        ),
      );
    }
    section.append(list);
  }
  return section;
}

export async function renderIndex(container: HTMLElement): Promise<void> {
  container.replaceChildren(element("div", "loading", "Cargando proyectos…"));
  try {
    const [index, workspace] = await Promise.all([loadProjects(), loadWorkspace()]);
    const header = element("header", "site-header");
    const headerContent = element("div", "header-content");
    headerContent.append(
      element("h1", undefined, "DevAnalyzer"),
      element("p", undefined, "Análisis estático publicado automáticamente con GitHub Actions."),
      element("p", "header-meta", `Actualizado ${formatDate(index.generated_at)}`),
    );
    header.append(headerContent);
    const main = element("main", "page");
    if (!index.projects.length) {
      main.append(element("div", "empty-state", "No hay proyectos publicados. Genera los datos con npm run data."));
    } else {
      const grid = element("section", "project-grid");
      for (const project of index.projects) grid.append(projectCard(project));
      main.append(grid);
    }
    if (workspace?.links.length) main.append(workspaceSection(workspace));
    container.replaceChildren(header, main);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const empty = element("main", "page");
    empty.append(
      element("div", "empty-state", `No se pudo cargar projects.json: ${message}`),
      element("code", undefined, "npm run data"),
    );
    container.replaceChildren(empty);
  }
}

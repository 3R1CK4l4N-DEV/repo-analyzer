import cytoscape, { type Core, type ElementDefinition, type LayoutOptions, type NodeSingular, type StylesheetStyle } from "cytoscape";

import { filterGraph } from "../lib/graph-filter";
import type { GraphData, GraphEdge, GraphNode } from "../types";

const defaultNodeKinds = new Set(["class", "interface", "enum", "record", "module", "function", "endpoint"]);
const defaultEdgeKinds = new Set(["depends_on", "extends", "implements", "calls", "exposes", "imports"]);
const layouts = ["cose", "breadthfirst", "grid", "concentric"] as const;

function checkboxGroup(title: string, values: string[], selected: Set<string>, onChange: () => void): HTMLElement {
  const fieldset = document.createElement("fieldset");
  const legend = document.createElement("legend");
  legend.textContent = title;
  fieldset.append(legend);
  for (const value of values) {
    const label = document.createElement("label");
    label.className = "check-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = value;
    input.checked = selected.has(value);
    input.addEventListener("change", () => {
      if (input.checked) selected.add(value);
      else selected.delete(value);
      onChange();
    });
    label.append(input, document.createTextNode(value));
    fieldset.append(label);
  }
  return fieldset;
}

function describeNode(node: GraphNode, graph: GraphData): HTMLElement {
  const panel = document.createElement("div");
  panel.className = "graph-details";
  const title = document.createElement("h3");
  title.textContent = String(node.name ?? node.fqn ?? node.id);
  panel.append(title);
  const attributes = document.createElement("dl");
  for (const [key, value] of Object.entries(node)) {
    if (value === null || value === undefined || typeof value === "object") continue;
    const term = document.createElement("dt");
    term.textContent = key;
    const description = document.createElement("dd");
    description.textContent = String(value);
    attributes.append(term, description);
  }
  panel.append(attributes);
  const relations = document.createElement("div");
  const incoming = graph.edges.filter((edge) => edge.target === node.id);
  const outgoing = graph.edges.filter((edge) => edge.source === node.id);
  const relationList = (heading: string, edges: GraphEdge[], endpoint: "source" | "target") => {
    const subtitle = document.createElement("h4");
    subtitle.textContent = `${heading} (${edges.length})`;
    const list = document.createElement("ul");
    for (const edge of edges.slice(0, 50)) {
      const item = document.createElement("li");
      item.textContent = `${edge.key}: ${edge[endpoint]}`;
      list.append(item);
    }
    relations.append(subtitle, list);
  };
  relationList("Entrantes", incoming, "source");
  relationList("Salientes", outgoing, "target");
  panel.append(relations);
  return panel;
}

export function createGraph(graph: GraphData): HTMLElement {
  const root = document.createElement("div");
  root.className = "graph-component";
  const controls = document.createElement("aside");
  controls.className = "graph-controls";
  const canvasArea = document.createElement("div");
  canvasArea.className = "graph-main";
  const canvas = document.createElement("div");
  canvas.className = "graph-canvas";
  const details = document.createElement("aside");
  details.className = "graph-details";
  details.innerHTML = "<h3>Detalle</h3><p>Selecciona un nodo para ver sus atributos y relaciones.</p>";
  canvasArea.append(canvas, details);
  root.append(controls, canvasArea);

  const nodeKinds = new Set(graph.nodes.map((node) => node.kind).filter((kind) => defaultNodeKinds.has(kind)));
  const edgeKinds = new Set(graph.edges.map((edge) => edge.key).filter((kind) => defaultEdgeKinds.has(kind)));
  const allNodeKinds = [...new Set(graph.nodes.map((node) => node.kind))].sort();
  const allEdgeKinds = [...new Set(graph.edges.map((edge) => edge.key))].sort();

  const search = document.createElement("input");
  search.type = "search";
  search.placeholder = "Buscar nombre o FQN";
  search.setAttribute("aria-label", search.placeholder);
  const layoutLabel = document.createElement("label");
  layoutLabel.textContent = "Distribución";
  const layoutSelect = document.createElement("select");
  for (const name of layouts) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    layoutSelect.append(option);
  }
  layoutLabel.append(layoutSelect);
  const notice = document.createElement("p");
  notice.className = "graph-notice";
  controls.append(search, layoutLabel);

  let cy: Core | null = null;
  const styles: StylesheetStyle[] = [
    {
      selector: "node",
      style: {
        "background-color": "#9aa0a6",
        color: "#1f2328",
        label: "data(label)",
        "font-size": 10,
        "text-wrap": "ellipsis",
        "text-max-width": "110px",
        "text-valign": "bottom",
        "text-margin-y": 6,
        width: 24,
        height: 24,
      },
    },
    { selector: 'node[stereotype = "controller"]', style: { "background-color": "#4f86c6" } },
    { selector: 'node[stereotype = "service"]', style: { "background-color": "#5bb974" } },
    { selector: 'node[stereotype = "repository"]', style: { "background-color": "#e0a830" } },
    { selector: 'node[stereotype = "entity"]', style: { "background-color": "#b86bd1" } },
    { selector: 'node[kind = "endpoint"]', style: { shape: "round-rectangle", width: 45, height: 22 } },
    {
      selector: "edge",
      style: {
        width: 1.2,
        "line-color": "#9aa0a6",
        "target-arrow-color": "#9aa0a6",
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        opacity: 0.75,
      },
    },
    { selector: ":selected", style: { "border-color": "#d1495b", "border-width": 3, opacity: 1 } },
  ];

  const render = () => {
    cy?.destroy();
    cy = null;
    details.innerHTML = "<h3>Detalle</h3><p>Selecciona un nodo para ver sus atributos y relaciones.</p>";
    const filtered = filterGraph(graph, { nodeKinds, edgeKinds });
    notice.remove();
    if (filtered.nodes.length > 600) {
      canvas.replaceChildren();
      notice.textContent = `Hay ${filtered.nodes.length} nodos visibles. Acota los filtros para mostrar un máximo de 600.`;
      controls.append(notice);
      return;
    }
    const elements: ElementDefinition[] = [
      ...filtered.nodes.map((node) => ({
        data: {
          ...node,
          label: String(node.name ?? node.fqn ?? node.id).split(".").at(-1),
          stereotype: node.stereotype ?? "",
        },
      })),
      ...filtered.edges.map((edge) => ({ data: edge })),
    ];
    cy = cytoscape({
      container: canvas,
      elements,
      style: styles,
      layout: { name: layoutSelect.value } as LayoutOptions,
    });
    cy.on("tap", "node", (event) => {
      const selected = event.target as NodeSingular;
      const node = graph.nodes.find((item) => item.id === selected.id());
      if (node) {
        const description = describeNode(node, graph);
        details.replaceChildren(...description.childNodes);
      }
    });
  };

  controls.append(
    checkboxGroup("Tipos de nodo", allNodeKinds, nodeKinds, render),
    checkboxGroup("Relaciones", allEdgeKinds, edgeKinds, render),
  );
  layoutSelect.addEventListener("change", render);
  search.addEventListener("input", () => {
    if (!cy) return;
    cy.nodes().unselect();
    const query = search.value.trim().toLocaleLowerCase("es");
    if (!query) return;
    const match = cy
      .nodes()
      .filter((node) => [node.data("name"), node.data("fqn"), node.id()].some((value) => String(value ?? "").toLocaleLowerCase("es").includes(query)))
      .first();
    if (match.nonempty()) {
      match.select();
      cy.center(match);
    }
  });
  requestAnimationFrame(render);
  return root;
}

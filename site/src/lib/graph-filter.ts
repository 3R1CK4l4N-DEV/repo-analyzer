import type { GraphData, GraphEdge, GraphNode } from "../types";

export interface GraphFilters {
  nodeKinds: ReadonlySet<string>;
  edgeKinds: ReadonlySet<string>;
}

export function filterGraph(graph: GraphData, filters: GraphFilters): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes = graph.nodes.filter((node) => filters.nodeKinds.has(node.kind));
  const ids = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => filters.edgeKinds.has(edge.key) && ids.has(edge.source) && ids.has(edge.target));
  return { nodes, edges };
}

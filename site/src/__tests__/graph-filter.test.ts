import { describe, expect, it } from "vitest";

import { filterGraph } from "../lib/graph-filter";
import type { GraphData } from "../types";

const graph: GraphData = {
  directed: true,
  multigraph: true,
  graph: {},
  nodes: [
    { id: "a", kind: "class" },
    { id: "b", kind: "method" },
    { id: "c", kind: "endpoint" },
  ],
  edges: [
    { source: "a", target: "b", key: "contains" },
    { source: "a", target: "c", key: "exposes" },
    { source: "b", target: "c", key: "calls" },
  ],
};

describe("filterGraph", () => {
  it("filtra nodos y elimina aristas con extremos ocultos", () => {
    const result = filterGraph(graph, {
      nodeKinds: new Set(["class", "endpoint"]),
      edgeKinds: new Set(["contains", "exposes", "calls"]),
    });
    expect(result.nodes.map((node) => node.id)).toEqual(["a", "c"]);
    expect(result.edges).toEqual([{ source: "a", target: "c", key: "exposes" }]);
  });

  it("respeta los tipos de relación seleccionados", () => {
    const result = filterGraph(graph, {
      nodeKinds: new Set(["class", "method", "endpoint"]),
      edgeKinds: new Set(["calls"]),
    });
    expect(result.edges).toEqual([{ source: "b", target: "c", key: "calls" }]);
  });
});

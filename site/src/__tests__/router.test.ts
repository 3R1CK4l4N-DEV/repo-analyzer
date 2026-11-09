import { describe, expect, it } from "vitest";

import { parseHash } from "../router";

describe("parseHash", () => {
  it("reconoce el índice", () => {
    expect(parseHash("#/")).toEqual({ kind: "index" });
    expect(parseHash("")).toEqual({ kind: "index" });
  });

  it("reconoce un proyecto con la pestaña por defecto", () => {
    expect(parseHash("#/p/x")).toEqual({ kind: "project", slug: "x", tab: "resumen" });
  });

  it("reconoce una pestaña de proyecto", () => {
    expect(parseHash("#/p/x/endpoints")).toEqual({ kind: "project", slug: "x", tab: "endpoints" });
  });

  it("envía hashes desconocidos al índice", () => {
    expect(parseHash("#/otra")).toEqual({ kind: "index" });
    expect(parseHash("#/p/x/desconocida")).toEqual({ kind: "index" });
  });
});

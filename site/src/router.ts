export const projectTabs = ["resumen", "arquitectura", "endpoints", "dependencias", "datos", "reglas", "reporte", "cambios"] as const;
export type ProjectTab = (typeof projectTabs)[number];

export type Route = { kind: "index" } | { kind: "project"; slug: string; tab: ProjectTab };

export function parseHash(hash: string): Route {
  const value = hash.replace(/^#/, "") || "/";
  if (value === "/") return { kind: "index" };
  const match = /^\/p\/([^/]+)(?:\/([^/]+))?$/.exec(value);
  if (!match?.[1]) return { kind: "index" };
  const tab = match[2] ?? "resumen";
  if (!projectTabs.includes(tab as ProjectTab)) return { kind: "index" };
  try {
    return { kind: "project", slug: decodeURIComponent(match[1]), tab: tab as ProjectTab };
  } catch {
    return { kind: "index" };
  }
}

export function projectHref(slug: string, tab: ProjectTab = "resumen"): string {
  const suffix = tab === "resumen" ? "" : `/${tab}`;
  return `#/p/${encodeURIComponent(slug)}${suffix}`;
}

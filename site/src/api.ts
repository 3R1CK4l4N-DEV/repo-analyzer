import type { Manifest, ProjectsIndex, WorkspaceData } from "./types";

const cache = new Map<string, Promise<unknown>>();
const dataRoot = `${import.meta.env.BASE_URL}data/`;

export function dataUrl(path: string): string {
  return `${dataRoot}${path.replace(/^\/+/, "")}`;
}

export function artifactUrl(manifest: Manifest, filename: string): string {
  return dataUrl(`${manifest.base}${filename}`);
}

export function fetchJson<T>(url: string): Promise<T> {
  const cached = cache.get(url);
  if (cached) return cached as Promise<T>;
  const request = fetch(url).then(async (response) => {
    if (!response.ok) throw new Error(`No se pudo cargar ${url} (${response.status})`);
    return (await response.json()) as T;
  });
  cache.set(url, request);
  request.catch(() => cache.delete(url));
  return request;
}

export function loadProjects(): Promise<ProjectsIndex> {
  return fetchJson<ProjectsIndex>(dataUrl("projects.json"));
}

export async function loadWorkspace(): Promise<WorkspaceData | null> {
  try {
    return await fetchJson<WorkspaceData>(dataUrl("workspace.json"));
  } catch {
    return null;
  }
}

export function loadArtifact<T>(manifest: Manifest, filename: string): Promise<T> {
  return fetchJson<T>(artifactUrl(manifest, filename));
}

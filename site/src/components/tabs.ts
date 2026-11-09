import { projectHref, type ProjectTab } from "../router";

export interface TabItem {
  id: ProjectTab;
  label: string;
}

export function createTabs(slug: string, active: ProjectTab, tabs: TabItem[]): HTMLElement {
  const nav = document.createElement("nav");
  nav.className = "tabs";
  nav.setAttribute("aria-label", "Secciones del análisis");
  for (const tab of tabs) {
    const link = document.createElement("a");
    link.href = projectHref(slug, tab.id);
    link.textContent = tab.label;
    if (tab.id === active) {
      link.className = "active";
      link.setAttribute("aria-current", "page");
    }
    nav.append(link);
  }
  return nav;
}

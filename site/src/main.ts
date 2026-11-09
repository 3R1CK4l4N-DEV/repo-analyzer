import "./styles.css";

import { parseHash } from "./router";
import { renderIndex } from "./views";
import { renderProject } from "./views/project";

const root = document.querySelector<HTMLElement>("#app");
if (!root) throw new Error("No se encontró el contenedor #app");
const app: HTMLElement = root;

async function render(): Promise<void> {
  const route = parseHash(window.location.hash);
  if (route.kind === "index") await renderIndex(app);
  else await renderProject(app, route.slug, route.tab);
}

window.addEventListener("hashchange", () => void render());
void render();

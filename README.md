# repo-analyzer (DevAnalyzer)

Analizador de proyectos multi-lenguaje (`v1.0.0`). Detecta tecnología, versiones, estructura, esquema de base de datos y dependencias; construye un grafo de conocimiento del proyecto; extrae y verifica reglas de negocio con IA; y publica todo en un vault de Obsidian navegable y en un servidor MCP para agentes IA.

- **Manual de uso detallado:** [MANUAL_DE_USO.md](MANUAL_DE_USO.md)
- **Estado y métricas de validación:** [STATUS.md](STATUS.md)
- **Hoja de ruta y diseño:** [MEGAPLAN.md](MEGAPLAN.md)

## Cobertura actual

| Área | Soporte |
|---|---|
| **Lenguajes (AST)** | Java, Kotlin, JavaScript / TypeScript (`.js`, `.jsx`, `.ts`, `.tsx`), Python |
| **Sistemas de build** | Maven (incl. herencia `<parent>`), Gradle (`.gradle` / `.kts`), npm / pnpm / yarn, pip / poetry / uv (`pyproject.toml`, `requirements*.txt` con `-r`) |
| **Frameworks** | Spring Boot, Jakarta EE / JAX-RS, Quarkus, Micronaut, NestJS, Express, Next.js (App & Pages Router), FastAPI, Flask, Django / DRF |
| **Esquema de BD** | Flyway / SQL, Liquibase (XML/YAML), Alembic, Django migrations, TypeORM, Prisma (`schema.prisma`) |
| **Proveedores LLM** | Ollama (local por defecto), OpenAI, Anthropic |
| **Salidas** | Consola (Rich), JSON, GraphML (Gephi/yEd), SQLite (`kb.sqlite`), Vault de Obsidian (Markdown + Mermaid + Canvas + Dataview), Reporte HTML/Markdown, Servidor MCP (stdio / SSE) |

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows (en Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"       # incluye pytest y soporte MCP
cp .env.example .env          # opcional
```

Extras opcionales para proveedores en la nube:

```bash
pip install -e ".[openai]"
pip install -e ".[anthropic]"
```

## Uso rápido

```bash
# 1. Resumen estático: lenguajes, build, versiones, frameworks, estereotipos, endpoints y tablas
devanalyzer analyze <ruta-proyecto> --show all --json analysis.json

# 2. Grafo de conocimiento (<ruta>/.devanalyzer/graph.json y graph.graphml)
devanalyzer graph build <ruta>
devanalyzer graph stats <ruta>
devanalyzer graph query <ruta> --neighbors AccountService --depth 2
devanalyzer graph query <ruta> --top 10
devanalyzer graph query <ruta> --path AccountController AccountRepository

# 3. Extracción, verificación y consolidación de reglas de negocio con IA
devanalyzer rules extract <ruta> --lang Spanish --verify -w 4
devanalyzer rules consolidate <ruta> --lang Spanish
devanalyzer rules list <ruta> --status inferred
devanalyzer rules review <ruta> BR-xxxxxxxxxx --status confirmed --note "validado con negocio"
devanalyzer rules glossary <ruta>
devanalyzer rules eval <ruta> --gold gold_rules.json

# 4. Índice y búsqueda semántica (embeddings en SQLite; también alimenta RAG en `rules extract`)
devanalyzer index <ruta>
devanalyzer search <ruta> "¿dónde se validan los límites de depósito?"

# 5. Vault de Obsidian (sincroniza ediciones previas hechas en Obsidian hacia SQLite)
devanalyzer vault <ruta>
devanalyzer vault <ruta> --out "C:/MiVault" --folder "Proyectos/mi-app"

# 6. Modo watch incremental (re-analiza al guardar y actualiza grafo y notas afectadas)
devanalyzer watch <ruta>

# 7. Reporte HTML/Markdown y comparación estructural entre corridas
devanalyzer report <ruta>                 # .devanalyzer/report.html
devanalyzer report <ruta> --format md -o informe.md
devanalyzer snapshot <ruta>
devanalyzer diff <ruta> [--update]

# 8. Análisis multi-repositorio (microservicios: HTTP, eventos Kafka/Rabbit/SQS, tablas compartidas)
devanalyzer workspace <directorio-con-repos>

# 9. Servidor MCP para agentes IA (stdio por defecto; --sse para HTTP)
devanalyzer serve <ruta>

# 10. Verificar conectividad con el proveedor LLM
devanalyzer llm-ping

# 11. Crear bundles para publicación estática
devanalyzer publish bundle <ruta-proyecto> --out bundle
devanalyzer publish index site/public/data
devanalyzer publish site site/repos.json --out site/public/data
```

Consulta el [Manual de Uso](MANUAL_DE_USO.md) para ver todas las opciones, flujos recomendados y resolución de problemas.

## Sitio estático (GitHub Pages)

El workflow de Pages publica por proyecto el reporte HTML, el grafo interactivo, el vault descargable, los cambios respecto a la publicación anterior y, cuando `workspace` está activo, los enlaces entre servicios.

El sitio queda disponible en `https://<usuario>.github.io/<repo>/`; para este repositorio, `https://3r1ck4l4n-dev.github.io/repo-analyzer/`. Tras activar **Settings → Pages → Source: GitHub Actions**, la primera publicación ocurre al hacer push a `main` o al ejecutar manualmente el workflow **GitHub Pages**.

La fuente de verdad es `site/repos.json`:

```json
{
  "workspace": false,
  "projects": [
    {
      "repo": "owner/repo",
      "ref": "main",
      "slug": "repo",
      "subdir": null,
      "rules": null,
      "kb": null
    }
  ]
}
```

`repo` acepta `owner/name`, URL de GitHub o ruta local. `ref`, `slug`, `subdir`, `rules` y `kb` son opcionales. El disparo manual de GitHub Actions admite un `repo` y `ref` extra, pero esa entrada solo vive durante esa ejecución; añade el proyecto a `repos.json` para hacerlo persistente.

La extracción LLM se ejecuta localmente: usa `devanalyzer rules extract <ruta>`, copia `.devanalyzer/rules.json` a `site/rules/<slug>.json` y referencia `"rules": "rules/<slug>.json"` en la entrada correspondiente.

Para desarrollar el frontend:

```bash
cd site
npm run data
npm run dev
npm run build
```

## Configurar el servidor MCP en un cliente

```json
{
  "mcpServers": {
    "devanalyzer": {
      "command": "C:/ruta/al/.venv/Scripts/devanalyzer.exe",
      "args": ["serve", "C:/ruta/al/proyecto"]
    }
  }
}
```

Tools expuestas (17): `get_project_summary`, `list_code_units`, `get_code_unit`, `query_graph`, `find_path`, `list_endpoints`, `list_dependencies`, `get_flows`, `get_workspace_links`, `list_tables`, `get_business_rules`, `review_business_rule`, `search_knowledge`, `semantic_search`, `get_glossary`, `read_source`, `refresh_analysis` (más el recurso `devanalyzer://summary`).

## Desarrollo

```bash
pytest -q
ruff check src tests
```

## Arquitectura

```
src/devanalyzer/
├── cli.py                 # CLI Typer
├── config.py              # pydantic-settings (.env, prefijo DEVANALYZER_)
├── core/
│   ├── models.py          # SourceFile, CodeUnit, Endpoint, Relation, DatabaseSchema, BusinessRule…
│   ├── reader.py          # recorrido con .gitignore, binarios, límites
│   ├── build_detectors.py # Maven, Gradle, npm/pnpm/yarn, pip/poetry/uv
│   ├── schema.py          # Flyway, Liquibase, Alembic, Django migrations, TypeORM, Prisma
│   ├── parse_cache.py     # caché incremental de AST por hash de contenido
│   ├── watch.py           # re-análisis incremental y actualización selectiva del vault
│   ├── workspace.py       # análisis multi-repo (HTTP, eventos, tablas compartidas)
│   └── analyzer.py        # orquestador principal
├── languages/
│   ├── java/              # tree-sitter Java & Kotlin + Spring / Jakarta / Quarkus / Micronaut
│   ├── node/              # tree-sitter JS/TS + NestJS / Express / Next.js + tsconfig paths
│   └── python/            # ast nativo + FastAPI / Flask / Django
├── llm/                   # LLMProvider (ollama/openai/anthropic), extracción, juez, consolidación, eval
├── kb/                    # KnowledgeBase SQLite + índice semántico (embeddings)
├── graph/                 # KnowledgeGraph (NetworkX) + flujos end-to-end por endpoint
├── obsidian/              # generador de vault Markdown, diagramas Mermaid y sync Obsidian -> KB
├── reporting/             # reporte HTML/MD autocontenido y diff estructural
└── mcp_server/            # servidor MCP (FastMCP stdio / SSE)
```

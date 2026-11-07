# repo-analyzer (DevAnalyzer)

Analizador de proyectos multi-lenguaje. Detecta tecnología, versiones, estructura y dependencias; construye un grafo de conocimiento del proyecto y, en fases posteriores, extrae reglas de negocio con IA y publica todo en un vault de Obsidian consultable por MCP.

Hoja de ruta completa en [MEGAPLAN.md](MEGAPLAN.md).

## Estado

| Milestone | Estado |
|---|---|
| M0 Fundaciones (paquete, CLI, config, reader) | Listo |
| M1 Plugins de lenguaje + detección de build (Maven, Gradle, npm/pnpm/yarn, pip/poetry/uv) | Listo |
| M2 Análisis Java con AST (tree-sitter) + heurísticas Spring | Listo |
| M3 Grafo de conocimiento (NetworkX, JSON/GraphML) | Listo |
| M4 Reglas de negocio con LLM (Ollama / OpenAI / Anthropic) | Listo |
| M5 Knowledge base SQLite (reglas, glosario, historial, revisión humana) | Listo |
| M6 Vault Obsidian (notas, MOCs, canvas, Dataview) | Listo |
| M7 Servidor MCP | Listo |
| M8 Plugin Node/TS (NestJS, Express) | Listo |
| M9 Plugin Python (FastAPI, Flask) | Listo |
| M10 Reportes HTML/Markdown y diff entre corridas | Listo |

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"
cp .env.example .env          # opcional
```

## Uso

```bash
# Resumen: lenguajes, build system, versiones, frameworks, estereotipos
devanalyzer analyze <ruta-proyecto>

# Secciones extra y volcado JSON
devanalyzer analyze <ruta> --show deps --show units --show endpoints --json analysis.json

# Grafo de conocimiento (se guarda en <ruta>/.devanalyzer/)
devanalyzer graph build <ruta>
devanalyzer graph stats <ruta>
devanalyzer graph query <ruta> --neighbors AccountService --depth 2
devanalyzer graph query <ruta> --top 10
devanalyzer graph query <ruta> --path AccountController AccountRepository

# Reglas de negocio con IA (usa el proveedor de .env; cache en .devanalyzer/cache)
devanalyzer rules extract <ruta> --lang Spanish            # todas las unidades con lógica
devanalyzer rules extract <ruta> --stereotype service -l 5 # acotar
devanalyzer rules extract <ruta> --verify -w 4             # segunda pasada: un modelo juez audita cada regla contra su evidencia
devanalyzer rules list <ruta> --status inferred
devanalyzer rules review <ruta> BR-xxxxxxxxxx --status confirmed --note "validado con negocio"
devanalyzer rules glossary <ruta>

# Vault de Obsidian (por defecto en <ruta>/.devanalyzer/vault)
devanalyzer vault <ruta>
devanalyzer vault <ruta> --out "C:/MiVault" --folder "Proyectos/mi-app"   # dentro de un vault existente

# Servidor MCP para agentes IA (stdio por defecto; --sse para HTTP)
devanalyzer serve <ruta>

# Reporte autocontenido (HTML con diagrama SVG, o Markdown)
devanalyzer report <ruta>                 # .devanalyzer/report.html
devanalyzer report <ruta> --format md -o informe.md

# Diff estructural entre corridas (tipos, métodos, endpoints, deps, versiones)
devanalyzer snapshot <ruta>               # guarda baseline
devanalyzer diff <ruta> [--update]

# Modo watch: re-analiza al guardar y refresca graph.json, analysis.json y las notas del vault afectadas
devanalyzer watch <ruta> [--interval 1 --debounce 0.5 --no-vault --once]

# Verificar proveedor LLM configurado
devanalyzer llm-ping
```

Nota sobre incrementalidad: las respuestas del LLM se cachean por hash de (proveedor, modelo, prompt), así que re-ejecutar `rules extract` solo consulta al modelo para unidades cuyo código cambió. `watch` reutiliza además los árboles ya parseados de los archivos sin cambios, publica los artefactos de forma atómica, sincroniza antes las ediciones hechas en Obsidian y archiva en `90 Archive/` las notas de código eliminado (conservando lo escrito fuera del bloque generado).

### Configurar el servidor MCP en un cliente

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

Tools expuestas: `get_project_summary`, `list_code_units`, `get_code_unit`, `query_graph`, `find_path`, `list_endpoints`, `list_dependencies`, `get_business_rules`, `review_business_rule`, `search_knowledge`, `get_glossary`, `read_source`, `refresh_analysis`.

### Calidad de las reglas: generador + juez

`--verify` hace una segunda pasada donde un modelo juzga cada regla **solo contra el código citado** (con contexto). Marca `verdict/supported|partial|unsupported|technical`, corrige enunciados sobre-afirmados y rechaza automáticamente los no sustentados. Usa un modelo distinto al generador con `DEVANALYZER_VERIFY_MODEL` (p. ej. `qwen3:8b`): en spring-petclinic el juez `qwen3:8b` detectó las 2 alucinaciones de `qwen2.5-coder:7b` sin falsos positivos. Una confirmación humana nunca es pisada por el juez.

### Flujo recomendado

1. `devanalyzer analyze` para validar detección.
2. `devanalyzer rules extract` (las reglas nacen con estado `inferred`).
3. `devanalyzer vault` y abrir en Obsidian: revisar reglas, escribir notas manuales fuera del bloque generado.
4. `devanalyzer rules review` para confirmar/rechazar; el estado sobrevive a re-extracciones.
5. `devanalyzer serve` para que un agente consulte el conocimiento del proyecto.

`graph.graphml` se puede abrir en Gephi o yEd; `graph.json` es formato node-link de NetworkX.

## Desarrollo

```bash
pytest -q
```

Los tests usan fixtures sintéticas en `tests/fixtures/`. Si existe `~/IdeaProjects/BancoProject` también se ejecuta una prueba de integración contra ese proyecto.

## Arquitectura

```
src/devanalyzer/
├── cli.py                 # typer CLI
├── config.py              # pydantic-settings (.env, prefijo DEVANALYZER_)
├── core/
│   ├── models.py          # SourceFile, CodeUnit, Endpoint, Relation, ProjectAnalysis…
│   ├── reader.py          # recorrido con .gitignore, binarios, límites
│   ├── build_detectors.py # Maven, Gradle, npm, pip/poetry/uv
│   ├── plugin.py          # LanguagePlugin Protocol + registry
│   └── analyzer.py        # orquestador
├── languages/
│   ├── java/              # tree-sitter + heurísticas Spring
│   ├── node/              # tree-sitter JS/TS + NestJS/Express
│   └── python/            # ast nativo + FastAPI/Flask
├── llm/                   # LLMProvider (ollama/openai/anthropic), extractor de reglas, prompts
├── kb/                    # KnowledgeBase SQLite
├── graph/                 # KnowledgeGraph (NetworkX)
├── obsidian/              # generador de vault
├── reporting/             # reporte HTML/MD y diff entre análisis
└── mcp_server/            # servidor MCP (FastMCP)
```

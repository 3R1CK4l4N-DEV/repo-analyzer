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
| M4 LLM + reglas de negocio | Pendiente |
| M5 Knowledge base | Pendiente |
| M6 Vault Obsidian | Pendiente |
| M7 Servidor MCP | Pendiente |
| M8 Plugin Node/TS | Pendiente (solo detección) |
| M9 Plugin Python | Pendiente (solo detección) |

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

# Verificar proveedor LLM configurado
devanalyzer llm-ping
```

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
│   ├── java/              # parser tree-sitter, Spring, métricas
│   ├── node/              # detección (M8)
│   └── python/            # detección (M9)
├── llm/                   # LLMProvider: ollama, openai, anthropic
└── graph/                 # KnowledgeGraph (NetworkX)
```

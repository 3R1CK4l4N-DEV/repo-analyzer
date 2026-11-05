# DevAnalyzer — Megaplan

**Visión:** DevAnalyzer es un analizador de proyectos de software que inspecciona el código fuente para detectar tecnología, versiones, arquitectura y dependencias; construye un grafo de conocimiento del proyecto; usa IA para extraer **reglas de negocio** (no solo lógica técnica); y publica todo como una base de conocimiento navegable en un **vault de Obsidian**, consultable además mediante un **servidor MCP**.

## Decisiones de arquitectura (ya acordadas)

| Tema | Decisión |
|---|---|
| Multi-lenguaje | Core con arquitectura de plugins desde el día 1; **Java primero**, luego Node/TS y Python sobre la misma interfaz |
| Grafo | **NetworkX** embebido + persistencia local (JSON/GraphML + SQLite) — sin servidor |
| Obsidian | Generación de vault Markdown (notas, wikilinks, frontmatter, canvas) **+ servidor MCP** para consultar grafo/KB |
| IA | Abstracción `LLMProvider` con implementaciones **Ollama** (local) y **cloud** (OpenAI/Anthropic) configurables por `.env` |

## Estado de ejecución

| Milestone | Estado | Commit |
|---|---|---|
| M0–M3 | Hecho | `938e4e7` |
| M4–M6 | Hecho | `a9dd931` |
| M7 | Hecho | `7b9be57` |
| M8 | Hecho | `fb68d43` |
| M9 | Hecho | `d81710b` |
| M10 | Pendiente | — |

Desviaciones respecto al plan original: `compute_metrics` vive en `core/metrics.py` (compartido); PageRank usa la variante pura de Python de NetworkX (sin numpy/scipy); el vault no usa Jinja2 (plantillas en Python); `mcp` es extra opcional (`pip install -e ".[mcp]"`).

## Estado inicial del proyecto (histórico)

- ~10 archivos sueltos en raíz (sin paquete, sin tests, sin CLI).
- `OllamaClient` mínimo funcional (qwen2.5-coder:7b, qwen3:8b instalados).
- `ProjectReader` recursivo por extensiones; `MavenDetector` por presencia de `pom.xml`.
- Dataclasses: `SourceFile`, `ProjectAnalysis`, `ProjectMetrics`, `ProjectTechnology`, `BuildSystem`.
- **Bug conocido:** `requirements.txt` está codificado en UTF-16 (se generó con `pip freeze >` en PowerShell) — hay que regenerarlo en UTF-8.
- Stack: Python 3.13, `ollama`, `pydantic`, `httpx`, `python-dotenv`. Maven y Node disponibles en el entorno.

---

## Arquitectura objetivo

```
Proyecto (repo)
   │
   ▼
Ingesta ──► ProjectReader (filtros .gitignore, binarios, límites de tamaño)
   │
   ▼
Detección ──► BuildSystemDetectors (Maven, Gradle, npm/pnpm/yarn, pip/poetry/uv)
   │           LanguageDetectors (Java, JS/TS, Python)
   ▼
Análisis ──► LanguagePlugin.analyze()
   │           ├─ manifiestos de build (pom.xml, package.json, pyproject)
   │           ├─ AST: clases, métodos, imports, anotaciones, endpoints
   │           ├─ métricas estructurales
   │           └─ heurísticas de framework (Spring, Express, FastAPI…)
   ▼
Grafo ──► KnowledgeGraph (NetworkX)
   │        nodos: proyecto/módulo/paquete/clase/método/endpoint/regla/dependencia
   │        aristas: contiene, importa, extiende, implementa, llama, expone, depende-de
   ▼
   ├──────────────┬─────────────────┬──────────────────┐
   ▼              ▼                 ▼                  ▼
LLM Layer    KnowledgeBase     Obsidian Vault      MCP Server
(reglas de    (SQLite: reglas,  (Markdown + links   (tools: query_graph,
 negocio,     términos,          + canvas + MOCs)    get_rules, search…)
 glosario)    decisiones)
   │
   ▼
Reportes (CLI stdout, JSON, HTML)
```

## Estructura de directorios propuesta

```
1_DevAnalizer/
├── pyproject.toml                  # packaging + deps + entry point `devanalyzer`
├── .env.example                    # OLLAMA_HOST, MODEL, OPENAI_API_KEY…
├── MEGAPLAN.md
├── src/devanalyzer/
│   ├── __init__.py
│   ├── cli.py                      # typer: analyze / graph / vault / serve / report
│   ├── config.py                   # pydantic-settings desde .env
│   ├── core/
│   │   ├── models.py               # SourceFile, ProjectAnalysis, CodeUnit, BusinessRule…
│   │   ├── reader.py               # ProjectReader mejorado
│   │   ├── plugin.py               # Protocol LanguagePlugin + registry
│   │   └── graph.py                # KnowledgeGraph (NetworkX + persistencia)
│   ├── languages/
│   │   ├── java/  (detector.py, parser.py, spring.py, metrics.py)
│   │   ├── node/  (detector.py, parser.py, frameworks.py)
│   │   └── python/(detector.py, parser.py, frameworks.py)
│   ├── llm/
│   │   ├── provider.py             # LLMProvider abstracto + factory
│   │   ├── ollama.py               # refactor de ollama_client.py
│   │   ├── openai.py, anthropic.py
│   │   ├── prompts/                # templates .md versionados
│   │   └── business_rules.py       # extractor de reglas + glosario
│   ├── kb/
│   │   └── store.py                # SQLite: reglas, términos, decisiones
│   ├── obsidian/
│   │   ├── vault.py                # generador de notas/MOCs/canvas
│   │   └── templates/              # Jinja2 o formatos de nota
│   ├── mcp_server/
│   │   └── server.py               # mcp SDK: tools sobre grafo+KB
│   └── reporting/
│       ├── json_report.py, html_report.py
└── tests/
    ├── fixtures/                   # mini-proyectos java/node/python
    └── test_*.py
```

## Modelo de datos central (core/models.py)

- `SourceFile` (existente) + `language`, `size`, `rel_path`.
- `CodeUnit`: tipo (`class|interface|enum|function|method|module`), nombre, firma, `file`, `line_start/end`, anotaciones/decoradores, docstring.
- `Dependency`: nombre, versión, scope (`compile|runtime|dev`), origen (manifiesto).
- `Endpoint`: método HTTP, ruta, handler (CodeUnit), framework.
- `BusinessRule`: id, enunciado (lenguaje de negocio), evidencia (lista de `(file, lines)`), confianza, categoría, tags, estado (`inferred|confirmed|rejected`).
- `ProjectAnalysis` (extendido): lenguajes[], build_systems[], versions{java,node,python}, frameworks[], dependencies[], units[], endpoints[], rules[], metrics.

## Dependencias a agregar

| Dep | Uso |
|---|---|
| `tree-sitter` + `tree-sitter-language-pack` | AST para Java y JS/TS (una sola dependencia cubre ambos); Python usa `ast` nativo |
| `networkx` | Grafo de conocimiento |
| `typer` + `rich` | CLI con salida formateada |
| `pydantic-settings` | Config vía `.env` |
| `jinja2` | Templates de notas Obsidian |
| `mcp` (modelcontextprotocol) | Servidor MCP |
| `openai`, `anthropic` | Providers cloud (opcionales, lazy import) |
| `pytest` | Tests |

---

## Fases del megaplan

### M0 — Fundaciones (refactor sin features nuevas)
- Migrar a layout `src/` + `pyproject.toml`; entry point `devanalyzer`.
- Regenerar `requirements.txt` en UTF-8; `.env.example`; `.gitignore`.
- `config.py` → `pydantic-settings` (OLLAMA_HOST, LLM_PROVIDER, MODEL, tokens cloud).
- `ProjectReader`: respetar `.gitignore`/`node_modules`/`target`/binarios, límite de tamaño por archivo, `rel_path`.
- **Aceptación:** `devanalyzer analyze <path>` corre el pipeline actual (Maven detect) sin romper nada; tests de reader/detector en verde.

### M1 — Plugin de lenguajes + detección
- `LanguagePlugin` (Protocol): `detect(files) -> float` (confianza), `analyze(ctx) -> LanguageAnalysis`.
- Registro de detectores de build: Maven, Gradle (archivos `build.gradle(.kts)`, `settings.gradle`), npm (`package.json`), pip/poetry (`requirements.txt`, `pyproject.toml`).
- Extracción de versiones: `maven.compiler.*`/`java.version`/`toolchain` en `pom.xml`; `sourceCompatibility` en Gradle; `engines.node`/`volta`/`devEngines` en package.json; `requires-python` en pyproject.
- **Aceptación:** analizar un proyecto Spring Boot real reporta build system, versión Java, frameworks (Spring/Web/JPA) y dependencias con versiones.

### M2 — Análisis Java profundo (AST)
- tree-sitter-java: clases, interfaces, enums, métodos (firma), campos, imports, anotaciones, extends/implements.
- Heurísticas Spring: `@RestController`→Endpoint, `@Service/@Repository/@Entity/@Component`, `@GetMapping…`→rutas, `@Transactional`.
- Métricas: conteo por estereotipo (reemplaza `ProjectMetrics`), LOC, fan-in/out por clase.
- **Aceptación:** sobre fixture Spring, unidades y endpoints extraídos con archivo+línea correctos.

### M3 — KnowledgeGraph
- Construcción del grafo desde `ProjectAnalysis` (nodos/aristas del diagrama).
- Persistencia: `graph.json` (node-link) + `graph.graphml` + tabla SQLite; API de consulta (subgrafos, vecinos, rutas, métricas de centralidad).
- **Aceptación:** `devanalyzer graph export` produce grafo cargable; queries básicas responden en <1s en proyecto de ~500 clases.

### M4 — LLM layer + reglas de negocio
- `LLMProvider` abstracto (`generate(prompt, **kw)`); impls: `OllamaProvider` (refactor), `OpenAIProvider`, `AnthropicProvider`; selección por `.env`, lazy imports.
- Pipeline de extracción: chunking por CodeUnit (método/clase) → prompt `business_rules.md` → salida JSON estructurada (pydantic-validated) → `BusinessRule` con evidencia.
- Glosario de dominio: términos candidatos extraídos de nombres/docstrings + enriquecimiento LLM.
- Cache de respuestas por hash(contenido+prompt+modelo) en `.devanalyzer/cache/`.
- **Aceptación:** sobre fixture con lógica de negocio conocida (p.ej. descuentos, validaciones), las reglas extraídas incluyen evidencia (file:línea) y son ≥80% correctas en revisión manual.

### M5 — KnowledgeBase (SQLite)
- Tablas: `rules`, `terms`, `decisions`, `notes`, `analysis_runs` (versionado de corridas).
- API: upsert con dedup por hash, historial de reglas entre análisis (nueva/cambiada/confirmada).
- **Aceptación:** re-analizar el mismo proyecto no duplica reglas; cambios se marcan como revisión.

### M6 — Obsidian Vault
- Estructura: `00 MOC/` (índices por módulo/regla/término), `10 Modules/`, `20 Classes/`, `30 Business Rules/`, `40 Glossary/`, `50 Decisions/`, `canvas/architecture.canvas`.
- Cada nota: frontmatter YAML (tipo, tags, paquete, evidencia), wikilinks a dependencias, bloque "Generado por DevAnalyzer — no editar" + sección libre.
- Idempotente: re-generar no pisa contenido manual fuera del bloque generado.
- **Aceptación:** abrir el vault en Obsidian muestra grafo nativo navegable y notas consistentes; Dataview query de ejemplo incluida.

### M7 — Servidor MCP
- Tools: `query_graph(query)`, `get_business_rules(filter)`, `get_code_unit(name)`, `search_knowledge(term)`, `get_project_summary()`.
- Transporte stdio (compatible con clientes MCP de escritorio) + SSE opcional.
- **Aceptación:** desde un cliente MCP se puede preguntar "qué reglas de negocio hay en X" y responde con datos del KB.

### M8 — Plugin Node/TS
- `package.json`/`tsconfig`/`package-lock` parsing; tree-sitter js/ts: imports, exports, clases, funciones; heurísticas Express/NestJS (`@Controller`, `app.get`, decorators).
- Reutiliza M3–M7 sin cambios (prueba de fuego de la arquitectura de plugins).
- **Aceptación:** analizar un proyecto Nest/Express produce grafo + reglas + vault comparables al de Java.

### M9 — Plugin Python
- `ast` nativo: clases, funciones, decoradores, imports; heurísticas FastAPI/Flask/Django; pyproject/requirements.
- **Aceptación:** analizar el propio DevAnalyzer produce un vault correcto (self-analysis como dogfooding).

### M10 — Reportes, UX y hardening
- `devanalyzer report --format html|md` con dashboard (métricas, grafo incrustado, reglas).
- Diff entre corridas (`--since`), `--incremental` (solo archivos cambiados por hash).
- README, ejemplos, fixtures de los 3 lenguajes en CI local.

---

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Extracción LLM de reglas poco fiable | Salida JSON validada con pydantic + evidencia obligatoria (file:line); estado `inferred` hasta confirmación humana en el vault |
| Contexto limitado en proyectos grandes | Chunking por unidad + resumen jerárquico (método→clase→módulo); cache |
| Rendimiento en repos grandes | `.gitignore`/límites en reader; incremental por hash; AST lazy |
| requirements.txt corrupto (UTF-16) | Regenerar en M0 |
| Scope creep | Fases con aceptación clara; Node/Python solo tras validar arquitectura con Java |

## Orden de ejecución recomendado

M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9 → M10
(cada milestone deja el sistema usable end-to-end hasta ese punto)

# DevAnalyzer — Estado actual y mejoras a definir

Fecha: 2026-11-05 · Rama `main` (local, 11 commits, sin push) · 41/41 tests · ~4.6k líneas de código + 850 de tests

---

## 1. Estado actual

### 1.1 Qué hace hoy (end-to-end)

```
devanalyzer analyze <repo>            → lenguajes, build, versiones, frameworks, estereotipos, endpoints
devanalyzer graph build|query|stats   → grafo NetworkX (JSON + GraphML), vecinos, caminos, PageRank
devanalyzer rules extract --verify    → reglas de negocio con evidencia file:línea, glosario, juez LLM
devanalyzer rules list|review|glossary→ knowledge base SQLite con revisión humana persistente
devanalyzer vault                     → vault Obsidian (notas, MOCs, canvas, Dataview, secciones manuales)
devanalyzer serve                     → servidor MCP con 13 tools para agentes IA
devanalyzer report | snapshot | diff  → HTML/MD autocontenido, diff estructural entre corridas
```

### 1.2 Milestones del megaplan

| M | Alcance | Estado | Commit |
|---|---|---|---|
| M0 | Paquete `src/`, CLI typer, config `.env`, reader con `.gitignore` | Hecho | `938e4e7` |
| M1 | Plugins de lenguaje, detectores Maven/Gradle/npm/pnpm/yarn/pip/poetry/uv | Hecho | `938e4e7` |
| M2 | Java AST (tree-sitter) + heurísticas Spring | Hecho | `938e4e7` |
| M3 | KnowledgeGraph NetworkX, persistencia, consultas | Hecho | `938e4e7` |
| M4 | LLMProvider (Ollama/OpenAI/Anthropic) + extractor de reglas | Hecho | `a9dd931` |
| M5 | Knowledge base SQLite (dedup, historial, revisión humana) | Hecho | `a9dd931` |
| M6 | Vault Obsidian | Hecho | `a9dd931` |
| M7 | Servidor MCP | Hecho | `7b9be57` |
| M8 | Plugin Node/TS (NestJS, Express) | Hecho | `fb68d43` |
| M9 | Plugin Python (FastAPI, Flask) | Hecho | `d81710b` |
| M10 | Reporte HTML/MD, snapshot/diff | Hecho | `017d028` |
| + | Perf: vault 4.9→2.0 s, graph save 2.3→0.55 s (3.000 clases) | Hecho | `9afb66a` |
| + | Extracción paralela `--workers`, cache atómica | Hecho | `5afebb2` |
| + | Pasada de verificación `--verify` con modelo juez | Hecho | `e93923a` |

### 1.3 Validación con proyectos reales

| Proyecto | Resultado |
|---|---|
| `~/IdeaProjects/BancoProject` (tuyo) | Maven, Java 17, Spring Boot 3.0.0, Lombok. Test de integración automático |
| `spring-projects/spring-petclinic` (clonado y auditado: checksum wrapper OK, sin hooks, sin repos externos) | 17 endpoints, 304 nodos; 13 reglas de negocio en español; juez `qwen3:8b` detectó las 2 alucinaciones del generador `qwen2.5-coder:7b` sin falsos positivos |
| DevAnalyzer sobre sí mismo (dogfooding) | 523 nodos / 1.717 aristas; test automático |
| Sintético 3.000 clases Spring | Análisis estático completo ~5.5 s; grafo 18.8k nodos |

### 1.4 Números de performance (sintético 3.000 clases)

| Etapa | Tiempo |
|---|---|
| Lectura (caliente) | 0.7 s |
| Análisis Java | 1.0 s (tree-sitter puro: 0.29 s) |
| Grafo build / save / load | 0.14 s / 0.55 s / 0.2 s |
| Vault (6.000 notas) | 2.0 s (I/O de Windows es el límite) |
| Reporte HTML | 0.09 s |
| **LLM por unidad (Ollama 7b local)** | **10–20 s** ← único cuello de botella real |

### 1.5 Deuda técnica conocida

| Ítem | Impacto | Nota |
|---|---|---|
| Resolución de versiones heredadas de BOM Maven | Bajo | Se reporta `managed`; resolver requiere red/`mvn help:effective-pom` |
| Heurísticas de estereotipos Python/Node por nombre | Medio | Falsos positivos posibles (`*Manager`, `*Model`); configurables en `languages/*/frameworks.py` |
| Resolución de imports Node sin `tsconfig` paths/aliases (`@/…`) | Medio | Solo imports relativos; aliases quedan sin relación |
| Kotlin detectado pero no parseado | Medio | Conteo de archivos sí; sin unidades ni relaciones |
| Tablas Rich truncan con `…` en consola Windows legacy | Cosmético | `--json` para salida completa |
| Sin `watch` ni re-análisis incremental de parsing | Bajo | El parsing es rápido; solo la LLM está cacheada |
| Sin CI | Medio | Tests solo locales |

---

## 2. Mejoras a definir

Ordenadas por valor/esfuerzo estimado. Las marcadas con **[DECISIÓN]** requieren tu input antes de implementarse.

### 2.1 Calidad de reglas de negocio

| # | Mejora | Valor | Esfuerzo | Decisión requerida |
|---|---|---|---|---|
| R1 | **Consolidación por módulo**: segunda pasada que agrupa reglas duplicadas semánticamente entre clases y genera un resumen de dominio por paquete (reglas transversales: "toda operación monetaria valida monto > 0") | Alto | Medio | **[DECISIÓN D1]** |
| R2 | **Dataset de evaluación**: fixture con reglas "gold" etiquetadas + `devanalyzer eval` que mide precisión/recall por modelo. Permite comparar modelos objetivamente en vez de revisión manual | Alto | Medio | **[DECISIÓN D2]** |
| R3 | Prompts por tipo de unidad (entity → invariantes/validaciones; controller → autorizaciones/contratos; service → políticas/cálculos) | Medio | Bajo | No |
| R4 | Contexto enriquecido en el prompt: firmas de las dependencias que usa la clase (del grafo) para que el modelo entienda llamadas externas | Medio | Bajo | No |
| R5 | Salida estructurada nativa (JSON mode / tool calling) en OpenAI/Anthropic/Ollama `format=json` en lugar de parseo tolerante | Medio | Bajo | No |

### 2.2 Cobertura de lenguajes/frameworks

| # | Mejora | Valor | Esfuerzo | Decisión requerida |
|---|---|---|---|---|
| L1 | **Kotlin** (tree-sitter-kotlin; Spring en Kotlin es común) | Alto si usas Kotlin | Medio | **[DECISIÓN D3]** |
| L2 | Java: Jakarta EE/JAX-RS (`@Path`, `@GET`), Quarkus, Micronaut | Medio | Bajo | **[DECISIÓN D3]** |
| L3 | Node: aliases de `tsconfig.paths`, Fastify plugins, Next.js API routes (por carpeta) | Medio | Medio | **[DECISIÓN D3]** |
| L4 | Python: Django (urls.py, models, views basadas en clase), SQLAlchemy models como entity | Medio | Medio | **[DECISIÓN D3]** |
| L5 | SQL/migraciones (Flyway, Liquibase, Alembic) → nodos "tabla" en el grafo ligados a entities | Alto para reglas de datos | Medio | **[DECISIÓN D3]** |
| L6 | C#/.NET, Go | Depende de tu cartera | Alto | **[DECISIÓN D3]** |

### 2.3 Grafo y base de conocimiento

| # | Mejora | Valor | Esfuerzo | Decisión requerida |
|---|---|---|---|---|
| G1 | **Búsqueda semántica** (embeddings de unidades, reglas y notas) para `search_knowledge` y para dar contexto relevante al LLM (RAG) | Alto | Medio | **[DECISIÓN D4]** |
| G2 | Flujos end-to-end: endpoint → service → repository → tabla, como "casos de uso" navegables en vault y MCP | Alto | Medio | No |
| G3 | Métricas de arquitectura: ciclos entre paquetes, violaciones de capas (controller→repository directo), acoplamiento por módulo | Medio | Bajo | No |
| G4 | Multi-proyecto: una KB/vault que indexe varios repos (microservicios) con relaciones entre ellos vía HTTP clients/eventos | Alto si hay microservicios | Alto | **[DECISIÓN D5]** |
| G5 | Migrar grafo a Kuzu/Neo4j para consultas Cypher si los proyectos superan ~50k nodos | Bajo hoy | Medio | No (revisar si G4) |

### 2.4 Obsidian y documentación

| # | Mejora | Valor | Esfuerzo | Decisión requerida |
|---|---|---|---|---|
| O1 | Diagramas Mermaid por módulo/caso de uso dentro de las notas (secuencia y clases) | Alto | Bajo | No |
| O2 | Nota de "decisiones" (ADR) editable desde Obsidian que se sincronice de vuelta a la KB (lectura del vault → SQLite) | Medio | Medio | **[DECISIÓN D6]** |
| O3 | Plantillas de nota personalizables por el usuario (`.devanalyzer/templates/*.md`) | Medio | Bajo | No |
| O4 | Revisión de reglas desde Obsidian: cambiar `status:` en frontmatter y que `devanalyzer vault --sync` lo lleve a la KB | Alto (flujo natural para negocio) | Medio | **[DECISIÓN D6]** |

### 2.5 Operación, DX y distribución

| # | Mejora | Valor | Esfuerzo | Decisión requerida |
|---|---|---|---|---|
| X1 | **GitHub Actions**: tests en Windows/Linux, lint (ruff), matriz Python 3.11–3.13 | Alto | Bajo | **[DECISIÓN D7]** |
| X2 | `devanalyzer watch`: re-análisis al guardar y regeneración de notas afectadas | Medio | Medio | No |
| X3 | Publicar en PyPI / `pipx install devanalyzer` | Medio | Bajo | **[DECISIÓN D7]** |
| X4 | Docker image con Ollama + DevAnalyzer para demos | Bajo | Bajo | No |
| X5 | Logging estructurado y `--verbose`/`--quiet` | Bajo | Bajo | No |
| X6 | Reporte en PR (GitHub Action que comenta el `diff` + reglas nuevas) | Alto en equipos | Medio | **[DECISIÓN D7]** |

---

## 3. Decisiones que necesito de tu lado

| ID | Pregunta | Opciones | Mi recomendación |
|---|---|---|---|
| **D1** | ¿La consolidación de reglas debe ser por **paquete/módulo** (estructura del código) o por **término de dominio** (glosario: todas las reglas de "Cuenta")? | a) por módulo · b) por término · c) ambas vistas | **c)** ambas: el módulo sirve al desarrollador, el término al negocio; costo marginal bajo una vez hecha una |
| **D2** | ¿Quién etiqueta el dataset "gold" para medir precisión? | a) yo genero fixtures sintéticas con reglas conocidas · b) tú etiquetas un proyecto real tuyo (BancoProject ampliado) · c) ambos | **c)** sintético para regresión automática + uno real tuyo para calidad percibida |
| **D3** | ¿Qué stack priorizar después? Dime tu cartera real de proyectos | Kotlin / Jakarta-Quarkus / Django / Next.js / SQL-migraciones / .NET / Go | Sin datos de tu cartera: **SQL-migraciones (L5)** porque enriquece reglas de datos en los 3 lenguajes ya soportados |
| **D4** | ¿Embeddings locales u online? | a) Ollama `nomic-embed-text` + índice en SQLite (sin deps nuevas) · b) ChromaDB/LanceDB local · c) OpenAI embeddings | **a)** coherente con "todo local por defecto"; migrar a b) si pasa de ~100k fragmentos |
| **D5** | ¿Vas a analizar sistemas de **microservicios** (varios repos relacionados)? | sí / no / más adelante | Si "sí", G4 cambia el modelo de datos (project_id en todo) y conviene hacerlo antes que G1/G2 |
| **D6** | ¿Dirección de sincronía con Obsidian? | a) solo generar (hoy) · b) leer frontmatter `status:` del vault → KB (`--sync`) · c) bidireccional completa incl. notas manuales | **b)** cubre el flujo "negocio revisa en Obsidian" con riesgo bajo; c) es complejo y propenso a conflictos |
| **D7** | ¿Repo público u orientado a equipo? Define CI, PyPI y Action de PR | a) uso personal · b) equipo interno · c) open source | Con a) solo X1; con b) X1+X6; con c) X1+X3+X6 |
| **D8** | ¿Push a `origin/main` ahora? Hay 10 commits locales sin publicar | sí / no | **sí**, antes de seguir acumulando |

---

## 4. Propuesta de siguiente iteración (si apruebas las recomendaciones)

1. **D8** push.
2. **X1** CI con GitHub Actions (barato, protege todo lo demás).
3. **R2** dataset de evaluación + `devanalyzer eval` (sin métrica no podemos afirmar que R1/R3/R4 mejoran).
4. **R1** consolidación por módulo y por término.
5. **O4** revisión de reglas desde Obsidian (`vault --sync`).
6. **O1** Mermaid en notas.
7. Según D3: primer lenguaje/framework adicional.

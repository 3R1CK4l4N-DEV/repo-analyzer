# DevAnalyzer — Estado actual y mejoras a definir

Fecha: 2026-11-05 (actualizado tras iteración 2) · Rama `main` (local, 20 commits, sin push) · 60/60 tests

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
| X1 | CI GitHub Actions (ubuntu/windows × py3.11/3.13) + ruff | Hecho | `ec43cfb` |
| R2 | Gold datasets + `rules eval` (P/R sin LLM); `reviewed_by` humano vs juez | Hecho | `5d4caa0` |
| R1 | `rules consolidate`: duplicados, reglas transversales, resumen por módulo/término; notas `35 Domains` | Hecho | `95cdb2e` |
| O4 | `vault --sync`: status/review_note/statement editados en Obsidian → KB; ediciones humanas sobreviven re-extracciones | Hecho | `95cdb2e` |
| O1 | Mermaid: classDiagram por módulo/arquitectura, sequenceDiagram por endpoint, erDiagram | Hecho | `daec95e` |
| L5 | Esquema de BD desde migraciones (Flyway/SQL, Liquibase XML+YAML, Alembic, TypeORM, Prisma); entity→tabla; `60 Data` | Hecho | `05c0b6c` |

### 1.3 Validación con proyectos reales

| Proyecto | Resultado |
|---|---|
| `~/IdeaProjects/BancoProject` (tuyo) | Maven, Java 17, Spring Boot 3.0.0, Lombok. Test de integración automático |
| `spring-projects/spring-petclinic` (clonado y auditado: checksum wrapper OK, sin hooks, sin repos externos) | 17 endpoints, 304 nodos, 7 tablas con mapeo entidad→tabla; 13 reglas en español; **eval P/R 91%** (generador `qwen2.5-coder:7b` + juez `qwen3:8b`); consolidación detecta 1 duplicado real |
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
| R1 | ~~Consolidación por módulo/término~~ | Hecho | — | D1 = ambas |
| R2 | ~~Dataset de evaluación + `rules eval`~~ | Hecho | — | D2 = ambos (sintético + petclinic real etiquetado) |
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
| L5 | ~~SQL/migraciones → tablas en grafo~~ | Hecho | — | Flyway, Liquibase, Alembic, TypeORM, Prisma |
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
| O1 | ~~Diagramas Mermaid~~ | Hecho | — | class/sequence/ER |
| O2 | Nota de "decisiones" (ADR) editable desde Obsidian que se sincronice de vuelta a la KB (lectura del vault → SQLite) | Medio | Medio | **[DECISIÓN D6]** |
| O3 | Plantillas de nota personalizables por el usuario (`.devanalyzer/templates/*.md`) | Medio | Bajo | No |
| O4 | ~~Revisión desde Obsidian (`vault --sync`)~~ | Hecho | — | D6 = status + nota + enunciado |

### 2.5 Operación, DX y distribución

| # | Mejora | Valor | Esfuerzo | Decisión requerida |
|---|---|---|---|---|
| X1 | ~~GitHub Actions + ruff~~ | Hecho (se activa al hacer push) | — | |
| X2 | `devanalyzer watch`: re-análisis al guardar y regeneración de notas afectadas | Medio | Medio | No |
| X3 | Publicar en PyPI / `pipx install devanalyzer` | Medio | Bajo | **[DECISIÓN D7]** |
| X4 | Docker image con Ollama + DevAnalyzer para demos | Bajo | Bajo | No |
| X5 | Logging estructurado y `--verbose`/`--quiet` | Bajo | Bajo | No |
| X6 | Reporte en PR (GitHub Action que comenta el `diff` + reglas nuevas) | Alto en equipos | Medio | **[DECISIÓN D7]** |

---

## 3. Decisiones tomadas

| ID | Decisión |
|---|---|
| D1 | Consolidación por módulo **y** por término (hecho) |
| D2 | Dataset sintético + petclinic etiquetado (hecho) |
| D3 | Cartera: Java/Spring, **Kotlin+Spring**, **Jakarta/Quarkus/Micronaut**, Django/Next.js/.NET/Go → siguiente prioridad: Kotlin (L1), luego Jakarta (L2) |
| D5 | Microservicios **próximamente** → la KB ya lleva `project` en todas las tablas y el grafo tiene nodo `project`; falta índice cross-repo (G4) |
| D6 | Sync Obsidian→KB de status + nota + enunciado (hecho) |
| D8 | **Sin push** por ahora; todo local |
| L5 | Flyway, Liquibase, Alembic, TypeORM y Prisma (hecho) |

Pendientes de decidir: **D4** (embeddings: recomiendo Ollama `nomic-embed-text` + SQLite) y **D7** (personal / equipo / open source).

## 3b. Decisiones pendientes (texto original)

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

## 4. Iteración 2 — completada

X1 → R2 → R1 → O4 → O1 → L5, todo con tests y validado en spring-petclinic.

## 5. Propuesta de iteración 3

1. **L1 Kotlin** (tree-sitter-kotlin; mismas heurísticas Spring) — tu cartera lo usa.
2. **L2 Jakarta/Quarkus/Micronaut** (JAX-RS `@Path/@GET`, CDI `@Inject`, Panache) — bajo esfuerzo sobre el parser Java actual.
3. **R3+R4** prompts por tipo de unidad + contexto de dependencias; medir con `rules eval` si sube P/R sobre el 91% actual.
4. **G2 flujos end-to-end** (endpoint → service → repository → tabla) como casos de uso navegables; ya hay base con secuencias Mermaid + tablas.
5. **G1 búsqueda semántica** si apruebas D4.
6. **G4 multi-repo** (D5): índice de proyectos + relaciones por clientes HTTP/eventos.

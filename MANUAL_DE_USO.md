# Manual de Uso — DevAnalyzer (v1.0.0)

**DevAnalyzer** es una herramienta de línea de comandos para analizar proyectos de software multi-lenguaje, construir su grafo de conocimiento arquitectónico, extraer y auditar reglas de negocio mediante inteligencia artificial, y publicar toda la documentación técnica y funcional en un **Vault de Obsidian** y a través de un **servidor MCP** para agentes de IA.

---

## Índice

1. [Requisitos e instalación](#1-requisitos-e-instalación)
2. [Configuración (`.env`)](#2-configuración-env)
3. [Tecnologías soportadas](#3-tecnologías-soportadas)
4. [Flujo de trabajo recomendado (Paso a paso)](#4-flujo-de-trabajo-recomendado-paso-a-paso)
5. [Referencia completa de comandos CLI](#5-referencia-completa-de-comandos-cli)
   - [5.1 `analyze` — Diagnóstico estático del proyecto](#51-analyze--diagnóstico-estático-del-proyecto)
   - [5.2 `graph` — Grafo de conocimiento](#52-graph--grafo-de-conocimiento)
   - [5.3 `rules` — Extracción, verificación, consolidación y evaluación de reglas](#53-rules--extracción-verificación-consolidación-y-evaluación-de-reglas)
   - [5.4 `index` y `search` — Búsqueda semántica y RAG](#54-index-y-search--búsqueda-semántica-y-rag)
   - [5.5 `vault` — Generación y sincronización con Obsidian](#55-vault--generación-y-sincronización-con-obsidian)
   - [5.6 `watch` — Modo incremental en tiempo real](#56-watch--modo-incremental-en-tiempo-real)
   - [5.7 `report`, `snapshot` y `diff` — Informes y control de cambios](#57-report-snapshot-y-diff--informes-y-control-de-cambios)
   - [5.8 `workspace` — Sistemas multi-repositorio y microservicios](#58-workspace--sistemas-multi-repositorio-y-microservicios)
   - [5.9 `serve` — Servidor MCP para agentes IA](#59-serve--servidor-mcp-para-agentes-ia)
   - [5.10 `llm-ping` — Prueba de conexión con el LLM](#510-llm-ping--prueba-de-conexión-con-el-llm)
6. [Estructura de artefactos generados (`.devanalyzer/`)](#6-estructura-de-artefactos-generados-devanalyzer)
7. [Trabajo con el Vault de Obsidian (Edición y Sincronización)](#7-trabajo-con-el-vault-de-obsidian-edición-y-sincronización)
8. [Solución de problemas frecuentes](#8-solución-de-problemas-frecuentes)

---

## 1. Requisitos e instalación

### Requisitos previos
- **Python 3.11 o superior** (probado en 3.11, 3.12 y 3.13 en Windows y Linux).
- *(Opcional, para extracción local de reglas y embeddings)* **Ollama** ejecutándose en `http://localhost:11434` con un modelo de código (por defecto `qwen2.5-coder:7b`) y, si usas búsqueda semántica, `nomic-embed-text`.
- *(Opcional)* **Obsidian** para explorar el vault generado.

### Instalación en entorno virtual

```bash
# Clonar el repositorio y entrar al directorio
git clone https://github.com/3R1CK4l4N-DEV/repo-analyzer.git
cd repo-analyzer

# Crear y activar entorno virtual
python -m venv .venv
.venv\Scripts\activate          # En Windows
# source .venv/bin/activate     # En Linux / macOS

# Instalación base + herramientas de desarrollo y servidor MCP
pip install -e ".[dev]"
```

### Extras según el proveedor que utilices

| Extra | Comando | Qué instala |
|---|---|---|
| Servidor MCP | `pip install -e ".[mcp]"` | SDK `mcp` (`FastMCP`) |
| OpenAI | `pip install -e ".[openai]"` | Cliente oficial `openai` (LLM y embeddings) |
| Anthropic | `pip install -e ".[anthropic]"` | Cliente oficial `anthropic` |
| Desarrollo completo | `pip install -e ".[dev,openai,anthropic]"` | `pytest`, `mcp`, `openai`, `anthropic` |

Verifica que el CLI quedó instalado ejecutando:

```bash
devanalyzer --version
# devanalyzer 1.0.0
```

---

## 2. Configuración (`.env`)

Copia el archivo de ejemplo en la raíz desde donde ejecutes `devanalyzer`:

```bash
cp .env.example .env
```

Variables disponibles (todas llevan el prefijo `DEVANALYZER_`):

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `DEVANALYZER_LLM_PROVIDER` | `ollama` | Proveedor principal para extraer y consolidar reglas (`ollama`, `openai`, `anthropic`). |
| `DEVANALYZER_LLM_MODEL` | `qwen2.5-coder:7b` | Modelo generador (ej. `qwen2.5-coder:7b`, `gpt-4o-mini`, `claude-3-5-sonnet-latest`). |
| `DEVANALYZER_OLLAMA_HOST` | `http://localhost:11434` | URL del servidor Ollama local o remoto. |
| `DEVANALYZER_VERIFY_PROVIDER` | *(igual al generador)* | Proveedor para la pasada de verificación (`--verify`). |
| `DEVANALYZER_VERIFY_MODEL` | *(igual al generador)* | Modelo juez para auditar reglas contra el código (recomendado usar un modelo distinto al generador, p. ej. `qwen3:8b`). |
| `DEVANALYZER_EMBEDDING_PROVIDER` | `ollama` | Proveedor de embeddings para `index` y `search` (`ollama` u `openai`). |
| `DEVANALYZER_EMBEDDING_MODEL` | `nomic-embed-text` | Modelo de embeddings (`nomic-embed-text` en Ollama; `text-embedding-3-small` en OpenAI). |
| `DEVANALYZER_OPENAI_API_KEY` | *(vacío)* | API Key si usas OpenAI como generador, juez o proveedor de embeddings. |
| `DEVANALYZER_ANTHROPIC_API_KEY` | *(vacío)* | API Key si usas Anthropic. |
| `DEVANALYZER_WORKSPACE` | *(vacío)* | Ruta opcional al `workspace.json` cuando trabajas con múltiples repositorios. |
| `DEVANALYZER_MAX_FILE_SIZE_KB` | `512` | Tamaño máximo por archivo fuente a leer (archivos mayores o binarios se omiten). |
| `DEVANALYZER_OUTPUT_DIR_NAME` | `.devanalyzer` | Nombre de la carpeta de artefactos dentro del proyecto analizado. |

> **Nota:** El análisis estático (`analyze`, `graph`, `report`, `snapshot`, `diff`, `watch`, `workspace` y `vault` sin reglas) **no requiere ningún LLM ni conexión a internet**.

---

## 3. Tecnologías soportadas

### Lenguajes y frameworks

| Ecosistema | Lenguajes | Sistemas de Build | Frameworks y Heurísticas |
|---|---|---|---|
| **JVM** | Java, Kotlin | Maven (`pom.xml` con herencia `<parent>`), Gradle (`.gradle`, `.gradle.kts`) | Spring Boot (`@RestController`, `@Service`, `@Repository`, `@Entity`, `@Component`, `@ControllerAdvice`), Jakarta EE / JAX-RS (`@Path`, `@GET/@POST…`, CDI scopes), Quarkus (Panache), Micronaut (`@Controller`, `@Get/@Post…`) |
| **Node / TS** | JavaScript, TypeScript, JSX, TSX | npm, pnpm, yarn (`package.json`, `tsconfig.json` con resolución de `paths` y `baseUrl`) | NestJS (`@Controller`, `@Injectable`, `@Entity`), Express / Fastify (`app.get/post…`, `router.*`), Next.js (App Router `app/**/route.ts` y `page.tsx`, Pages Router `pages/api/**` y `pages/**`, rutas dinámicas y grupos) |
| **Python** | Python (`.py`) | pip, poetry, uv (`pyproject.toml`, `requirements.txt` con seguimiento de `-r` y `requirements/*.txt`) | FastAPI (`@app.get`, `@router.post`, `Depends`), Flask (`@app.route`, `@bp.route`), Django y Django REST Framework (`urlpatterns`, `path`, `re_path`, `include()` anidados, `DefaultRouter`/`SimpleRouter`, `APIView`, `ViewSet`, `Model`, `Manager`) |

### Esquema de base de datos y migraciones
DevAnalyzer reconstruye las tablas, columnas, claves primarias, claves foráneas y su mapeo hacia entidades ORM a partir de:
- **SQL / Flyway** (`V1__*.sql`, `CREATE TABLE`, `ALTER TABLE`).
- **Liquibase** (`changelog.xml`, `changelog.yaml` / `.yml`).
- **Alembic** (`op.create_table`, `op.add_column` en archivos de versiones `.py`).
- **Migraciones de Django** (`migrations/*.py`: `CreateModel`, `AddField`, `AlterField`, `RemoveField`, `RenameField`, `RenameModel`, `DeleteModel`).
- **TypeORM** (`queryRunner.createTable` y entidades `@Entity`).
- **Prisma** (`schema.prisma`, incluidos `@@map` y `@map`).

---

## 4. Flujo de trabajo recomendado (Paso a paso)

Si analizas un proyecto por primera vez (por ejemplo `C:/Proyectos/mi-backend`), sigue este orden:

```bash
# Paso 1: Inspección rápida (verifica que detecta lenguaje, framework, endpoints y tablas)
devanalyzer analyze C:/Proyectos/mi-backend --show all

# Paso 2: Construir el grafo de conocimiento y guardar el snapshot base
devanalyzer graph build C:/Proyectos/mi-backend

# Paso 3 (opcional): Crear el índice semántico para búsqueda y contexto RAG
devanalyzer index C:/Proyectos/mi-backend

# Paso 4: Extraer reglas de negocio con IA y auditarlas con el modelo juez
devanalyzer rules extract C:/Proyectos/mi-backend --lang Spanish --verify

# Paso 5: Consolidar reglas por módulo y por término de dominio (fusiona duplicados y genera resúmenes)
devanalyzer rules consolidate C:/Proyectos/mi-backend --lang Spanish

# Paso 6: Generar el Vault de Obsidian
devanalyzer vault C:/Proyectos/mi-backend
```

A partir de aquí:
1. Abre la carpeta `C:/Proyectos/mi-backend/.devanalyzer/vault` como un Vault en **Obsidian** (empieza por la nota `<proyecto> - Home`).
2. Revisa las reglas en `30 Business Rules/`: cambia `status: confirmed` o `status: rejected`, corrige `statement:` o agrega un `review_note:` en el frontmatter YAML, y escribe tus propias notas debajo de `## Notes`.
3. Vuelve a correr `devanalyzer vault C:/Proyectos/mi-backend`: tus cambios en Obsidian se sincronizan automáticamente hacia `kb.sqlite` y **nunca serán sobrescritos** en futuras extracciones.

---

## 5. Referencia completa de comandos CLI

### 5.1 `analyze` — Diagnóstico estático del proyecto

Analiza el repositorio y muestra en consola el resumen de lenguajes, sistemas de build, versiones detectadas, frameworks y conteo por estereotipo.

```bash
devanalyzer analyze <ruta-proyecto> [OPCIONES]
```

**Opciones:**
- `--show`, `-s`: Secciones detalladas a mostrar en tablas. Puede repetirse:
  - `deps`: Dependencias declaradas con versión, scope y manifiesto.
  - `units`: Clases, interfaces, módulos y sus métodos con líneas de inicio/fin.
  - `endpoints`: Endpoints HTTP detectados (` método`, `ruta`, `handler`, `archivo:línea`).
  - `tables`: Tablas de base de datos, columnas, FKs y entidades mapeadas.
  - `all`: Muestra todas las anteriores.
- `--json <archivo>`: Exporta el objeto `ProjectAnalysis` completo en formato JSON.

**Ejemplo:**
```bash
devanalyzer analyze ./mi-app -s endpoints -s tables --json ./mi-app-analysis.json
```

---

### 5.2 `graph` — Grafo de conocimiento

Construye y consulta el grafo dirigido del proyecto (`NetworkX`), donde los nodos son módulos, clases, métodos, endpoints, tablas y dependencias, y las aristas representan `contains`, `imports`, `extends`, `implements`, `depends_on`, `calls`, `exposes`, `maps_to_table` y `uses_dependency`.

#### `devanalyzer graph build`
```bash
devanalyzer graph build <ruta-proyecto> [--out <dir>] [--no-graphml]
```
- Guarda `graph.json` (formato node-link), `graph.graphml` (compatible con **Gephi** y **yEd**) y `analysis.json` en `<proyecto>/.devanalyzer/`.
- Usa `--no-graphml` en repositorios muy grandes si solo necesitas el JSON.

#### `devanalyzer graph stats`
```bash
devanalyzer graph stats <ruta-proyecto>
```
- Muestra el total de nodos y aristas desglosado por tipo.

#### `devanalyzer graph query`
```bash
# Vecindario de una clase o método a profundidad 2
devanalyzer graph query <ruta> --neighbors AccountService --depth 2

# Filtrar por tipo de relación (depends_on, calls, extends, implements, exposes...)
devanalyzer graph query <ruta> --neighbors AccountService --depth 1 --kind depends_on

# Top 10 nodos más referenciados (in-degree + PageRank)
devanalyzer graph query <ruta> --top 10

# Camino más corto entre dos componentes
devanalyzer graph query <ruta> --path AccountController AccountRepository
```

---

### 5.3 `rules` — Extracción, verificación, consolidación y evaluación de reglas

#### `devanalyzer rules extract`
Envía las unidades candidatas con lógica de negocio (`service`, `controller`, `entity`, `component`, `advice` y módulos funcionales) al LLM configurado, enriqueciendo el prompt con las firmas de sus dependencias, tablas asociadas y contexto semántico (RAG).

```bash
devanalyzer rules extract <ruta-proyecto> [OPCIONES]
```

**Opciones:**
- `--lang <idioma>`: Idioma de salida para los enunciados y el glosario (por defecto `English`; usa `Spanish` para español).
- `--stereotype`, `-s <tipo>`: Limita la extracción a ciertos estereotipos (ej. `-s service -s entity`).
- `--limit`, `-l <N>`: Número máximo de unidades a enviar al LLM (útil para pruebas rápidas).
- `--verify`: Activa una **segunda pasada con un modelo juez** que audita cada regla únicamente contra las líneas de código citadas, corrigiendo exageraciones y marcando como `rejected` las alucinaciones o detalles puramente técnicos.
- `--workers`, `-w <N>`: Llamadas al LLM en paralelo (por defecto `1` para Ollama local, `4` para proveedores cloud).
- `--no-cache`: Ignora la caché de respuestas de `.devanalyzer/cache/`.
- `--no-rag`: No inyecta contexto del índice semántico en el prompt.

#### `devanalyzer rules list`
Lista las reglas almacenadas en `kb.sqlite` y muestra el resumen por estado y categoría.

```bash
devanalyzer rules list <ruta> [--status inferred|confirmed|rejected] [--category <cat>] [--unit <texto>] [-q <búsqueda>] [--duplicates]
```

#### `devanalyzer rules review`
Registra una decisión humana sobre una regla desde la terminal. Las reglas revisadas por un humano (`reviewed_by = 'human'`) se preservan intactas aunque vuelvas a ejecutar `rules extract`.

```bash
devanalyzer rules review <ruta> BR-1a2b3c4d5e --status confirmed --note "Validado con el líder funcional"
```

#### `devanalyzer rules consolidate`
Agrupa las reglas por paquete/módulo y por término de dominio, identifica duplicados semánticos entre clases, sintetiza reglas transversales (*cross-cutting*) y redacta un resumen ejecutivo para cada dominio.

```bash
devanalyzer rules consolidate <ruta> [--by both|module|term] [--depth 3] [--lang Spanish] [-w 4]
```

#### `devanalyzer rules glossary`
Muestra el glosario de lenguaje ubicuo inferido del código y vinculado a clases y reglas.

```bash
devanalyzer rules glossary <ruta> [-q <término>]
```

#### `devanalyzer rules eval`
Mide precisión, recall, F1 y exactitud de categoría contra un dataset de referencia (*gold set*) etiquetado manualmente, sin usar llamadas al LLM.

```bash
devanalyzer rules eval <ruta> --gold ./gold_rules.json [--include-rejected] [--json reporte_eval.json]
```

---

### 5.4 `index` y `search` — Búsqueda semántica y RAG

Construye un índice vectorial dentro de `kb.sqlite` (tabla `embeddings`) sobre clases, métodos, endpoints, tablas, reglas de negocio y términos del glosario. Solo re-calcula embeddings cuando cambia el hash del documento.

```bash
# Construir o actualizar el índice (usa nomic-embed-text en Ollama o text-embedding-3-small en OpenAI)
devanalyzer index <ruta-proyecto> [--force]

# Buscar en lenguaje natural
devanalyzer search <ruta-proyecto> "validación de saldo mínimo al retirar" --limit 10
devanalyzer search <ruta-proyecto> "cálculo de comisión" -k business_rule -k method
```

Valores válidos para `--kind` (`-k`): `class`, `method`, `endpoint`, `table`, `business_rule`, `glossary_term`.

---

### 5.5 `vault` — Generación y sincronización con Obsidian

Genera o actualiza el Vault de Obsidian completo.

```bash
# Generar en <proyecto>/.devanalyzer/vault
devanalyzer vault <ruta-proyecto>

# Generar dentro de una subcarpeta de tu Vault personal de Obsidian
devanalyzer vault <ruta-proyecto> --out "C:/Users/MiUsuario/Documents/ObsidianVault" --folder "Proyectos/MiBackend"

# Previsualizar qué cambios hechos en Obsidian se sincronizarían hacia SQLite sin escribir nada
devanalyzer vault <ruta-proyecto> --dry-run

# Solo sincronizar ediciones de Obsidian hacia SQLite sin regenerar notas
devanalyzer vault <ruta-proyecto> --sync-only
```

**Opciones:**
- `--out`, `-o`: Ruta raíz del vault.
- `--folder`: Subcarpeta dentro del vault donde colocar las notas de este proyecto.
- `--include-rejected`: Incluye en el vault las reglas con estado `rejected` (por defecto se omiten).
- `--sync / --no-sync`: Sincroniza (por defecto activo) los cambios de `status`, `statement` y `review_note` hechos en Obsidian antes de regenerar.
- `--sync-only`: Solo ejecuta la sincronización Obsidian → SQLite.
- `--dry-run`: Muestra qué reglas cambiarían en la base de datos sin modificar archivos.

---

### 5.6 `watch` — Modo incremental en tiempo real

Observa el directorio del proyecto, reutiliza el caché de AST para re-parsear únicamente los archivos modificados, actualiza de forma atómica `analysis.json`, `graph.json` y las notas afectadas del vault, y mueve a `90 Archive/` las notas de clases o endpoints eliminados (conservando tus notas manuales).

```bash
# Observar continuamente cada 1 segundo
devanalyzer watch <ruta-proyecto>

# Ejecutar un único ciclo incremental y salir (útil en hooks de Git o scripts)
devanalyzer watch <ruta-proyecto> --once

# Ajustar intervalos o desactivar la actualización del vault
devanalyzer watch <ruta-proyecto> --interval 2.0 --debounce 0.5 --no-vault
```

---

### 5.7 `report`, `snapshot` y `diff` — Informes y control de cambios

#### `devanalyzer report`
Genera un informe estático autocontenido (un solo archivo HTML con estilos y diagrama SVG interactivo, o bien en Markdown) listo para compartir sin necesidad de Obsidian.

```bash
devanalyzer report <ruta-proyecto>                          # Genera .devanalyzer/report.html
devanalyzer report <ruta-proyecto> --format md -o DOC.md    # Genera DOC.md
```

#### `devanalyzer snapshot` y `devanalyzer diff`
Guarda una línea base estructural y compárala después de una refactorización o un PR para detectar clases, métodos, endpoints, dependencias o versiones añadidas/eliminadas.

```bash
# Guardar línea base
devanalyzer snapshot <ruta-proyecto>

# Comparar el código actual contra la línea base (y opcionalmente actualizarla con --update)
devanalyzer diff <ruta-proyecto> [--baseline otra_base.json] [--update]
```

---

### 5.8 `workspace` — Sistemas multi-repositorio y microservicios

Analiza varios proyectos a la vez y descubre cómo se comunican entre sí mediante:
- **Llamadas HTTP** (clientes REST, `RestTemplate`, `WebClient`, `@FeignClient`, `fetch`/`axios`, rutas `lb://` de Spring Cloud Gateway, variables `baseUrl`).
- **Eventos y colas** (`@KafkaListener`/`KafkaTemplate`, `@RabbitListener`/`RabbitTemplate`, SQS, `@Incoming`/`@Outgoing`, `@EventPattern` en NestJS).
- **Tablas de base de datos compartidas** entre servicios.

```bash
# Pasando una carpeta padre cuyos subdirectorios son repositorios/servicios
devanalyzer workspace C:/Proyectos/mi-ecosistema
```

También puedes definir un archivo `devanalyzer.workspace.json` si los repositorios están en rutas arbitrarias:

```json
{
  "name": "plataforma-pagos",
  "projects": [
    {"name": "gateway", "path": "../api-gateway"},
    {"name": "orders", "path": "../orders-service"},
    {"name": "billing", "path": "../billing-service"}
  ]
}
```

Genera `<dir>/.devanalyzer/workspace.json` y `workspace.md` (con el mapa Mermaid de arquitectura entre servicios). Además, al regenerar el `vault` de cualquiera de los servicios miembros, se incluye automáticamente la nota `00 MOC/Related Services.md`.

---

### 5.9 `serve` — Servidor MCP para agentes IA

Levanta un servidor compatible con el **Model Context Protocol (MCP)** para que asistentes como Claude Desktop, Cursor, Windsurf o Devin consulten directamente la arquitectura, flujos y reglas de negocio del proyecto.

```bash
# Modo stdio (por defecto para clientes de escritorio)
devanalyzer serve <ruta-proyecto>

# Modo HTTP / Server-Sent Events (SSE)
devanalyzer serve <ruta-proyecto> --sse --host 127.0.0.1 --port 8765
```

#### Tools MCP disponibles

| Tool | Descripción |
|---|---|
| `get_project_summary` | Resumen de lenguajes, build systems, versiones, frameworks, métricas y estadísticas del grafo. |
| `list_code_units` | Lista clases/interfaces/enums/módulos filtrando por `stereotype`, `kind` o `package`. |
| `get_code_unit` | Detalle completo de una unidad (métodos, campos, relaciones entrantes/salientes, endpoints, reglas y código fuente opcional). |
| `query_graph` | Subgrafo de vecinos hasta `depth` saltos, filtrable por tipo de relación y dirección (`in`/`out`/`both`). |
| `find_path` | Camino más corto en el grafo entre dos unidades del proyecto. |
| `list_endpoints` | Lista endpoints HTTP filtrando por verbo (`GET`, `POST`…) o subcadena de la ruta. |
| `list_dependencies` | Lista dependencias de terceros por scope o nombre. |
| `get_flows` | Flujos de caso de uso end-to-end por endpoint: capas atravesadas, tablas tocadas, si muta estado (`writes`) y reglas implicadas. |
| `get_workspace_links` | Otros microservicios con los que este proyecto se comunica (HTTP, eventos, tablas compartidas). |
| `list_tables` | Tablas de base de datos reconstruidas desde migraciones/esquemas y sus entidades ORM. |
| `get_business_rules` | Consulta reglas de negocio filtrando por `status`, `category`, `unit` o texto libre. |
| `review_business_rule` | Cambia el estado de una regla (`confirmed` / `rejected` / `inferred`) y añade nota de revisión humana. |
| `search_knowledge` | Búsqueda por palabra clave en unidades de código, endpoints, reglas y glosario. |
| `semantic_search` | Búsqueda semántica por significado usando los embeddings de `devanalyzer index`. |
| `get_glossary` | Consulta el glosario de términos de dominio. |
| `read_source` | Lee líneas numeradas de cualquier archivo del proyecto de forma segura. |
| `refresh_analysis` | Re-ejecuta el análisis estático y actualiza el grafo en caliente sin reiniciar el servidor MCP. |

---

### 5.10 `llm-ping` — Prueba de conexión con el LLM

Verifica que las credenciales o el servidor Ollama respondan correctamente antes de lanzar una extracción larga:

```bash
devanalyzer llm-ping
```

---

### 5.11 `publish` — Bundles para sitio estático

Genera artefactos consumibles por un frontend estático sin ejecutar LLM en CI.

#### `devanalyzer publish bundle`

Analiza un proyecto y escribe `manifest.json`, `analysis.json`, grafo, reportes y un vault comprimido. Puede incorporar reglas o una KB existente y comparar contra un snapshot anterior.

```bash
devanalyzer publish bundle C:/Proyectos/api --out bundle --slug api \
  --repo-url https://github.com/acme/api --ref main --commit abc123

devanalyzer publish bundle C:/Proyectos/api -o bundle \
  --rules site/rules/api.json --baseline publicacion-anterior/analysis.json
```

Opciones principales: `--slug`, `--repo-url`, `--ref`, `--commit`, `--subdir`, `--rules`, `--kb`, `--baseline` y `--no-graphml`.

#### `devanalyzer publish index`

Reconstruye `projects.json` leyendo los manifests bajo `p/<slug>/`.

```bash
devanalyzer publish index site/public/data
```

#### `devanalyzer publish site`

Procesa todos los proyectos de `repos.json`, continúa si falla uno y genera el índice global. `--extra` añade un repo solo a esa ejecución; `--baseline-url` habilita diffs contra la publicación anterior.

```bash
devanalyzer publish site site/repos.json --out site/public/data
devanalyzer publish site site/repos.json -o site/public/data \
  --extra owner/otro-repo@develop \
  --baseline-url https://owner.github.io/repo/data \
  --work-dir .devanalyzer-publish
```

Usa `--no-graphml` para omitir GraphML. Con `"workspace": true`, también publica `workspace.json` y `workspace.md`.

---

## 6. Estructura de artefactos generados (`.devanalyzer/`)

Todo lo que genera DevAnalyzer se guarda por defecto dentro de `<proyecto>/.devanalyzer/` (carpeta ignorada automáticamente en futuros escaneos):

```
<proyecto>/.devanalyzer/
├── analysis.json          # Último snapshot estructural completo (ProjectAnalysis)
├── graph.json             # Grafo de conocimiento en formato node-link (NetworkX)
├── graph.graphml          # Grafo para abrir en Gephi / yEd
├── rules.json             # Resultado crudo de la última corrida de extracción de reglas
├── kb.sqlite              # Base de conocimiento SQLite (reglas, historial, glosario, grupos, decisiones, embeddings)
├── report.html            # Reporte HTML interactivo autocontenido
├── cache/                 # Caché de respuestas del LLM (<sha256>.json)
└── vault/                 # Vault de Obsidian
    ├── <Proyecto> - Home.md
    ├── 00 MOC/            # Índices: Architecture, Business Rules, Glossary, Data Model, Use Case Flows, Related Services
    ├── 10 Modules/        # Notas por manifiesto de build / submódulo
    ├── 20 Classes/        # Una nota por clase/interfaz/módulo con métodos, relaciones y reglas
    ├── 25 Endpoints/      # Una nota por endpoint con diagrama de secuencia Mermaid
    ├── 30 Business Rules/ # Una nota por regla con evidencia de código y bloque de estado
    ├── 35 Domains/        # Resúmenes consolidados por módulo con diagrama de clases y reglas transversales
    ├── 40 Glossary/       # Términos del dominio y dónde aparecen
    ├── 50 Decisions/      # Registros de decisiones de arquitectura (ADRs)
    ├── 60 Data/           # Tablas de BD con columnas, claves foráneas y entidades
    ├── 70 Flows/          # Casos de uso end-to-end por endpoint con diagrama de flujo Mermaid
    ├── 90 Archive/        # Notas archivadas por `watch` cuando una unidad se elimina del código
    └── canvas/
        └── architecture.canvas  # Lienzo visual nativo de Obsidian agrupado por estereotipo
```

---

## 7. Trabajo con el Vault de Obsidian (Edición y Sincronización)

Cada nota `.md` generada tiene esta estructura:

```markdown
---
type: business-rule
rule_id: BR-8f3c12a901
status: inferred
statement: El monto de retiro no puede superar el saldo disponible de la cuenta.
review_note: ""
...
---
<!-- devanalyzer:generated:start -->
# BR-8f3c12a901
... (contenido autogenerado) ...
<!-- devanalyzer:generated:end -->

## Notes

<!-- Anything outside the generated block is yours and will be preserved. -->
Aquí puedes escribir tus propias observaciones, enlaces o comentarios.
```

1. **Preservación de notas manuales:** Todo lo que escribas **fuera** de `<!-- devanalyzer:generated:start --> ... <!-- devanalyzer:generated:end -->` se conserva intacto cada vez que regeneras el vault.
2. **Revisión de reglas desde Obsidian:** Si en una nota de `30 Business Rules/` editas en el frontmatter YAML:
   - `status: confirmed` (o `rejected`),
   - `statement: Nuevo enunciado corregido por negocio`, o
   - `review_note: Confirmado en sesión con producto`,
   al ejecutar `devanalyzer vault <ruta>` esos cambios se vuelcan primero a `kb.sqlite` como revisión humana (`reviewed_by = 'human'`) y nunca serán pisados por el LLM en corridas posteriores.

---

## 8. Solución de problemas frecuentes

| Problema | Causa probable | Solución |
|---|---|---|
| `ConnectionError` al correr `rules extract` o `llm-ping` | Ollama no está corriendo o el modelo no está descargado. | Inicia Ollama (`ollama serve`) y descarga el modelo: `ollama pull qwen2.5-coder:7b`. |
| `No embeddings yet; run devanalyzer index first` | Se ejecutó `devanalyzer search` sin haber indexado el proyecto. |Descarga el modelo de embeddings (`ollama pull nomic-embed-text`) y ejecuta `devanalyzer index <ruta>`. |
| `Install the optional dependency: pip install 'devanalyzer[mcp]'` | El entorno no tiene instalado el extra `mcp`. | Ejecuta `pip install -e ".[mcp]"` o `pip install -e ".[dev]"`. |
| Un modelo local 7B tarda mucho en `rules extract` | Por defecto procesa todas las clases candidatas secuencialmente. | Acota con `--stereotype service` o `--limit 10`, o usa un proveedor cloud con `-w 4`. En la segunda corrida la caché hace que solo se procesen las clases modificadas. |
| En consola de Windows los nombres largos salen truncados con `…` | `Rich` ajusta el ancho de las tablas al ancho de la ventana de terminal. | Amplía la ventana de terminal o usa `--json salida.json` en `analyze` / `rules eval`. |

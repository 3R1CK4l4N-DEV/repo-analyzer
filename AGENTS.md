# Guía para agentes

DevAnalyzer analiza proyectos Java/Kotlin, JavaScript/TypeScript y Python.
La CLI Python produce análisis, grafos, reportes, reglas y vaults de Obsidian.
El sitio Vite en `site/` publica esos artefactos como GitHub Pages.

## Verificación Python

```bash
pytest -q
ruff check src tests
ruff format --check src tests
```

## Verificación del sitio

```bash
cd site
npm ci
npm run typecheck
npm test
npm run build
```

Regenera los datos de desarrollo con:

```bash
cd site
npm run data
```

## Convenciones

- Ruff usa `line-length = 160`.
- No añadas dependencias sin justificar su necesidad.
- La interfaz del sitio se escribe en español.
- `site/public/data` es contenido generado y no se edita a mano.

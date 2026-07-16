# AI Context Hub — demo

Backend FastAPI que junta dos fuentes de contexto para los agentes IA de los
devs de Real Plaza:

- **Grafo de código** — motor: binario `codebase-memory-mcp` ya instalado en
  `%LOCALAPPDATA%\Programs\codebase-memory-mcp\` (`cbm.py` lo invoca por
  subproceso; nunca se toca directo desde el frontend).
- **Lineamientos oficiales** — el clon git de `Wiki-Arquitectura.wiki`
  (`wiki.py` hace `git pull` + indexa SOLO `Arquetipos/Microservicio` en
  SQLite FTS5, y sirve las reglas **verbatim**, sin resumir).

## Arrancar

```powershell
cd context-hub
uv run uvicorn main:app --reload --port 8000
```

Abrir `http://localhost:8000`.

Para el endpoint `/audit` (auditoría con Claude) se necesita:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

## Estructura

- `main.py` — FastAPI: rutas `/repos`, `/wiki/*`, `/audit`, y sirve `static/`.
- `cbm.py` — wrapper subprocess sobre `codebase-memory-mcp cli <tool>`.
- `wiki.py` — sync git + índice FTS5 de la wiki de lineamientos.
- `repos_db.py` — persistencia SQLite de los repos registrados.
- `static/index.html` — frontend (vanilla JS, sin build step).

## Flujo

1. Registrar un repo (URL git o ruta local) → clona si hace falta → indexa
   con cbm en background → queda navegable en la tabla y con link al grafo 3D
   (`http://localhost:9749`, servido por el propio binario).
2. "Sincronizar wiki" trae los lineamientos oficiales y los deja buscables.
3. "Auditar" junta el código real del módulo (grafo) con las reglas oficiales
   (wiki) y le pide a Claude que liste incumplimientos con archivo:línea.

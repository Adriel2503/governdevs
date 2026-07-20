# AI Context Hub — demo

Backend FastAPI (+ MCP server montado en el mismo proceso) que junta dos
fuentes de contexto para los agentes IA de los devs de Real Plaza:

- **Grafo de código** — motor: binario `codebase-memory-mcp` (`app/graph_engine.py`
  lo invoca por subproceso; nunca se toca directo desde el frontend). Los repos
  registrados por URL se clonan **solo temporalmente**: cbm indexa el grafo
  (nodos/aristas/metadatos, persistido en su propio store) y el working tree
  clonado se borra apenas termina — no nos interesa conservar el código, solo
  el grafo. Consecuencia: `get_code_snippet` (que cbm relee del disco en cada
  llamada, no lo guarda en su store) deja de devolver `source` para esos repos
  una vez borrado el clon; `get_architecture`, `search_graph` y `trace_path`
  siguen funcionando porque viven en el store de cbm.
- **Lineamientos oficiales** — `wiki_data/Microservicio/` bundleado dentro de
  este repo (congelado, sin git en runtime). `app/reglas.py` indexa esos `.md` en
  SQLite FTS5 y sirve las reglas **verbatim**, sin resumir. Para actualizar
  contenido: reemplazar los `.md` en `wiki_data/Microservicio/` y redeployar
  (o pisarlos a mano y llamar a `POST /wiki/sync` para reindexar sin rebuild).

## Estructura

```
app/                    paquete Python — todo el código vive acá
├── config.py           única fuente de verdad de las env vars (pydantic Settings)
├── main.py             FastAPI: rutas /repos, /wiki/*, /audit; monta el MCP y static/
├── mcp_server.py        servidor MCP (FastMCP): tools de grafo + reglas, sin LLM propio
├── graph_engine.py      wrapper subprocess sobre `codebase-memory-mcp cli <tool>`
├── reglas.py             índice FTS5 de wiki_data/Microservicio/
└── db.py                 persistencia SQLite de los repos registrados
static/index.html       frontend (vanilla JS, sin build step)
wiki_data/Microservicio/  los .md de lineamientos oficiales, congelados (versionado en git)
data/                    repos.db + wiki_index.db (gitignored, persistir por volumen)
workspace/               clones temporales durante indexado (efímero, no persistir)
Dockerfile / .dockerignore  imagen para Dokploy
```

## Arrancar en local

```powershell
cd context-hub
uv run uvicorn app.main:app --reload --port 8000
```

Abrir `http://localhost:8000`.

Variables de entorno: ver `.env.example` (copiarlo a `.env` si hace falta
pisar algún default). Todas están centralizadas en `app/config.py` —
no hay lectura de `os.environ` suelta en ningún otro archivo.

Para el endpoint `/audit` (auditoría con Claude, apagado por defecto):

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

## Desplegar en Dokploy

El repo trae `Dockerfile` listo (Python 3.12 + `uv` + binario Linux oficial
de `codebase-memory-mcp`, variante UI, descargado en build time). En Dokploy:
crear una app de tipo **Dockerfile**, apuntarla a este directorio y configurar:

**Variables de entorno**

| Variable | Para qué | Default si se omite |
|---|---|---|
| `GRAPH_UI_PUBLIC_URL` | URL pública desde la que Dokploy expone el puerto 9749 (el grafo 3D). **Obligatoria en prod** — sin esto, el botón "Ver grafo" redirige a `localhost` de la laptop del usuario, no del servidor. | `http://localhost:9749` (solo sirve en local) |
| `ANTHROPIC_API_KEY` | Solo si se quiere habilitar `/audit` (REST). El camino MCP no la necesita. | no seteada → `/audit` responde 500 |
| `CBM_BIN` | Ruta al binario cbm. | ya seteada en el Dockerfile |
| `WIKI_MICROSERVICIO_DIR` | Carpeta con los `.md` de lineamientos. | ya resuelve sola (relativa al paquete) |

**Puertos**

- `8000` — la app (frontend + REST + MCP en `/mcp/`).
- `9749` — la UI 3D del grafo, servida por el propio binario cbm. Hay que
  exponerlo aparte en Dokploy (dominio o puerto propio) y apuntar
  `GRAPH_UI_PUBLIC_URL` a esa URL pública.

**Volumen persistente (recomendado)**

Sin volumen, cada redeploy empieza de cero: hay que re-registrar repos y se
pierde `data/repos.db` (el índice de reglas se puede regenerar solo desde
`wiki_data/`, que sí viaja en la imagen). Montar un volumen sobre:

```
/app/data
```

(`/app/workspace/` NO hace falta persistirlo — los clones ahí son temporales
y se autoborran tras indexar.)

**Conectar el MCP desde Claude Code (una vez desplegado):**

```bash
claude mcp add --transport http realplaza-context --scope user https://<tu-dominio-dokploy>/mcp
```

`/mcp` redirige con 307 (preserva método y body) a `/mcp/`, que es donde el
sub-app MCP responde por el recorte de prefijo del Mount de Starlette. Los
clientes MCP siguen el redirect de forma transparente, así que podés registrar
la URL estándar sin barra final (ver nota en `app/main.py`).

## Flujo

1. Registrar un repo (URL git o ruta local) → si es URL, clona temporalmente,
   indexa con cbm en background y borra el clon; si es ruta local, no se toca
   (es del usuario). Queda navegable en la tabla y con link al grafo 3D.
2. "Sincronizar wiki" reindexa los lineamientos bundleados.
3. Desde Claude Code (u otro cliente MCP) conectado al hub: `get_regla`,
   `search_graph`, `reunir_contexto_auditoria`, etc. — el agente conectado
   razona con sus propios hechos, sin depender de `ANTHROPIC_API_KEY`.

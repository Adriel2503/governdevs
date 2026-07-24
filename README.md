# AI Context Hub

Backend FastAPI (+ servidor MCP montado en el mismo proceso) que centraliza dos
fuentes de contexto y se las sirve a los agentes IA de los devs de Real Plaza
(Claude Code, Cursor, cualquier cliente MCP):

- **El grafo de código real** de los microservicios — qué existe, cómo se llama
  entre sí, qué impacta qué.
- **Los lineamientos oficiales de arquitectura** — las reglas por capa, servidas
  **verbatim**, sin resumir ni reinterpretar.

Sobre eso se monta un flujo tipo CI/CD contra GitHub: el admin registra el repo,
cada push a la rama canónica **reindexa el grafo solo**, y cada PR recibe una
**verificación arquitectónica automática comentada en GitHub**.

---

## Cómo funciona

### Las dos fuentes

**Grafo de código** — motor: el binario `codebase-memory-mcp` (cbm), invocado
por subproceso desde `app/graph_engine.py`. Nadie más lo toca: si mañana cambia
el motor, solo se reescribe ese archivo. Los repos registrados por URL se clonan
de forma **persistente** bajo `WORKSPACE_DIR/repos/` — el clon sobrevive entre
corridas porque es la base del `git fetch` incremental que dispara el webhook y
de que `detect_changes` pueda diffear ramas. Efecto secundario bueno:
`get_code_snippet` sigue devolviendo el código fuente.

**Lineamientos** — `.md` indexados con **BM25 en Postgres** (extensión
`pg_search` de ParadeDB). Se cargan por el importador desde una URL de GitHub o
un ZIP subido, eligiendo desde qué carpeta indexar; o se montan como volumen de
solo lectura y se reindexan con `POST /wiki/sync`.

> ⚠️ El contenido de la wiki es **interno de Real Plaza**: `wiki_data/` está en
> `.gitignore`, no se versiona y no viaja dentro de la imagen.

### El ciclo con GitHub

```
   admin                                          devs
     │                                              │
     │ 1. crea credencial (PAT cifrado)             │
     │ 2. registra el repo ────────────┐            │
     │                                 ▼            │
     │                     clon persistente         │
     │                     + indexado (cola)        │
     │                     + webhook auto-registrado│
     │                                              │
     │ 3. push a main ──► webhook ──► reindexa      │
     │                                              │
     │                        4. abre PR ◄──────────┤
     │                              │               │
     │                              ▼               │
     │                    detect_changes vs main    │
     │                    + lineamientos de las     │
     │                      capas tocadas           │
     │                              │               │
     │                              ▼               │
     │                    comentario con el         │
     │                    veredicto en el PR ──────►│
     │                                              │
     │                        5. consumen el grafo  │
     │                           vía MCP ◄──────────┤
```

**Verificación de rama (enfoque A).** El PR no genera un grafo temporal: se hace
fetch de `refs/pull/<N>/head` (funciona incluso para forks), se corre
`detect_changes` contra la rama base y **el checkout se restaura siempre**. El
grafo canónico de `main` nunca se altera — verificado midiendo `get_architecture`
antes y después. El worker único garantiza que ningún reindexado se cuele
mientras el clon está parado en la rama del PR.

---

## Estructura

```
app/                      todo el código Python
├── config.py             única fuente de verdad de las env vars (pydantic Settings)
├── main.py               FastAPI: rutas REST, monta el MCP y static/
├── mcp_server.py         servidor MCP (FastMCP): tools de grafo + reglas
├── graph_engine.py       wrapper subprocess sobre `codebase-memory-mcp cli`
├── pg.py                 pool de conexiones a Postgres (psycopg 3)
├── db.py                 persistencia de repos
├── reglas.py             índice BM25 (pg_search) de los lineamientos
├── importador.py         importar lineamientos desde GitHub o ZIP
├── cripto.py             cifrado Fernet de secretos
├── credenciales.py       PAT cifrados — punto de extensión hacia GitHub App
├── git_repo.py           operaciones git (clone/fetch/checkout/diff)
├── github_api.py         API de GitHub (webhooks, comentarios en PR)
├── cola.py               cola FIFO in-memory + worker
├── webhook.py            receptor de webhooks (HMAC, filtros, ruteo)
└── verificacion.py       verificación arquitectónica de ramas en PR
migrations/schema.sql     esquema completo, idempotente (6 tablas + índice BM25)
static/                   frontend vanilla JS, sin build step
tests/                    36 tests (pytest)
compose.yaml              app + ParadeDB para levantar todo local
Dockerfile                imagen para Dokploy
```

### Modelo de datos

| Tabla | Para qué |
|---|---|
| `capas` | Las capas de la arquitectura (endpoints, handlers, queries…) |
| `lineamientos` | Los `.md` de reglas + índice BM25; versionado por `es_vigente` |
| `git_credentials` | PAT **cifrados**; discriminador `tipo` (`pat` / `github_app`) |
| `repos` | Repos vigilados: rama, `watch_paths`, clon, último commit indexado |
| `index_jobs` | Historial de reindexados; único por `(repo, commit)` → idempotencia |
| `revisiones` | Verificaciones de PR y su veredicto |

---

## Arrancar en local

### Todo con Docker (recomendado)

```powershell
cd context-hub
docker compose up --build
```

Levanta ParadeDB (con el esquema aplicado automáticamente en el primer arranque)
y la app. Abrir `http://localhost:8000`.

### Solo la app, contra un Postgres propio

```powershell
cd context-hub
uv run uvicorn app.main:app --reload --port 8000
```

Necesita `DATABASE_URL` apuntando a un Postgres **con `pg_search`** (ParadeDB) y
el esquema ya aplicado:

```powershell
docker run -d --name paradedb -p 5433:5432 `
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=context_hub paradedb/paradedb:latest-pg17
Get-Content migrations/schema.sql | docker exec -i paradedb psql -U postgres -d context_hub
```

### Tests

```powershell
uv run pytest
```

Los tests que necesitan Postgres se saltean solos si no hay `DATABASE_URL`; el
resto (firma HMAC, cliente de GitHub con transporte simulado, cola, git) corre
sin red y sin base.

---

## Variables de entorno

Todas centralizadas en `app/config.py` — no hay lectura de `os.environ` suelta
en ningún otro archivo. Ver `.env.example` para la referencia completa.

**Obligatorias en producción**

| Variable | Para qué |
|---|---|
| `DATABASE_URL` | Postgres/ParadeDB. En Dokploy usar el **Internal Host**, no la IP pública. |
| `CREDENTIALS_KEY` | Clave Fernet que cifra los PAT y los secretos de webhook. **Si se pierde, las credenciales guardadas no se pueden descifrar.** |
| `PUBLIC_BASE_URL` | URL pública de la app; con ella se arma la URL del webhook que se registra en GitHub. |
| `GRAPH_UI_PUBLIC_URL` | URL pública de la UI 3D del grafo (puerto 9749). Sin esto, "Ver grafo" apunta al `localhost` del navegador del usuario. |
| `WORKSPACE_DIR` | Debe caer dentro del volumen (`/app/data/workspace`) o los clones se pierden en cada redeploy. |

**Opcionales**

| Variable | Para qué |
|---|---|
| `ANTHROPIC_API_KEY` | Veredicto automático en los PR y endpoint `/audit`. Sin ella la verificación igual corre y comenta cambios, impacto y reglas aplicables — pero sin juicio automático. |
| `WIKI_MICROSERVICIO_DIR` | Carpeta de lineamientos montada de solo lectura. |
| `CBM_BIN`, `CBM_UI_PORT`, `CBM_CLI_TIMEOUT`, `CBM_INDEX_TIMEOUT` | Ajustes del motor de grafo; ya vienen resueltos. |

Generar la clave de cifrado:

```powershell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Desplegar en Dokploy

1. **Base de datos** — crear un Postgres con imagen `paradedb/paradedb:latest-pg17`
   y aplicarle `migrations/schema.sql` una vez.
2. **App** — tipo *Dockerfile*, apuntada a este repo.
3. **Variables** — las cinco obligatorias de arriba.
4. **Dominios** — dos:
   - `8000` → la app (frontend + REST + MCP en `/mcp/`).
   - `9749` → la UI 3D del grafo, servida por el propio binario cbm.
5. **Volumen** — montar sobre `/app/data`. Ahí viven el store del grafo y los
   clones persistentes; sin volumen, cada redeploy arranca de cero.
6. **Lineamientos** — montar `wiki_data/` de solo lectura, o cargarlos desde el
   panel "Lineamientos oficiales" del frontend.

**Conectar el MCP desde Claude Code:**

```bash
claude mcp add --transport http realplaza-context --scope user https://<dominio>/mcp
```

`/mcp` redirige con 307 (preserva método y body) a `/mcp/`, que es donde
responde el sub-app por el recorte de prefijo del Mount de Starlette.

---

## API

| Endpoint | Qué hace |
|---|---|
| `POST /credenciales` · `GET` · `DELETE /{id}` | PAT cifrados. El token **nunca** vuelve por la API. |
| `POST /repos` | Registra un repo: clona, encola el indexado y auto-registra el webhook. |
| `GET /repos` · `/{name}/status` · `/{name}/arch` · `/{name}/graph-ui` | Estado y navegación del grafo. |
| `GET /repos/{name}/jobs` · `/revisiones` | Historial de reindexados y de verificaciones de PR. |
| `DELETE /repos/{name}` | Borra grafo, webhook en GitHub y clon local. |
| `POST /webhooks/github` | Receptor: HMAC sobre el **raw body**, ruteo push/PR. |
| `POST /wiki/import/{git,zip,index}` | Importar lineamientos y elegir la carpeta a indexar. |
| `POST /wiki/sync` · `GET /wiki/reglas` · `/wiki/buscar` | Reindexar y consultar lineamientos (BM25). |
| `POST /audit` | Auditoría de un módulo con Claude (requiere API key). |

### Tools MCP

`list_repos`, `get_architecture`, `search_graph`, `trace_path`,
`get_code_snippet`, `list_reglas`, `get_regla`, `buscar_regla`,
`reunir_contexto_auditoria`.

El agente conectado razona con sus propios hechos: el camino MCP **no** necesita
`ANTHROPIC_API_KEY`.

---

## Decisiones de seguridad

- **Secretos cifrados en reposo** (Fernet). La clave vive en el entorno, nunca en
  la base: si se filtra Postgres, no hay tokens en claro.
- **HMAC sobre el cuerpo crudo**, no sobre un JSON re-serializado — un
  re-serializado puede no coincidir byte a byte y tirar abajo firmas válidas.
- **La búsqueda del repo y la autenticación son el mismo paso**: gana el repo
  cuyo secreto valida la firma del payload.
- **El token no queda en `.git/config`**: se usa para clonar y se reescribe el
  remote sin credencial.
- **Nunca 500 hacia GitHub**: 200 = procesado o nada que hacer, 400 = payload o
  evento inválido, 401 = firma. El detalle del error se queda del lado servidor.
- **`token_para_clonar()`** es la única puerta por la que se pide un token — el
  punto exacto donde se enchufa una GitHub App sin tocar el resto del sistema.

### Dónde vive el código fuente

Los clones son **persistentes y completos** (no shallow) porque el enfoque A lo
exige: el webhook hace `fetch` incremental y `detect_changes` necesita la
historia git en disco para diffear la rama del PR contra `main`.

Consecuencia: **el código fuente queda en el disco del servidor donde corre el
hub**, y ese disco pasa a ser información confidencial — misma categoría que
`wiki_data/`. Quien accede al servidor accede al código.

Alternativa ya implementada pero limitada: registrar un repo por **ruta local**
(`source` = un directorio, no una URL). No clona nada y nunca borra ese árbol —
pero pierde el flujo automático, porque GitHub necesita llegar a una URL pública
y la verificación del PR tiene que correr aunque el dev no esté.

Para un despliegue con repos reales, la recomendación es correr el hub **dentro
de la infraestructura de Real Plaza**: mismo `Dockerfile`, mismo `compose.yaml`,
y el código nunca se copia a un servidor de terceros.

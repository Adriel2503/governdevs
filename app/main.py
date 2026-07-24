"""AI Context Hub — backend de la demo.

Un solo proceso FastAPI que:
  - registra repos (URL git o ruta local), los indexa con el binario cbm
    (codebase-memory-mcp) y expone su grafo/arquitectura
  - sirve los lineamientos oficiales de la Wiki-Arquitectura (verbatim + BM25)
  - /audit junta grafo + lineamientos para auditar un módulo con Claude

cbm y la wiki quedan totalmente detrás de esta capa: el frontend nunca los
toca directo. Si el motor de grafo cambia mañana, solo graph_engine.py se
reescribe.
"""

import json
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import anthropic
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cola, credenciales, cripto
from . import db as repos_db
from . import git_repo, github_api
from . import graph_engine as cbm
from . import importador
from . import reglas as wiki
from . import verificacion
from . import webhook as gh_webhook
from .config import settings
from .mcp_server import mcp as mcp_server

# Capas de lineamiento que solemos cruzar en una auditoría de módulo CQRS
_CAPAS_AUDITORIA = ["endpoints", "ruteo", "handlers", "queries", "validators", "custom-exceptions"]

WORKSPACE = Path(settings.workspace_dir)
# parents=True: WORKSPACE_DIR apunta adentro del volumen (/app/data/workspace) y
# el padre puede no existir todavía en un deploy nuevo. Sin esto, la app no
# arranca por un directorio faltante.
WORKSPACE.mkdir(parents=True, exist_ok=True)

# Clones PERSISTENTES de los repos vigilados. A diferencia de la versión previa
# (que borraba el working tree tras indexar), estos sobreviven: son la base del
# fetch incremental que dispara el webhook y de que detect_changes pueda diffear
# ramas. Efecto secundario bueno: get_code_snippet sigue devolviendo el código.
REPOS_DIR = WORKSPACE / "repos"

# La UI 3D la sirve el propio binario cbm en su propio puerto dentro del
# contenedor. Redirigir al navegador del cliente a "localhost:<puerto>" solo
# funciona en desarrollo local (el navegador ahí SÍ es la misma máquina que el
# servidor); en producción el cliente está en otra red y "localhost" apunta a
# su propia laptop. GRAPH_UI_PUBLIC_URL debe ser la URL pública desde la que
# Dokploy expone ese puerto (dominio/puerto propio mapeado al mismo contenedor).
CBM_UI_PORT = settings.cbm_ui_port
GRAPH_UI_PUBLIC_URL = settings.graph_ui_url

# FastMCP.http_app() devuelve una sub-app Starlette con su propio lifespan
# (arranca el session manager de Streamable HTTP). FastAPI solo invoca UN
# lifespan — mezclar esto con @app.on_event("startup") haría que ese último
# nunca corra — así que combinamos ambos arranques en un único lifespan.
mcp_asgi_app = mcp_server.http_app(path="/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cbm.ensure_ui_running(CBM_UI_PORT)
    cola.arrancar_worker()  # consume los reindexados que dispara el webhook
    async with mcp_asgi_app.lifespan(app):
        yield


app = FastAPI(title="AI Context Hub", lifespan=lifespan)


# El sub-app MCP se monta bajo /mcp, y por cómo Starlette recorta el prefijo del
# Mount solo responde con la barra final (/mcp/). Para poder registrar el MCP con
# la URL estándar /mcp, redirigimos /mcp → /mcp/ con 307 (preserva método y body,
# a diferencia de 301/302). Debe ir ANTES del mount para ganar el match exacto;
# /mcp/ y /mcp/... siguen cayendo directo en el mount. Cubre GET/POST/DELETE
# porque el transporte Streamable HTTP de MCP usa los tres sobre el mismo endpoint.
@app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
def _mcp_trailing_slash():
    return RedirectResponse("/mcp/", status_code=307)


app.mount("/mcp", mcp_asgi_app)


class RegisterRepoRequest(BaseModel):
    source: str  # URL git (https://... o git@...) o ruta local absoluta
    name: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$",
        description="Slug seguro para usar en rutas: letras, números, guion y guion bajo.",
    )
    credential_id: str | None = Field(
        default=None, description="Credencial para clonar repos privados (ver /credenciales)."
    )
    rama: str = Field(default="main", description="Rama canónica vigilada.")
    watch_paths: list[str] = Field(
        default_factory=list,
        description="Globs; si se define, solo se reindexa cuando cambian estos archivos.",
    )


def _is_git_url(source: str) -> bool:
    return source.startswith(("http://", "https://", "git@"))


def _derive_name(source: str) -> str:
    stem = source.rstrip("/").rsplit("/", 1)[-1]
    stem = re.sub(r"\.git$", "", stem)
    return re.sub(r"[^a-zA-Z0-9_-]", "-", stem).lower() or "repo"


def _auto_registrar_webhook(name: str, source: str, token: str | None) -> str:
    """Crea el webhook en GitHub para que los push/PR lleguen solos.

    Degrada con gracia: si falla (token sin permiso Webhooks:write, sin URL
    pública, etc.) el repo queda igualmente registrado y con su grafo — solo se
    pierde la actualización automática. Se informa el motivo en la respuesta.
    """
    if not token:
        return "omitido: el repo se registró sin credencial"
    if not settings.public_base_url:
        return "omitido: falta PUBLIC_BASE_URL (GitHub necesita una URL pública)"
    try:
        owner, repo = github_api.parse_owner_repo(source)
        secret = secrets.token_hex(32)
        hook_id = github_api.crear_webhook(
            owner,
            repo,
            token,
            url=f"{settings.public_base_url.rstrip('/')}/webhooks/github",
            secret=secret,
        )
        repos_db.guardar_webhook(name, hook_id, cripto.cifrar(secret))
        return f"registrado (id {hook_id})"
    except (github_api.GitHubError, RuntimeError) as e:
        return f"error: {e}"


@app.post("/repos")
def register_repo(req: RegisterRepoRequest):
    name = req.name or _derive_name(req.source)
    token = None

    if _is_git_url(req.source):
        if req.credential_id:
            try:
                token = credenciales.token_para_clonar(req.credential_id)
            except credenciales.CredencialError as e:
                raise HTTPException(400, str(e))

        local_path = REPOS_DIR / name
        try:
            if local_path.exists():
                git_repo.actualizar(local_path, req.source, token, req.rama)
            else:
                git_repo.clonar(req.source, token, local_path, req.rama)
        except git_repo.GitError as e:
            raise HTTPException(400, str(e))
    else:
        local_path = Path(req.source)
        if not local_path.is_dir():
            raise HTTPException(400, f"La ruta local no existe: {req.source}")

    repos_db.upsert(
        name,
        req.source,
        str(local_path),
        status="registrado",
        credential_id=req.credential_id,
        rama=req.rama,
        watch_paths=req.watch_paths,
    )

    webhook = (
        _auto_registrar_webhook(name, req.source, token)
        if _is_git_url(req.source)
        else "omitido: no es un repo git"
    )
    # El indexado inicial va por la MISMA cola que los reindexados del webhook:
    # un solo camino, historial completo en index_jobs y cero indexados en
    # paralelo sobre el mismo repo.
    job_id = cola.encolar(name, evento="registro_inicial")

    return {
        "name": name,
        "local_path": str(local_path),
        "rama": req.rama,
        "status": "registrado",
        "webhook": webhook,
        "job_id": job_id,
    }


@app.get("/repos/{name}/jobs")
def repo_jobs(name: str):
    """Historial de reindexados (los que dispara el webhook y los manuales)."""
    if repos_db.get(name) is None:
        raise HTTPException(404, "Repo no registrado")
    return cola.listar_jobs(name)


@app.get("/repos/{name}/revisiones")
def repo_revisiones(name: str):
    """Historial de verificaciones de rama (una por PR abierto/actualizado)."""
    if repos_db.get(name) is None:
        raise HTTPException(404, "Repo no registrado")
    return verificacion.listar(name)


@app.get("/repos")
def list_repos():
    return repos_db.list_all()


@app.get("/repos/{name}/status")
def repo_status(name: str):
    repo = repos_db.get(name)
    if repo is None:
        raise HTTPException(404, "Repo no registrado")
    if repo["cbm_project"]:
        try:
            repo["cbm_status"] = cbm.index_status(repo["cbm_project"])
        except cbm.CbmError as e:
            repo["cbm_status"] = {"error": str(e)}
    return repo


@app.get("/repos/{name}/graph-ui")
def repo_graph_ui(name: str):
    """Redirige a la UI 3D que el propio binario cbm sirve (no reimplementamos
    visualización). Deep-link: la SPA de cbm lee ?project=<slug> para abrir ese
    grafo directo, en vez de caer en la home. Asegura el proceso vivo antes."""
    repo = repos_db.get(name)
    if repo is None or not repo["cbm_project"]:
        raise HTTPException(404, "Repo no indexado todavía")
    cbm.ensure_ui_running(CBM_UI_PORT)
    base = GRAPH_UI_PUBLIC_URL.rstrip("/")
    url = f"{base}/?tab=graph&project={quote(repo['cbm_project'])}"
    return RedirectResponse(url)


@app.delete("/repos/{name}")
def delete_repo(name: str):
    """Limpia el proyecto de cbm, el clon persistente y el registro. Solo se borra
    el working tree si lo gestionamos nosotros (está bajo REPOS_DIR): jamás se
    toca una ruta local que el usuario registró apuntando a su propio disco."""
    repo = repos_db.get(name)
    if repo is None:
        raise HTTPException(404, "Repo no registrado")

    if repo["cbm_project"]:
        try:
            cbm.delete_project(repo["cbm_project"])
        except cbm.CbmError:
            pass

    # Quitar el webhook de GitHub para no dejarlo huérfano apuntando acá.
    datos = repos_db.datos_webhook(name)
    if datos and datos.get("webhook_github_id") and repo.get("credential_id"):
        try:
            token = credenciales.token_para_clonar(repo["credential_id"])
            owner, r = github_api.parse_owner_repo(repo["source"])
            github_api.borrar_webhook(owner, r, token, datos["webhook_github_id"])
        except (github_api.GitHubError, credenciales.CredencialError, RuntimeError):
            pass  # el repo se borra igual; a lo sumo queda un hook inactivo

    local_path = repo.get("local_path")
    if local_path and Path(local_path).resolve().is_relative_to(REPOS_DIR.resolve()):
        git_repo.borrar_arbol(local_path)

    repos_db.delete(name)
    return {"deleted": name}


# --- Webhook de GitHub -----------------------------------------------------


@app.post("/webhooks/github", include_in_schema=False)
async def webhook_github(request: Request):
    """Recibe push/pull_request de GitHub. Se lee el cuerpo CRUDO porque la firma
    HMAC se calcula sobre esos bytes exactos: re-serializar el JSON puede no
    coincidir byte a byte y tirar abajo firmas válidas."""
    cuerpo = await request.body()
    firma = request.headers.get("x-hub-signature-256", "")
    evento = request.headers.get("x-github-event", "")
    status, body = gh_webhook.procesar(cuerpo, firma, evento)
    return JSONResponse(status_code=status, content=body)


# --- Credenciales git (PAT hoy, GitHub App en v3) --------------------------
# El token se guarda cifrado y NUNCA se devuelve: las respuestas solo llevan
# metadata. Todo el sistema pide el token vía credenciales.token_para_clonar().


class CrearCredencialRequest(BaseModel):
    alias: str = Field(description="Nombre reconocible, ej. 'Cuenta de Amado (demo)'")
    token: str = Field(description="Personal Access Token; se guarda cifrado")
    github_login: str | None = None


@app.post("/credenciales")
def crear_credencial(req: CrearCredencialRequest):
    try:
        cid = credenciales.crear_pat(
            alias=req.alias, token=req.token, github_login=req.github_login
        )
    except (credenciales.CredencialError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return {"id": cid, "alias": req.alias, "tipo": "pat"}


@app.get("/credenciales")
def listar_credenciales():
    return credenciales.listar()


@app.delete("/credenciales/{credential_id}")
def eliminar_credencial(credential_id: str):
    if not credenciales.eliminar(credential_id):
        raise HTTPException(404, "Credencial no encontrada")
    return {"deleted": credential_id}


# --- Lineamientos (wiki) ---------------------------------------------------


@app.post("/wiki/sync")
def wiki_sync():
    try:
        return wiki.sync()
    except wiki.WikiError as e:
        raise HTTPException(500, str(e))


@app.get("/wiki/fuentes")
def wiki_fuentes():
    """Los lotes de lineamientos importados, para poder quitarlos."""
    return wiki.listar_fuentes()


@app.delete("/wiki/fuentes")
def wiki_borrar_fuente(fuente: str):
    """Elimina los lineamientos de una importación.

    La fuente va como query param y no en la ruta porque contiene '/' y '#'
    (ej. 'wiki.zip#Lineamientos/Desarrollo/Microservicio'), que romperían el
    enrutado por path.
    """
    resultado = wiki.borrar_fuente(fuente)
    if not resultado["eliminados"]:
        raise HTTPException(404, f"No hay lineamientos de la fuente: {fuente}")
    return resultado


@app.get("/wiki/reglas")
def wiki_list_reglas():
    return wiki.list_reglas()


@app.get("/wiki/reglas/{capa}")
def wiki_get_regla(capa: str):
    regla = wiki.get_regla(capa)
    if regla is None:
        raise HTTPException(404, f"No hay regla para '{capa}'")
    return regla


@app.get("/wiki/buscar")
def wiki_buscar(q: str):
    return wiki.buscar(q)


# --- Import de lineamientos (repo GitHub / ZIP subido) ---------------------
# Flujo en dos pasos: (1) escanear una fuente → devuelve el árbol de carpetas;
# (2) indexar la carpeta raíz elegida → persiste solo eso y borra la descarga.


class ImportGitRequest(BaseModel):
    url: str  # URL del repo (GitHub; Azure DevOps en Fase 2)
    token: str | None = None  # PAT para repos privados; nunca se guarda ni loguea


class ImportIndexRequest(BaseModel):
    import_id: str
    carpeta: str = ""  # carpeta raíz a indexar (ej. "Microservicio"); "" = todo


@app.post("/wiki/import/git")
def wiki_import_git(req: ImportGitRequest):
    """Paso 1 (git): clona el repo a una carpeta temporal y devuelve el árbol de
    carpetas con conteo de .md para que el admin elija cuál indexar."""
    try:
        return importador.scan_git(req.url, req.token)
    except importador.ImportadorError as e:
        raise HTTPException(400, str(e))


@app.post("/wiki/import/zip")
async def wiki_import_zip(file: UploadFile = File(...)):
    """Paso 1 (ZIP): extrae los .md del archivo subido a una carpeta temporal y
    devuelve el árbol de carpetas para elegir la raíz a indexar.

    La subida se escribe a disco POR PARTES. Antes se hacía `await file.read()`,
    que carga el archivo entero en memoria: con un export de wiki de cientos de
    MB eso es un pico de RAM en un servidor que además corre Postgres y el motor
    de grafo. Así el consumo es constante sin importar el tamaño, y pasado el
    límite se corta con un 413 explícito en vez de morir de formas raras.
    """
    limite = settings.max_upload_mb * 1024 * 1024
    subidas = WORKSPACE / "uploads"
    subidas.mkdir(parents=True, exist_ok=True)
    tmp = subidas / f"{uuid.uuid4().hex}.zip"

    try:
        total = 0
        with tmp.open("wb") as salida:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > limite:
                    raise HTTPException(
                        413,
                        f"El archivo supera el límite de {settings.max_upload_mb} MB. "
                        "Solo se indexan los .md: podés subir un ZIP sin .git ni adjuntos.",
                    )
                salida.write(chunk)
        return importador.scan_zip(tmp, fuente=file.filename or "zip")
    except importador.ImportadorError as e:
        raise HTTPException(400, str(e))
    finally:
        tmp.unlink(missing_ok=True)  # el ZIP no se conserva: ya se extrajeron los .md


@app.post("/wiki/import/index")
def wiki_import_index(req: ImportIndexRequest):
    """Paso 2: indexa los .md de la carpeta elegida en `lineamientos` y borra la
    descarga temporal. Solo persiste en la BD lo de esa carpeta."""
    try:
        return importador.indexar(req.import_id, req.carpeta)
    except (importador.ImportadorError, wiki.WikiError) as e:
        raise HTTPException(400, str(e))


# --- Auditoría en vivo (grafo + lineamientos + LLM) ------------------------


class AuditRequest(BaseModel):
    repo: str
    modulo: str  # ej. "Products" — se busca por name_pattern en el grafo


class Finding(BaseModel):
    archivo: str
    linea: int | None = None
    regla_violada: str | None = None
    descripcion: str
    fix_sugerido: str | None = None
    severidad: str = "media"


class AuditResponse(BaseModel):
    findings: list[Finding]


def _reunir_snippets_modulo(cbm_project: str, modulo: str, max_clases: int = 8) -> list[dict]:
    """Grafo → hechos: ubica las clases del módulo y trae su código con
    procedencia (archivo:línea) directamente del grafo, no de un LLM."""
    hallazgos_grafo = cbm.search_graph(cbm_project, f".*{modulo}.*", label="Class")
    clases = hallazgos_grafo.get("results", [])[:max_clases]

    snippets = []
    for c in clases:
        try:
            snip = cbm.get_code_snippet(cbm_project, c["qualified_name"])
            snippets.append(snip)
        except cbm.CbmError:
            continue
    return snippets


def _reunir_reglas_relevantes() -> dict[str, str]:
    """Normas → las reglas oficiales (verbatim) de las capas que suelen tocar
    un feature CQRS completo."""
    reglas = {}
    for capa in _CAPAS_AUDITORIA:
        regla = wiki.get_regla(capa)
        if regla:
            reglas[regla["capa"]] = regla["contenido"]
    return reglas


@app.post("/audit", response_model=AuditResponse)
def audit_modulo(req: AuditRequest):
    repo = repos_db.get(req.repo)
    if repo is None or not repo["cbm_project"]:
        raise HTTPException(404, "Repo no registrado o aún no indexado")

    if not settings.anthropic_api_key:
        raise HTTPException(500, "Falta ANTHROPIC_API_KEY en el entorno del servidor")

    snippets = _reunir_snippets_modulo(repo["cbm_project"], req.modulo)
    if not snippets:
        raise HTTPException(404, f"El grafo no encontró clases para el módulo '{req.modulo}'")

    reglas = _reunir_reglas_relevantes()

    codigo_fmt = "\n\n".join(
        f"--- {s['file_path']}:{s['start_line']}-{s['end_line']} ({s['name']}) ---\n{s['source']}"
        for s in snippets
    )
    reglas_fmt = "\n\n".join(f"=== Regla oficial: {capa} ===\n{contenido}" for capa, contenido in reglas.items())

    prompt = f"""Eres un auditor de código senior de Real Plaza. Audita el siguiente código \
del módulo "{req.modulo}" del repositorio "{req.repo}" contra los lineamientos oficiales \
adjuntos. Lista SOLO incumplimientos reales (no inventes); cada uno con archivo, línea \
si aplica, la regla violada, una descripción breve y un fix sugerido. Ordena por severidad.

# Código del módulo (extraído del grafo real del repo, con procedencia archivo:línea)
{codigo_fmt}

# Lineamientos oficiales (verbatim de la wiki de arquitectura)
{reglas_fmt}

Responde EXCLUSIVAMENTE con un JSON: {{"findings": [{{"archivo": "...", "linea": N o null, \
"regla_violada": "...", "descripcion": "...", "fix_sugerido": "...", "severidad": "alta|media|baja"}}]}}"""

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = response.content[0].text

    try:
        data = json.loads(texto)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", texto, re.DOTALL)
        if not match:
            raise HTTPException(500, f"Claude no devolvió JSON parseable: {texto[:300]}")
        data = json.loads(match.group(0))

    return {"findings": data.get("findings", [])}


@app.get("/resumen")
def resumen():
    """Los números que responden '¿qué está gobernando esto?' de un vistazo.

    Existe para la pantalla de entrada: antes lo primero que se veía era un
    formulario de credenciales vacío, o sea la configuración en vez del valor.

    Todo el conteo sale de UNA consulta; los nodos y aristas del grafo se piden
    a cbm solo para los repos que ya están listos.
    """
    from . import pg

    with pg.conn() as c:
        fila = c.execute(
            """
            SELECT (SELECT count(*) FROM repos)                                        AS repos,
                   (SELECT count(*) FROM repos WHERE status = 'listo')                 AS repos_listos,
                   (SELECT count(*) FROM lineamientos WHERE es_vigente)                AS lineamientos,
                   -- De cuántas importaciones distintas vienen. Contar capas no
                   -- servía: desde que el slug sale de la ruta, cada documento es
                   -- su propia capa y el número siempre igualaba al de arriba.
                   (SELECT count(DISTINCT fuente) FROM lineamientos WHERE es_vigente) AS fuentes,
                   (SELECT count(*) FROM index_jobs WHERE estado = 'ok')               AS reindexados,
                   (SELECT count(*) FROM revisiones)                                   AS revisiones,
                   (SELECT count(*) FROM revisiones WHERE estado = 'viola')            AS revisiones_viola
            """
        ).fetchone()

    nodos = aristas = 0
    for repo in repos_db.list_all():
        if repo["status"] != "listo" or not repo["cbm_project"]:
            continue
        try:
            estado = cbm.index_status(repo["cbm_project"])
            nodos += estado.get("nodes") or estado.get("node_count") or 0
            aristas += estado.get("edges") or estado.get("edge_count") or 0
        except cbm.CbmError:
            pass  # un repo sin grafo disponible no puede tumbar el resumen

    return {**dict(fila), "nodos": nodos, "aristas": aristas}


@app.get("/capacidades")
def capacidades():
    """Qué funciones están realmente disponibles en ESTE deploy.

    El frontend lo consulta al arrancar para deshabilitar con un motivo, en vez
    de ofrecer botones que revientan: la auditoría necesita ANTHROPIC_API_KEY, y
    'Sincronizar wiki' solo sirve si los .md están montados en el contenedor
    (si no, el camino correcto es importarlos desde un repo o un ZIP).
    """
    bundle = Path(settings.wiki_microservicio_dir)
    return {
        "auditoria": bool(settings.anthropic_api_key),
        "wiki_bundle": bundle.is_dir() and any(bundle.rglob("*.md")),
    }


@app.get("/health", include_in_schema=False)
def health():
    """Liveness para el HEALTHCHECK del contenedor. No toca cbm ni la DB —
    solo confirma que el proceso responde."""
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")

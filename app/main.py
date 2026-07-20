"""AI Context Hub — backend de la demo.

Un solo proceso FastAPI que:
  - registra repos (URL git o ruta local), los indexa con el binario cbm
    (codebase-memory-mcp) y expone su grafo/arquitectura
  - sirve los lineamientos oficiales de la Wiki-Arquitectura (verbatim + FTS5)
  - /audit junta grafo + lineamientos para auditar un módulo con Claude

cbm y la wiki quedan totalmente detrás de esta capa: el frontend nunca los
toca directo. Si el motor de grafo cambia mañana, solo graph_engine.py se
reescribe.
"""

import json
import re
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db as repos_db
from . import graph_engine as cbm
from . import reglas as wiki
from .config import settings
from .mcp_server import mcp as mcp_server

# Capas de lineamiento que solemos cruzar en una auditoría de módulo CQRS
_CAPAS_AUDITORIA = ["endpoints", "ruteo", "handlers", "queries", "validators", "custom-exceptions"]

WORKSPACE = Path(settings.workspace_dir)
WORKSPACE.mkdir(exist_ok=True)

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


def _is_git_url(source: str) -> bool:
    return source.startswith(("http://", "https://", "git@"))


def _derive_name(source: str) -> str:
    stem = source.rstrip("/").rsplit("/", 1)[-1]
    stem = re.sub(r"\.git$", "", stem)
    return re.sub(r"[^a-zA-Z0-9_-]", "-", stem).lower() or "repo"


def _index_repo_job(name: str, local_path: str, delete_clone_after: bool = False):
    """Corre en background: index_repository puede tardar en repos grandes.

    Solo nos interesa el grafo, no el código clonado: si delete_clone_after es
    True (repo registrado por URL), el working tree se borra apenas cbm termina
    de indexar. cbm relee el código fuente del disco en cada get_code_snippet
    (no lo persiste en su propio store) — por diseño, para estos repos esa tool
    deja de devolver "source" una vez borrado el clon; el grafo (arquitectura,
    search_graph, trace_path) sigue funcionando porque eso sí vive en el store."""
    try:
        repos_db.set_status(name, "indexando")
        cbm.index_repository(local_path)

        # cbm nombra sus proyectos con un slug propio derivado de la ruta;
        # lo resolvemos matcheando root_path contra lo que acabamos de indexar.
        cbm_project = None
        for p in cbm.list_projects():
            if Path(p["root_path"]).resolve() == Path(local_path).resolve():
                cbm_project = p["name"]
                break

        repos_db.set_status(name, "listo", cbm_project=cbm_project)
    except cbm.CbmError as e:
        repos_db.set_status(name, "error", error=str(e))
    finally:
        if delete_clone_after:
            shutil.rmtree(local_path, ignore_errors=True)


@app.post("/repos")
def register_repo(req: RegisterRepoRequest, background_tasks: BackgroundTasks):
    name = req.name or _derive_name(req.source)

    if _is_git_url(req.source):
        local_path = WORKSPACE / name
        if not local_path.exists():
            result = subprocess.run(
                ["git", "clone", req.source, str(local_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                stdin=subprocess.DEVNULL,
                timeout=300,
            )
            if result.returncode != 0:
                raise HTTPException(400, f"git clone falló: {result.stderr.strip()}")
    else:
        local_path = Path(req.source)
        if not local_path.is_dir():
            raise HTTPException(400, f"La ruta local no existe: {req.source}")

    repos_db.upsert(name, req.source, str(local_path), status="registrado")
    background_tasks.add_task(_index_repo_job, name, str(local_path), _is_git_url(req.source))

    return {"name": name, "local_path": str(local_path), "status": "registrado"}


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


@app.get("/repos/{name}/arch")
def repo_architecture(name: str):
    repo = repos_db.get(name)
    if repo is None or not repo["cbm_project"]:
        raise HTTPException(404, "Repo no indexado todavía")
    return cbm.get_architecture(repo["cbm_project"])


@app.get("/repos/{name}/graph-ui")
def repo_graph_ui(name: str):
    """Redirige a la UI 3D que el propio binario cbm sirve (no reimplementamos
    visualización). Se asegura de que el proceso siga vivo antes de redirigir."""
    cbm.ensure_ui_running(CBM_UI_PORT)
    return RedirectResponse(GRAPH_UI_PUBLIC_URL)


@app.delete("/repos/{name}")
def delete_repo(name: str):
    """El working tree clonado ya se borró al terminar de indexar (solo nos
    interesa el grafo) — esto solo limpia el proyecto de cbm y el registro."""
    repo = repos_db.get(name)
    if repo is None:
        raise HTTPException(404, "Repo no registrado")

    if repo["cbm_project"]:
        try:
            cbm.delete_project(repo["cbm_project"])
        except cbm.CbmError:
            pass
    repos_db.delete(name)
    return {"deleted": name}


# --- Lineamientos (wiki) ---------------------------------------------------


@app.post("/wiki/sync")
def wiki_sync():
    try:
        return wiki.sync()
    except wiki.WikiError as e:
        raise HTTPException(500, str(e))


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


app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")

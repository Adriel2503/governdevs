"""Servidor MCP del AI Context Hub — expone grafo + lineamientos como tools.

Se monta dentro de main.py (FastAPI) en /mcp, vía Streamable HTTP: cualquier
agente (Claude Code, Cursor, Copilot...) que soporte MCP remoto puede conectarse
con `claude mcp add --transport http context-hub http://host/mcp`.

Diseño: las tools devuelven HECHOS (código real con archivo:línea, reglas
oficiales verbatim) — NO llaman a ningún LLM propio. El razonamiento (ej. una
auditoría) lo hace el agente que se conectó, con su propio modelo. Esto evita
depender de ANTHROPIC_API_KEY para el camino MCP.

Autorización: hoy es un stub (permite todo) — es un piloto de un solo equipo.
El punto de extensión para Escala 2 (permisos por repo vía Azure DevOps) es
`_check_access()`; envolver ahí la validación no requiere tocar las tools.
"""

from fastmcp import FastMCP

from . import db as repos_db
from . import graph_engine as cbm
from . import reglas as wiki

mcp = FastMCP(
    name="realplaza-context-hub",
    instructions=(
        "Contexto gobernado de Real Plaza: grafo de código real (no lo adivines, "
        "consúltalo) + lineamientos oficiales de arquitectura (son la norma; ante "
        "conflicto con el código existente, la norma manda). Antes de crear o "
        "auditar un handler/query/validator/endpoint, llama a get_regla con la "
        "capa correspondiente."
    ),
)


def _check_access(repo: str) -> None:
    """Punto de extensión: aquí se conecta la autorización por repo (Azure
    DevOps) cuando se conecte identidad real. Hoy: stub, permite todo."""
    return


def _resolve_cbm_project(repo: str) -> str:
    _check_access(repo)
    info = repos_db.get(repo)
    if info is None:
        raise ValueError(
            f"'{repo}' no está registrado en el hub. Usa list_repos para ver los disponibles."
        )
    if not info.get("cbm_project"):
        raise ValueError(f"'{repo}' todavía se está indexando (estado: {info['status']}).")
    return info["cbm_project"]


# --- Repos / grafo ----------------------------------------------------------


@mcp.tool
def list_repos() -> list[dict]:
    """Lista los repositorios registrados en el hub, con su estado de indexado."""
    return repos_db.list_all()


@mcp.tool
def get_architecture(repo: str) -> dict:
    """Resumen de arquitectura del repo: conteo de nodos por tipo (Class, Route,
    Function...), tamaño total del grafo. Punto de partida para orientarse en
    un repo desconocido."""
    project = _resolve_cbm_project(repo)
    return cbm.get_architecture(project)


@mcp.tool
def search_graph(repo: str, name_pattern: str, label: str | None = None) -> dict:
    """Busca símbolos en el grafo real del código por patrón de nombre (regex).
    label opcional: Class, Function, Method, Route, Interface, File... Úsalo
    para ubicar dónde vive algo antes de leer archivos a ciegas."""
    project = _resolve_cbm_project(repo)
    return cbm.search_graph(project, name_pattern, label)


@mcp.tool
def trace_path(repo: str, function_name: str, direction: str = "both") -> dict:
    """Traza quién llama a esta función y a qué llama ella (direction: 'callers',
    'callees' o 'both'). Úsalo antes de modificar una firma para saber el
    impacto real, no adivinado."""
    project = _resolve_cbm_project(repo)
    return cbm.trace_path(project, function_name, direction)


@mcp.tool
def get_code_snippet(repo: str, qualified_name: str) -> dict:
    """Trae el código fuente real de un símbolo (qualified_name viene de
    search_graph), con archivo:línea de procedencia verificable. Nota: solo
    funciona mientras el clon del repo siga en disco — si ya se indexó y se
    borró el working tree (comportamiento por defecto para repos por URL),
    devuelve "(source not available)"."""
    project = _resolve_cbm_project(repo)
    return cbm.get_code_snippet(project, qualified_name)


# --- Lineamientos (wiki oficial) --------------------------------------------


@mcp.tool
def list_reglas() -> list[dict]:
    """Lista las reglas oficiales disponibles (Arquetipo Microservicio):
    Handlers, Queries, Validators, Endpoints, Ruteo, etc."""
    return wiki.list_reglas()


@mcp.tool
def get_regla(capa: str) -> dict:
    """Devuelve el contenido VERBATIM de la regla oficial de una capa (ej.
    'handlers', 'queries', 'validators', 'ruteo-de-endpoints', 'endpoints').
    Esto es la norma, no un resumen — cítala tal cual."""
    regla = wiki.get_regla(capa)
    if regla is None:
        raise ValueError(f"No hay regla para '{capa}'. Usa list_reglas para ver las disponibles.")
    return regla


@mcp.tool
def buscar_regla(query: str) -> list[dict]:
    """Busca en todas las reglas oficiales por palabra clave (ej. 'totalResults',
    'nombrar rutas de reportes'). Útil cuando no sabes en qué capa cae la duda.
    Devuelve fragmentos para ubicar la capa; para citar, pedí get_regla."""
    # `tramos` solo existe para pintar el resaltado en la interfaz. Al agente le
    # sirve el texto del fragmento, no el markup de dónde coincidió.
    return [{k: v for k, v in r.items() if k != "tramos"} for r in wiki.buscar(query)]


# --- Auditoría: reúne hechos, el agente conectado razona --------------------


@mcp.tool
def reunir_contexto_auditoria(repo: str, modulo: str, max_clases: int = 8) -> dict:
    """Junta, para un módulo (ej. 'Products'), el código real de sus clases
    (grafo, con archivo:línea) y las reglas oficiales de las capas típicas de
    un feature CQRS (endpoints, ruteo, handlers, queries, validators,
    custom-exceptions). NO audita por sí misma — te da el material para que TÚ
    compares código contra norma y reportes los incumplimientos con precisión."""
    project = _resolve_cbm_project(repo)

    hallazgos = cbm.search_graph(project, f".*{modulo}.*", label="Class")
    clases = hallazgos.get("results", [])[:max_clases]

    snippets = []
    for c in clases:
        try:
            snippets.append(cbm.get_code_snippet(project, c["qualified_name"]))
        except cbm.CbmError:
            continue

    capas = ["endpoints", "ruteo-de-endpoints", "handlers", "queries", "validators", "custom-exceptions"]
    reglas = {}
    for capa in capas:
        regla = wiki.get_regla(capa)
        if regla:
            reglas[regla["capa"]] = regla["contenido"]

    return {"modulo": modulo, "codigo": snippets, "reglas_oficiales": reglas}

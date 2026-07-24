"""Capa de lineamientos: las reglas oficiales del Arquetipo/Microservicio,
servidas verbatim más búsqueda BM25 transversal.

Antes: SQLite FTS5 (tabla `reglas`). Ahora: Postgres/ParadeDB con `pg_search`
(tabla `lineamientos`). El ranking BM25 se conserva idéntico — cambia el motor,
no el resultado: `MATCH ... ORDER BY rank` (FTS5) → `@@@ ... ORDER BY
paradedb.score(id)` (pg_search).

Se mantienen la API pública (sync, list_reglas, get_regla, buscar) y los shapes
de retorno para no tocar main.py ni mcp_server.py.

Fuente de contenido: por ahora los .md bundleados en wiki_data/ — `sync()` los
relee e inserta/actualiza en `lineamientos`. En la Fase 1 (import) se suma la
importación desde una URL de GitHub, que reusa la misma tabla.

Las reglas NO se resumen ni pasan por LLM: son la norma, se sirven tal cual.
"""

import re
from pathlib import Path
from urllib.parse import unquote

import psycopg

from . import pg
from .config import settings

MICROSERVICIO_DIR = Path(settings.wiki_microservicio_dir)
FUENTE_WIKI = "wiki_data/Microservicio"


class WikiError(RuntimeError):
    pass


def _limpiar(texto: str) -> str:
    """Decodifica el %XX que mete Azure DevOps en los nombres de página.

    En esta wiki hay 26 apariciones de %2D (un guion). Sin decodificar, la regla
    se llama 'Custom Exceptions %2D Status Code 400%2Dx' en la interfaz y en lo
    que recibe un agente por MCP.
    """
    return unquote(texto)


def _slug_segmento(segmento: str) -> str:
    """'2.-Application-Handler-con-Wolverine.md' -> 'application-handler-con-wolverine'

    Quita la extensión y el prefijo de orden de la wiki ('2.-', '8.1-'), que es
    presentación, no identidad.
    """
    nombre = _limpiar(segmento)
    if nombre.lower().endswith(".md"):
        nombre = nombre[:-3]
    nombre = re.sub(r"^[\d.]+[-\s]*", "", nombre)
    nombre = re.sub(r"[\s_]+", "-", nombre.strip())
    return re.sub(r"-{2,}", "-", nombre).strip("-").lower()


def _slug_capa(ruta_relativa: str) -> str:
    """Deriva el identificador de un documento desde su RUTA COMPLETA, no solo
    desde el nombre del archivo.

    Con el nombre suelto, 16 slugs de esta wiki colisionan y dejan 32 documentos
    ambiguos: 'Endpoints.md' existe en la raíz del arquetipo y otra vez dentro de
    Lineamientos-de-desarrollo/, y son documentos distintos. La ruta los
    distingue y hace que cada documento sea direccionable:

        2.-Endpoints.md                        -> endpoints
        Lineamientos-de-desarrollo/Endpoints.md -> lineamientos-de-desarrollo/endpoints
    """
    partes = [s for s in (_slug_segmento(p) for p in ruta_relativa.split("/")) if s]
    return "/".join(partes) or "otros"


def _titulo(ruta_relativa: str) -> str:
    """Nombre legible del documento, ya decodificado y sin el prefijo de orden."""
    hoja = _slug_segmento(ruta_relativa.split("/")[-1])
    return hoja.replace("-", " ").title() or "Sin titulo"


def _upsert_capa(c, slug: str) -> None:
    """Garantiza que la capa exista antes de insertar el lineamiento (FK). Las 6
    capas oficiales ya vienen sembradas; cualquier otra derivada del nombre del
    archivo se agrega con orden alto para que quede después de las curadas."""
    # El nombre visible sale de la hoja del slug: 'lineamientos-de-desarrollo/
    # endpoints' se muestra como 'Endpoints', no como la ruta entera.
    nombre = slug.rsplit("/", 1)[-1].replace("-", " ").title()
    c.execute(
        "INSERT INTO capas (slug, nombre, orden) VALUES (%s, %s, 100) ON CONFLICT (slug) DO NOTHING",
        (slug, nombre),
    )


def indexar_carpeta(base_dir: Path, fuente: str) -> int:
    """Indexa todos los .md bajo base_dir en `lineamientos` (upsert por
    ruta_relativa vigente; idempotente). Reutilizado por sync() (wiki_data
    bundleada) y por el importador (repos de GitHub / ZIP subidos). `fuente`
    registra de dónde vino cada documento para trazabilidad."""
    if not base_dir.is_dir():
        raise WikiError(f"No existe la carpeta: {base_dir}")

    count = 0
    with pg.conn() as c:
        for md_file in sorted(base_dir.rglob("*.md")):
            contenido = md_file.read_text(encoding="utf-8")
            # Las portadas de carpeta de Azure DevOps son .md de 0 bytes: cuando
            # una página tiene hijas, la wiki crea el archivo vacío MÁS una
            # carpeta con el contenido real. Indexarlas mete "reglas" que no
            # dicen nada (32 de 229 en esta wiki) y un agente que pida una por
            # MCP recibe un documento en blanco. No son reglas, son carpetas.
            if not contenido.strip():
                continue
            ruta_relativa = str(md_file.relative_to(base_dir)).replace("\\", "/")
            capa = _slug_capa(ruta_relativa)
            _upsert_capa(c, capa)
            c.execute(
                """
                INSERT INTO lineamientos
                    (capa_slug, ruta_relativa, titulo, contenido, formato_original, fuente, es_vigente)
                VALUES (%s, %s, %s, %s, 'md', %s, true)
                ON CONFLICT (ruta_relativa) WHERE es_vigente
                DO UPDATE SET capa_slug = EXCLUDED.capa_slug,
                              titulo    = EXCLUDED.titulo,
                              contenido = EXCLUDED.contenido,
                              fuente    = EXCLUDED.fuente
                """,
                (capa, ruta_relativa, _titulo(ruta_relativa), contenido, fuente),
            )
            count += 1
    return count


def sync() -> dict:
    """Reindexa las reglas bundleadas en disco (wiki_data) hacia Postgres."""
    if not MICROSERVICIO_DIR.is_dir():
        raise WikiError(f"No existe la carpeta esperada: {MICROSERVICIO_DIR}")
    return {"reglas_indexadas": indexar_carpeta(MICROSERVICIO_DIR, FUENTE_WIKI)}


def list_reglas() -> list[dict]:
    with pg.conn() as c:
        rows = c.execute(
            """
            SELECT capa_slug, titulo, ruta_relativa, length(contenido) AS chars
            FROM lineamientos
            WHERE es_vigente
            ORDER BY ruta_relativa
            """
        ).fetchall()
    return [
        {"capa": r["capa_slug"], "archivo": r["titulo"], "ruta_relativa": r["ruta_relativa"], "chars": r["chars"]}
        for r in rows
    ]


def get_regla(capa: str) -> dict | None:
    """Devuelve el contenido VERBATIM de una regla oficial.

    Acepta el slug completo ('lineamientos-de-desarrollo/endpoints') o el nombre
    corto ('endpoints'), que es como lo pide un agente y como lo infiere
    verificacion.capas_tocadas() desde las rutas del repo.

    Cuando el nombre corto coincide con varios documentos, devuelve el que más
    probablemente sea la norma y **declara los otros en `alternativas`**. Antes
    elegía uno y callaba: el agente no tenía forma de saber que existía otra
    versión, ni de pedirla. Ahora puede pedirla por su slug completo.
    """
    buscado = capa.strip().lower()
    with pg.conn() as c:
        # 1) slug exacto  2) nombre corto = último segmento de la ruta
        for where, params in (
            ("capa_slug = %s", (buscado,)),
            ("(capa_slug = %s OR capa_slug LIKE %s)", (buscado, f"%/{buscado}")),
            ("capa_slug LIKE %s", (f"%{buscado}%",)),
        ):
            filas = c.execute(
                f"""
                SELECT capa_slug, titulo, ruta_relativa, contenido
                FROM lineamientos
                WHERE {where} AND es_vigente
                ORDER BY (ruta_relativa LIKE 'Lineamientos-de-desarrollo/%%') DESC,
                         length(capa_slug), ruta_relativa
                """,
                params,
            ).fetchall()
            if filas:
                principal, *otras = filas
                return {
                    "capa": principal["capa_slug"],
                    "archivo": principal["titulo"],
                    "ruta_relativa": principal["ruta_relativa"],
                    "contenido": principal["contenido"],
                    "alternativas": [
                        {"capa": o["capa_slug"], "ruta_relativa": o["ruta_relativa"]} for o in otras
                    ],
                }
    return None


def _run_busqueda(match_expr: str, limit: int) -> list[dict]:
    """Una búsqueda BM25 en su propia conexión (para que un error de sintaxis del
    parser aborte solo esta transacción y el retry use una conexión limpia).
    paradedb.snippet resalta el fragmento; paradedb.score da el ranking BM25."""
    with pg.conn() as c:
        return c.execute(
            """
            SELECT capa_slug, titulo, ruta_relativa,
                   paradedb.snippet(contenido) AS snippet
            FROM lineamientos
            WHERE contenido @@@ %s AND es_vigente
            ORDER BY paradedb.score(id) DESC
            LIMIT %s
            """,
            (match_expr, limit),
        ).fetchall()


def buscar(query: str, limit: int = 5) -> list[dict]:
    """Búsqueda BM25 sobre las reglas. Devuelve fragmentos con snippet resaltado.
    Mismo comportamiento que la versión FTS5, con fallback si el texto tiene
    caracteres que el parser de consultas interpreta como sintaxis."""
    try:
        rows = _run_busqueda(query, limit)
    except psycopg.Error:
        # query con sintaxis inválida para el parser (ej. caracteres especiales
        # sueltos) → tratarlo como frase literal entre comillas.
        rows = _run_busqueda('"' + query.replace('"', "") + '"', limit)
    return [
        {"capa": r["capa_slug"], "archivo": r["titulo"], "ruta_relativa": r["ruta_relativa"], "snippet": r["snippet"]}
        for r in rows
    ]

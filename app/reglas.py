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

from pathlib import Path

import psycopg

from . import pg
from .config import settings

MICROSERVICIO_DIR = Path(settings.wiki_microservicio_dir)
FUENTE_WIKI = "wiki_data/Microservicio"


class WikiError(RuntimeError):
    pass


def _slug_capa(filename: str) -> str:
    """'2.-Application-Handler-con-Wolverine.md' -> 'application-handler-con-wolverine'"""
    name = filename.rsplit(".", 1)[0]
    name = name.lstrip("0123456789.-")
    return name.strip("-").lower() or "otros"


def _upsert_capa(c, slug: str) -> None:
    """Garantiza que la capa exista antes de insertar el lineamiento (FK). Las 6
    capas oficiales ya vienen sembradas; cualquier otra derivada del nombre del
    archivo se agrega con orden alto para que quede después de las curadas."""
    nombre = slug.replace("-", " ").title()
    c.execute(
        "INSERT INTO capas (slug, nombre, orden) VALUES (%s, %s, 100) ON CONFLICT (slug) DO NOTHING",
        (slug, nombre),
    )


def sync() -> dict:
    """Reindexa las reglas bundleadas en disco hacia Postgres. Idempotente: si el
    documento (ruta_relativa) ya existe como vigente, actualiza su contenido en
    lugar de duplicar."""
    if not MICROSERVICIO_DIR.is_dir():
        raise WikiError(f"No existe la carpeta esperada: {MICROSERVICIO_DIR}")

    count = 0
    with pg.conn() as c:
        for md_file in sorted(MICROSERVICIO_DIR.rglob("*.md")):
            contenido = md_file.read_text(encoding="utf-8")
            ruta_relativa = str(md_file.relative_to(MICROSERVICIO_DIR)).replace("\\", "/")
            capa = _slug_capa(md_file.name)
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
                (capa, ruta_relativa, md_file.name, contenido, FUENTE_WIKI),
            )
            count += 1
    return {"reglas_indexadas": count}


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
    """Devuelve el contenido VERBATIM de la regla oficial (match exacto o por
    prefijo de slug). Cuando el slug es ambiguo (ej. 'endpoints' existe como
    tutorial Y como regla oficial), prioriza las de Lineamientos-de-desarrollo/
    — esas son la norma; el resto son tutoriales paso a paso."""
    with pg.conn() as c:
        for where, param in (("capa_slug = %s", capa), ("capa_slug LIKE %s", f"%{capa}%")):
            row = c.execute(
                f"""
                SELECT capa_slug, titulo, ruta_relativa, contenido
                FROM lineamientos
                WHERE {where} AND es_vigente
                ORDER BY (ruta_relativa LIKE 'Lineamientos-de-desarrollo/%%') DESC, ruta_relativa
                LIMIT 1
                """,
                (param,),
            ).fetchone()
            if row:
                return {
                    "capa": row["capa_slug"],
                    "archivo": row["titulo"],
                    "ruta_relativa": row["ruta_relativa"],
                    "contenido": row["contenido"],
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

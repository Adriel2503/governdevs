"""Capa de lineamientos: las reglas oficiales de Arquetipos/Microservicio,
bundleadas como archivos dentro de este repo (wiki_data/), servidas verbatim
más un índice FTS5 para búsqueda transversal.

Por directiva del jefe, solo indexamos Lineamientos/Desarrollo/Arquetipos/Microservicio.
Las reglas NO se resumen ni se pasan por LLM: son la norma oficial, se sirven tal cual.

Actualizar contenido: pisar los .md en wiki_data/Microservicio (copiados a mano
o por CI desde la wiki fuente) y llamar a /wiki/sync para reindexar. No hay git
en runtime — la wiki viaja congelada dentro de la imagen/deploy.
"""

import sqlite3
from pathlib import Path

from .config import settings

MICROSERVICIO_DIR = Path(settings.wiki_microservicio_dir)
DB_PATH = Path(settings.data_dir) / "wiki_index.db"


class WikiError(RuntimeError):
    pass


def _connect() -> sqlite3.Connection:
    """Conexión SQLite endurecida: busy_timeout + WAL, igual que en db.py — el
    reindexado (sync) escribe mientras alguien puede estar buscando/listando."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def sync() -> dict:
    """Reindexa las reglas bundleadas en disco (sin git: el contenido viaja
    congelado dentro del deploy; actualizarlo requiere reemplazar los .md y
    redeployar, o llamar a este endpoint tras pisarlos a mano)."""
    count = _reindex()
    return {"reglas_indexadas": count}


def _slug_capa(filename: str) -> str:
    """'2.-Application-Handler-con-Wolverine.md' -> 'application-handler-con-wolverine'"""
    name = filename.rsplit(".", 1)[0]
    name = name.lstrip("0123456789.-")
    return name.strip("-").lower()


def _reindex() -> int:
    if not MICROSERVICIO_DIR.is_dir():
        raise WikiError(f"No existe la carpeta esperada: {MICROSERVICIO_DIR}")

    conn = _connect()
    conn.execute("DROP TABLE IF EXISTS reglas")
    conn.execute(
        """
        CREATE VIRTUAL TABLE reglas USING fts5(
            capa, archivo, ruta_relativa, contenido
        )
        """
    )

    count = 0
    for md_file in sorted(MICROSERVICIO_DIR.rglob("*.md")):
        contenido = md_file.read_text(encoding="utf-8")
        ruta_relativa = str(md_file.relative_to(MICROSERVICIO_DIR)).replace("\\", "/")
        capa = _slug_capa(md_file.name)
        conn.execute(
            "INSERT INTO reglas (capa, archivo, ruta_relativa, contenido) VALUES (?, ?, ?, ?)",
            (capa, md_file.name, ruta_relativa, contenido),
        )
        count += 1

    conn.commit()
    conn.close()
    return count


def _ensure_index():
    if not DB_PATH.exists():
        _reindex()


def list_reglas() -> list[dict]:
    _ensure_index()
    conn = _connect()
    rows = conn.execute(
        "SELECT capa, archivo, ruta_relativa, length(contenido) FROM reglas ORDER BY ruta_relativa"
    ).fetchall()
    conn.close()
    return [
        {"capa": r[0], "archivo": r[1], "ruta_relativa": r[2], "chars": r[3]}
        for r in rows
    ]


def get_regla(capa: str) -> dict | None:
    """Devuelve el contenido VERBATIM de la regla oficial (match exacto o por
    prefijo de slug). Cuando el slug es ambiguo (ej. 'endpoints' existe como
    tutorial Y como regla oficial), prioriza las de Lineamientos-de-desarrollo/
    — esas son la norma; el resto son tutoriales paso a paso."""
    _ensure_index()
    conn = _connect()
    for where in ("capa = ?", "capa LIKE ?"):
        param = capa if where == "capa = ?" else f"%{capa}%"
        row = conn.execute(
            f"""
            SELECT capa, archivo, ruta_relativa, contenido FROM reglas
            WHERE {where}
            ORDER BY ruta_relativa LIKE 'Lineamientos-de-desarrollo/%' DESC
            LIMIT 1
            """,
            (param,),
        ).fetchone()
        if row is not None:
            break
    conn.close()
    if row is None:
        return None
    return {"capa": row[0], "archivo": row[1], "ruta_relativa": row[2], "contenido": row[3]}


def buscar(query: str, limit: int = 5) -> list[dict]:
    """Búsqueda FTS5 sobre las reglas. Devuelve fragmentos con snippet resaltado."""
    _ensure_index()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT capa, archivo, ruta_relativa,
                   snippet(reglas, 3, '**', '**', '...', 40)
            FROM reglas
            WHERE reglas MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # query con sintaxis FTS5 inválida (ej. caracteres especiales sueltos)
        rows = conn.execute(
            """
            SELECT capa, archivo, ruta_relativa,
                   snippet(reglas, 3, '**', '**', '...', 40)
            FROM reglas
            WHERE reglas MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (f'"{query}"', limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"capa": r[0], "archivo": r[1], "ruta_relativa": r[2], "snippet": r[3]}
        for r in rows
    ]

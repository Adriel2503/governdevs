"""Capa de lineamientos: la Wiki-Arquitectura.wiki (repo git ya clonado por el
usuario) servida verbatim, más un índice FTS5 para búsqueda transversal.

Por directiva del jefe, solo indexamos Lineamientos/Desarrollo/Arquetipos/Microservicio.
Las reglas NO se resumen ni se pasan por LLM: son la norma oficial, se sirven tal cual.
"""

import os
import sqlite3
import subprocess
from pathlib import Path

WIKI_REPO = Path(
    r"C:\Users\Experis\Documents\Real_Plaza\optimizar_ia\repositorios\Wiki-Arquitectura.wiki"
)
MICROSERVICIO_DIR = WIKI_REPO / "Lineamientos" / "Desarrollo" / "Arquetipos" / "Microservicio"

DB_PATH = Path(__file__).parent / "wiki_index.db"


class WikiError(RuntimeError):
    pass


def sync() -> dict:
    """git pull del clon existente de la wiki. No clona: el usuario ya la clonó
    (login Azure DevOps interactivo), nosotros solo actualizamos."""
    if not WIKI_REPO.is_dir():
        raise WikiError(f"No existe el clon de la wiki en {WIKI_REPO}")

    result = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=WIKI_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    if result.returncode != 0:
        raise WikiError(f"git pull falló: {result.stderr.strip()}")

    count = _reindex()
    return {"pulled": result.stdout.strip(), "reglas_indexadas": count}


def _slug_capa(filename: str) -> str:
    """'2.-Application-Handler-con-Wolverine.md' -> 'application-handler-con-wolverine'"""
    name = filename.rsplit(".", 1)[0]
    name = name.lstrip("0123456789.-")
    return name.strip("-").lower()


def _reindex() -> int:
    if not MICROSERVICIO_DIR.is_dir():
        raise WikiError(f"No existe la carpeta esperada: {MICROSERVICIO_DIR}")

    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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

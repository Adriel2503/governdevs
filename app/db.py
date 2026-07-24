"""Persistencia de repos registrados en Postgres.

Antes vivía en SQLite (repos.db); ahora en la tabla `repos` de Postgres/ParadeDB
(ver migrations/schema.sql). Se mantienen la misma API pública y los mismos shapes
de retorno para no tocar main.py ni mcp_server.py.

La tabla `repos` de Postgres tiene más columnas (credential_id, rama, watch_paths,
webhook_*, last_indexed_commit...) que se usarán en la Fase 2 (GitHub/CI-CD); acá
solo se tocan las que necesita el flujo actual de registro + indexado.
"""

from datetime import datetime

from . import pg

# owner es NOT NULL en el esquema; hasta que exista RBAC, todo lo registra el
# admin del piloto. En la Fase 2 este valor vendrá del usuario autenticado.
_DEFAULT_OWNER = "admin"


def upsert(name: str, source: str, local_path: str, status: str = "registrado"):
    with pg.conn() as c:
        c.execute(
            """
            INSERT INTO repos (name, source, local_path, status, owner)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE
              SET source = EXCLUDED.source,
                  local_path = EXCLUDED.local_path
            """,
            (name, source, local_path, status, _DEFAULT_OWNER),
        )


def set_status(name: str, status: str, cbm_project: str | None = None, error: str | None = None):
    with pg.conn() as c:
        c.execute(
            "UPDATE repos SET status = %s, cbm_project = COALESCE(%s, cbm_project), error = %s WHERE name = %s",
            (status, cbm_project, error, name),
        )


def get(name: str) -> dict | None:
    with pg.conn() as c:
        row = c.execute(
            "SELECT name, source, local_path, cbm_project, status, error, registered_at FROM repos WHERE name = %s",
            (name,),
        ).fetchone()
    return _norm(row) if row else None


def list_all() -> list[dict]:
    with pg.conn() as c:
        rows = c.execute(
            "SELECT name, source, local_path, cbm_project, status, error, registered_at FROM repos ORDER BY registered_at DESC"
        ).fetchall()
    return [_norm(r) for r in rows]


def delete(name: str):
    with pg.conn() as c:
        c.execute("DELETE FROM repos WHERE name = %s", (name,))


def _norm(row: dict) -> dict:
    """registered_at es TIMESTAMPTZ (datetime) — se serializa a ISO para que el
    dict sea JSON-serializable, igual que devolvía la versión SQLite (string)."""
    d = dict(row)
    ra = d.get("registered_at")
    if isinstance(ra, datetime):
        d["registered_at"] = ra.isoformat()
    return d

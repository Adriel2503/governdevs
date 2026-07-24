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


_COLS = (
    "name, source, local_path, cbm_project, status, error, registered_at, "
    "credential_id, rama, watch_paths, last_indexed_commit, last_synced_at"
)


def upsert(
    name: str,
    source: str,
    local_path: str,
    status: str = "registrado",
    credential_id: str | None = None,
    rama: str = "main",
    watch_paths: list[str] | None = None,
):
    with pg.conn() as c:
        c.execute(
            """
            INSERT INTO repos (name, source, local_path, status, owner, credential_id, rama, watch_paths)
            VALUES (%s, %s, %s, %s, %s, %s::uuid, %s, %s)
            ON CONFLICT (name) DO UPDATE
              SET source        = EXCLUDED.source,
                  local_path    = EXCLUDED.local_path,
                  credential_id = EXCLUDED.credential_id,
                  rama          = EXCLUDED.rama,
                  watch_paths   = EXCLUDED.watch_paths
            """,
            (name, source, local_path, status, _DEFAULT_OWNER, credential_id, rama, watch_paths or []),
        )


def set_status(name: str, status: str, cbm_project: str | None = None, error: str | None = None):
    with pg.conn() as c:
        c.execute(
            "UPDATE repos SET status = %s, cbm_project = COALESCE(%s, cbm_project), error = %s WHERE name = %s",
            (status, cbm_project, error, name),
        )


def marcar_indexado(name: str, commit_sha: str | None = None, cbm_project: str | None = None):
    """Cierra un ciclo de indexado: deja el repo listo y registra hasta qué commit
    está el grafo (base del reindexado incremental de la Fase 2)."""
    with pg.conn() as c:
        c.execute(
            """
            UPDATE repos
               SET status              = 'listo',
                   error               = NULL,
                   cbm_project         = COALESCE(%s, cbm_project),
                   last_indexed_commit = COALESCE(%s, last_indexed_commit),
                   last_synced_at      = now()
             WHERE name = %s
            """,
            (cbm_project, commit_sha, name),
        )


def buscar_por_github(full_name: str) -> list[dict]:
    """Candidatos cuyo `source` apunta a 'owner/repo' (el full_name que manda
    GitHub en el webhook). Devuelve una lista porque el match por URL puede ser
    ambiguo; quien desempata es la firma HMAC (solo el repo correcto tiene el
    secreto), así la búsqueda y la autenticación son el mismo paso."""
    with pg.conn() as c:
        rows = c.execute(
            f"SELECT {_COLS} FROM repos WHERE source ILIKE %s OR source ILIKE %s",
            (f"%{full_name}", f"%{full_name}.git"),
        ).fetchall()
    return [_norm(r) for r in rows]


def guardar_webhook(name: str, hook_id: int | None, secret_cifrado: str | None):
    with pg.conn() as c:
        c.execute(
            "UPDATE repos SET webhook_github_id = %s, webhook_secret_cifrado = %s WHERE name = %s",
            (hook_id, secret_cifrado, name),
        )


def datos_webhook(name: str) -> dict | None:
    """Uso INTERNO. Deliberadamente fuera de get()/list_all(): esos alimentan la
    API y el secreto del webhook no debe salir nunca por ahí."""
    with pg.conn() as c:
        return c.execute(
            "SELECT webhook_github_id, webhook_secret_cifrado FROM repos WHERE name = %s",
            (name,),
        ).fetchone()


def get(name: str) -> dict | None:
    with pg.conn() as c:
        row = c.execute(f"SELECT {_COLS} FROM repos WHERE name = %s", (name,)).fetchone()
    return _norm(row) if row else None


def list_all() -> list[dict]:
    with pg.conn() as c:
        rows = c.execute(f"SELECT {_COLS} FROM repos ORDER BY registered_at DESC").fetchall()
    return [_norm(r) for r in rows]


def delete(name: str):
    with pg.conn() as c:
        c.execute("DELETE FROM repos WHERE name = %s", (name,))


def _norm(row: dict) -> dict:
    """Los TIMESTAMPTZ y el UUID se serializan a string para que el dict sea
    JSON-serializable (la versión SQLite ya devolvía strings)."""
    d = dict(row)
    for campo in ("registered_at", "last_synced_at"):
        if isinstance(d.get(campo), datetime):
            d[campo] = d[campo].isoformat()
    if d.get("credential_id") is not None:
        d["credential_id"] = str(d["credential_id"])
    return d

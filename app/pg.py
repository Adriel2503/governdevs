"""Conexión a Postgres/ParadeDB — pool único compartido por todo el hub.

La base vive en Dokploy (ParadeDB pg17). En producción la app se conecta por el
host interno de Dokploy; en desarrollo se apunta a un Postgres local. La URL sale
de `settings.database_url` (DATABASE_URL) — nada lee os.environ directo.

Se usa un pool perezoso: se abre en el primer uso, no al importar, para que la
app arranque aunque la base todavía no esté disponible (y para no exigir la URL
en tiempo de import durante los tests).
"""

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise RuntimeError(
                "Falta DATABASE_URL: la capa de datos necesita la conexión a Postgres."
            )
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=10,
            open=True,
            # dict_row: los cursores devuelven dicts en vez de tuplas, así el
            # resto del código lee por nombre de columna.
            kwargs={"row_factory": dict_row},
        )
    return _pool


@contextmanager
def conn():
    """Entrega una conexión del pool. Al salir sin error hace commit y la
    devuelve al pool; ante excepción, rollback. Uso:

        with pg.conn() as c:
            c.execute("...", (...))
    """
    with _get_pool().connection() as c:
        yield c

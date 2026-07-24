"""Cola de reindexado: FIFO en memoria + un único worker.

**Un solo hilo** consumiendo la cola garantiza que nunca se reindexen dos repos
—ni el mismo dos veces— en paralelo, así el grafo no se corrompe. Es suficiente
para el piloto; escalar a N workers con lock por repo es aditivo y no cambia
esta interfaz.

El análisis de Dokploy confirmó que **no hace falta Redis**: ellos también usan
una cola in-memory FIFO por grupo, con una interfaz que imita BullMQ para poder
migrar. Acá igual: `index_jobs` es el registro **durable** (sobrevive reinicios y
da el historial que se muestra en la demo); la cola es solo el runtime.

Idempotencia: el índice único parcial sobre (repo_name, commit_sha) para eventos
'push' hace que un reintento de webhook del mismo commit no encole dos veces.
"""

import queue
import threading
from pathlib import Path

import psycopg

from . import credenciales
from . import db as repos_db
from . import git_repo
from . import graph_engine as cbm
from . import pg

# La cola lleva (tipo, id): "reindex" -> index_jobs, "revision" -> revisiones.
# Ambos tocan el MISMO clon en disco (uno hace fetch/reset, el otro hace checkout
# de la rama del PR), así que compartir un único worker no es solo eficiencia:
# es lo que evita que un reindexado indexe el código de un PR dentro del grafo
# canónico de main.
_cola: "queue.Queue[tuple[str, str]]" = queue.Queue()
_worker: threading.Thread | None = None


def encolar(repo_name: str, commit_sha: str | None = None, evento: str = "push") -> str | None:
    """Registra el job y lo pone en la cola. Devuelve el id del job, o None si
    ese commit ya estaba encolado/procesado (reintento de GitHub)."""
    try:
        with pg.conn() as c:
            row = c.execute(
                """
                INSERT INTO index_jobs (repo_name, commit_sha, evento)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (repo_name, commit_sha, evento),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        return None  # mismo commit ya encolado → nada que hacer

    job_id = str(row["id"])
    _cola.put(("reindex", job_id))
    return job_id


def encolar_revision(revision_id: str) -> str:
    """Encola la verificación de un PR (ya creada en estado 'generando')."""
    _cola.put(("revision", revision_id))
    return revision_id


def listar_jobs(repo_name: str, limite: int = 20) -> list[dict]:
    with pg.conn() as c:
        rows = c.execute(
            """
            SELECT id, repo_name, commit_sha, evento, estado, mensaje,
                   encolado_en, iniciado_en, finalizado_en
            FROM index_jobs
            WHERE repo_name = %s
            ORDER BY encolado_en DESC
            LIMIT %s
            """,
            (repo_name, limite),
        ).fetchall()
    return [_norm(r) for r in rows]


def _estado(job_id: str, estado: str, mensaje: str | None = None) -> None:
    campos = {
        "corriendo": "iniciado_en = now()",
        "ok": "finalizado_en = now()",
        "error": "finalizado_en = now()",
    }.get(estado)
    with pg.conn() as c:
        c.execute(
            f"UPDATE index_jobs SET estado = %s, mensaje = %s"
            + (f", {campos}" if campos else "")
            + " WHERE id = %s",
            (estado, mensaje, job_id),
        )


def _procesar(job_id: str) -> None:
    with pg.conn() as c:
        job = c.execute(
            "SELECT repo_name, commit_sha, evento FROM index_jobs WHERE id = %s", (job_id,)
        ).fetchone()
    if job is None:
        return

    _estado(job_id, "corriendo")
    try:
        repo = repos_db.get(job["repo_name"])
        if repo is None:
            raise RuntimeError("el repo ya no está registrado")
        if not repo.get("local_path"):
            raise RuntimeError("el repo no tiene un clon en disco")

        repos_db.set_status(job["repo_name"], "indexando")
        ruta = Path(repo["local_path"])

        # Repos por URL: se pone al día el clon persistente (fetch incremental,
        # mucho más barato que re-clonar). Rutas locales se indexan tal cual.
        if str(repo["source"]).startswith(("http://", "https://", "git@")):
            token = (
                credenciales.token_para_clonar(repo["credential_id"])
                if repo.get("credential_id")
                else None
            )
            sha = git_repo.actualizar(ruta, repo["source"], token, repo.get("rama") or "main")
        else:
            try:
                sha = git_repo.commit_actual(ruta)
            except git_repo.GitError:
                sha = None  # ruta local que no es un repo git

        cbm.index_repository(str(ruta))

        # cbm nombra sus proyectos con un slug propio derivado de la ruta; se
        # resuelve matcheando root_path (hace falta en el primer indexado).
        cbm_project = None
        for p in cbm.list_projects():
            if Path(p["root_path"]).resolve() == ruta.resolve():
                cbm_project = p["name"]
                break

        repos_db.marcar_indexado(
            job["repo_name"], commit_sha=sha or job["commit_sha"], cbm_project=cbm_project
        )
        _estado(job_id, "ok", mensaje=f"grafo actualizado en {sha[:12] if sha else 'HEAD'}")
    except Exception as e:  # un worker nunca debe morir por un job malo
        _estado(job_id, "error", mensaje=str(e)[:500])
        repos_db.set_status(job["repo_name"], "error", error=str(e)[:500])


def _bucle() -> None:
    while True:
        tipo, identificador = _cola.get()
        try:
            if tipo == "revision":
                from . import verificacion  # import diferido: evita ciclo al cargar

                verificacion.ejecutar(identificador)
            else:
                _procesar(identificador)
        except Exception:
            pass  # el worker nunca muere; cada rama ya persiste su propio error
        finally:
            _cola.task_done()


def arrancar_worker() -> None:
    """Idempotente: se llama desde el lifespan de la app."""
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_bucle, name="reindex-worker", daemon=True)
        _worker.start()


def _norm(row: dict) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    for campo in ("encolado_en", "iniciado_en", "finalizado_en"):
        if d.get(campo) is not None:
            d[campo] = d[campo].isoformat()
    return d

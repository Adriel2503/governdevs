"""Tests de la cola de reindexado. Requieren Postgres con el esquema aplicado.

Se saltan solos si no hay DATABASE_URL, así el resto de la suite corre en
cualquier lado:
    docker run -d --name pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=context_hub \
        -p 5433:5432 paradedb/paradedb:latest-pg17
    docker exec -i pg psql -U postgres -d context_hub < migrations/schema.sql
    DATABASE_URL=postgresql://postgres:test@localhost:5433/context_hub uv run pytest
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requiere Postgres (DATABASE_URL)"
)


@pytest.fixture()
def repo_de_prueba():
    from app import db as repos_db

    nombre = "repo-test-cola"
    repos_db.upsert(nombre, "https://github.com/o/r.git", "/tmp/r", status="listo")
    yield nombre
    repos_db.delete(nombre)


def test_encolar_devuelve_id_de_job(repo_de_prueba):
    from app import cola

    job_id = cola.encolar(repo_de_prueba, "aaaaaaaa", "push")
    assert job_id is not None

    jobs = cola.listar_jobs(repo_de_prueba)
    assert any(j["id"] == job_id and j["commit_sha"] == "aaaaaaaa" for j in jobs)


def test_encolar_es_idempotente_por_commit(repo_de_prueba):
    """GitHub reintenta los webhooks: el mismo commit no debe reindexar dos veces."""
    from app import cola

    primero = cola.encolar(repo_de_prueba, "bbbbbbbb", "push")
    segundo = cola.encolar(repo_de_prueba, "bbbbbbbb", "push")

    assert primero is not None
    assert segundo is None


def test_eventos_manuales_no_chocan_entre_si(repo_de_prueba):
    """La unicidad aplica solo a 'push'; un reindexado manual siempre se encola."""
    from app import cola

    uno = cola.encolar(repo_de_prueba, "cccccccc", "manual")
    dos = cola.encolar(repo_de_prueba, "cccccccc", "manual")

    assert uno is not None
    assert dos is not None

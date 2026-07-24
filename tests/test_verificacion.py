"""Tests de la verificación de rama (enfoque A).

Las funciones puras (inferencia de capas, armado del comentario) se prueban sin
nada. El enganche del webhook de PR necesita Postgres.
"""

import hashlib
import hmac
import json
import os

import pytest

from app import verificacion, webhook

REQUIERE_BD = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") and os.getenv("CREDENTIALS_KEY")),
    reason="requiere Postgres y CREDENTIALS_KEY",
)

CAPAS = ["endpoints", "ruteo", "handlers", "queries", "validators", "custom-exceptions"]


def _firmar(secreto: str, cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


# --- Funciones puras ---------------------------------------------------------


def test_capas_tocadas_infiere_por_segmento_de_ruta():
    archivos = [
        "src/Handlers/CrearCitaHandler.cs",
        "src/Queries/ListarCitas.cs",
        "README.md",
    ]
    assert verificacion.capas_tocadas(archivos, CAPAS) == ["handlers", "queries"]


def test_capas_tocadas_sin_coincidencias_devuelve_vacio():
    assert verificacion.capas_tocadas(["docs/readme.md", "LICENSE"], CAPAS) == []


def test_capas_tocadas_normaliza_guiones():
    archivos = ["src/Custom_Exceptions/NotFound.cs"]
    assert "custom-exceptions" in verificacion.capas_tocadas(archivos, CAPAS)


def test_comentario_incluye_archivos_impacto_y_violaciones():
    cambios = {
        "changed_files": ["src/Handlers/X.cs"],
        "impacted_symbols": ["Api.Endpoints.Citas"],
    }
    violaciones = [
        {
            "archivo": "src/Handlers/X.cs",
            "regla_violada": "Handlers no llaman queries directo",
            "descripcion": "El handler consulta la base sin pasar por el validator.",
            "fix_sugerido": "Delegar en el validator.",
            "severidad": "alta",
        }
    ]
    cuerpo = verificacion._armar_comentario(
        cambios, ["handlers"], {"handlers": "regla"}, violaciones, "viola"
    )
    assert "❌" in cuerpo
    assert "1** archivo(s)" in cuerpo
    assert "src/Handlers/X.cs" in cuerpo
    assert "Api.Endpoints.Citas" in cuerpo
    assert "Handlers no llaman queries directo" in cuerpo
    assert "Delegar en el validator" in cuerpo


def test_comentario_sin_violaciones_lista_las_reglas_aplicables():
    cuerpo = verificacion._armar_comentario(
        {"changed_files": ["a.cs"], "impacted_symbols": []},
        ["handlers"],
        {"handlers": "regla"},
        [],
        "ok",
    )
    assert "✅" in cuerpo
    assert "No se detectaron incumplimientos" in cuerpo
    assert "**handlers**" in cuerpo


# --- Enganche del webhook ----------------------------------------------------


@pytest.fixture()
def repo_con_webhook():
    from app import cripto
    from app import db as repos_db

    nombre, secreto = "repo-test-pr", "secreto-pr"
    repos_db.upsert(
        nombre, "https://github.com/acme/pr-demo.git", "/tmp/pr", status="listo", rama="main"
    )
    repos_db.guardar_webhook(nombre, 333, cripto.cifrar(secreto))
    yield nombre, secreto
    repos_db.delete(nombre)


def _payload_pr(accion="opened", base="main", numero=7):
    return json.dumps(
        {
            "action": accion,
            "number": numero,
            "repository": {"full_name": "acme/pr-demo"},
            "pull_request": {
                "number": numero,
                "user": {"login": "amadofrias"},
                "head": {"ref": "feature/nuevo-handler", "sha": "head123"},
                "base": {"ref": base, "sha": "base456"},
            },
        }
    ).encode()


@REQUIERE_BD
def test_pr_abierto_crea_revision_y_la_encola(repo_con_webhook):
    nombre, secreto = repo_con_webhook
    cuerpo = _payload_pr()
    status, body = webhook.procesar(cuerpo, _firmar(secreto, cuerpo), "pull_request")

    assert status == 200
    assert body["revision_id"]
    assert body["pr"] == 7

    revisiones = verificacion.listar(nombre)
    assert any(
        r["pr_numero"] == 7 and r["rama"] == "feature/nuevo-handler" and r["autor"] == "amadofrias"
        for r in revisiones
    )


@REQUIERE_BD
def test_pr_hacia_otra_rama_se_ignora(repo_con_webhook):
    _, secreto = repo_con_webhook
    cuerpo = _payload_pr(base="develop", numero=8)
    status, body = webhook.procesar(cuerpo, _firmar(secreto, cuerpo), "pull_request")

    assert status == 200
    assert "no hacia" in body["message"]


@REQUIERE_BD
def test_acciones_de_pr_irrelevantes_se_ignoran(repo_con_webhook):
    _, secreto = repo_con_webhook
    cuerpo = _payload_pr(accion="labeled", numero=9)
    status, body = webhook.procesar(cuerpo, _firmar(secreto, cuerpo), "pull_request")

    assert status == 200
    assert "ignorada" in body["message"]

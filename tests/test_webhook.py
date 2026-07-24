"""Tests del receptor de webhooks, firmando los payloads localmente.

No hace falta GitHub: se genera el HMAC con el mismo secreto que guardaría el
auto-registro, así se ejercita el camino real (incluida la firma inválida).
"""

import hashlib
import hmac
import json
import os

import pytest

from app import webhook

REQUIERE_BD = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") and os.getenv("CREDENTIALS_KEY")),
    reason="requiere Postgres y CREDENTIALS_KEY",
)


def _firmar(secreto: str, cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def _push(full_name="acme/demo", rama="main", mensaje="feat: algo", archivos=None, sha="abc123"):
    archivos = archivos if archivos is not None else ["src/app.py"]
    return json.dumps(
        {
            "ref": f"refs/heads/{rama}",
            "repository": {"full_name": full_name},
            "head_commit": {"id": sha, "message": mensaje},
            "commits": [{"added": [], "modified": archivos, "removed": []}],
        }
    ).encode()


# --- Funciones puras (sin base) ---------------------------------------------


def test_firma_valida_acepta_la_correcta_y_rechaza_la_alterada():
    cuerpo = b'{"a":1}'
    firma = _firmar("s3cr3t", cuerpo)
    assert webhook.firma_valida("s3cr3t", cuerpo, firma)
    assert not webhook.firma_valida("otro", cuerpo, firma)
    assert not webhook.firma_valida("s3cr3t", b'{"a":2}', firma)  # cuerpo manipulado
    assert not webhook.firma_valida("s3cr3t", cuerpo, "")


def test_archivos_tocados_incluye_added_modified_y_removed():
    """Mejora sobre Dokploy, que solo mira 'modified'."""
    payload = {
        "commits": [
            {"added": ["nuevo.py"], "modified": ["viejo.py"], "removed": ["borrado.py"]},
            {"added": [], "modified": ["otro.py"], "removed": []},
        ]
    }
    assert sorted(webhook.archivos_tocados(payload)) == [
        "borrado.py",
        "nuevo.py",
        "otro.py",
        "viejo.py",
    ]


def test_debe_reindexar_respeta_watch_paths():
    assert webhook.debe_reindexar([], ["cualquier.txt"])  # sin filtro, siempre
    assert webhook.debe_reindexar(["src/*"], ["src/app.py"])
    assert not webhook.debe_reindexar(["src/*"], ["docs/readme.md"])
    assert webhook.debe_reindexar(["*.py"], ["docs/readme.md", "a.py"])


def test_evento_no_soportado_da_400():
    status, body = webhook.procesar(b"{}", "firma", "issues")
    assert status == 400


def test_ping_responde_200_sin_tocar_la_base():
    status, body = webhook.procesar(b"{}", "", "ping")
    assert status == 200
    assert "activo" in body["message"]


def test_json_invalido_da_400():
    status, _ = webhook.procesar(b"no-es-json", "firma", "push")
    assert status == 400


def test_falta_la_firma_da_401():
    status, body = webhook.procesar(_push(), "", "push")
    assert status == 401
    assert "x-hub-signature-256" in body["message"]


# --- Integración (requiere base) --------------------------------------------


@pytest.fixture()
def repo_con_webhook():
    from app import cripto
    from app import db as repos_db

    nombre, secreto = "repo-test-webhook", "secreto-de-prueba"
    repos_db.upsert(
        nombre, "https://github.com/acme/demo.git", "/tmp/demo", status="listo", rama="main"
    )
    repos_db.guardar_webhook(nombre, 111, cripto.cifrar(secreto))
    yield nombre, secreto
    repos_db.delete(nombre)


@REQUIERE_BD
def test_firma_invalida_da_401(repo_con_webhook):
    cuerpo = _push()
    status, _ = webhook.procesar(cuerpo, _firmar("secreto-equivocado", cuerpo), "push")
    assert status == 401


@REQUIERE_BD
def test_push_a_main_encola_el_reindexado(repo_con_webhook):
    nombre, secreto = repo_con_webhook
    cuerpo = _push(sha="deadbeef1")
    status, body = webhook.procesar(cuerpo, _firmar(secreto, cuerpo), "push")
    assert status == 200
    assert body["repo"] == nombre
    assert body["job_id"]


@REQUIERE_BD
def test_reintento_del_mismo_commit_no_duplica(repo_con_webhook):
    _, secreto = repo_con_webhook
    cuerpo = _push(sha="deadbeef2")
    firma = _firmar(secreto, cuerpo)
    webhook.procesar(cuerpo, firma, "push")
    status, body = webhook.procesar(cuerpo, firma, "push")  # GitHub reintenta
    assert status == 200
    assert "reintento" in body["message"]


@REQUIERE_BD
def test_push_a_otra_rama_se_ignora(repo_con_webhook):
    _, secreto = repo_con_webhook
    cuerpo = _push(rama="feature/x", sha="deadbeef3")
    status, body = webhook.procesar(cuerpo, _firmar(secreto, cuerpo), "push")
    assert status == 200
    assert "no vigilada" in body["message"]


@REQUIERE_BD
def test_skip_ci_saltea_el_reindexado(repo_con_webhook):
    _, secreto = repo_con_webhook
    cuerpo = _push(mensaje="docs: typo [skip ci]", sha="deadbeef4")
    status, body = webhook.procesar(cuerpo, _firmar(secreto, cuerpo), "push")
    assert status == 200
    assert "Salteado" in body["message"]


@REQUIERE_BD
def test_tag_no_dispara_reindexado(repo_con_webhook):
    _, secreto = repo_con_webhook
    cuerpo = json.dumps(
        {
            "ref": "refs/tags/v1.0.0",
            "repository": {"full_name": "acme/demo"},
            "head_commit": {"id": "t1", "message": "release"},
            "commits": [],
        }
    ).encode()
    status, body = webhook.procesar(cuerpo, _firmar(secreto, cuerpo), "push")
    assert status == 200
    assert "No es un push a una rama" in body["message"]


@REQUIERE_BD
def test_watch_paths_filtra_cambios_irrelevantes(repo_con_webhook):
    from app import db as repos_db

    nombre, secreto = repo_con_webhook
    repos_db.upsert(
        nombre,
        "https://github.com/acme/demo.git",
        "/tmp/demo",
        status="listo",
        rama="main",
        watch_paths=["src/*"],
    )
    cuerpo = _push(archivos=["docs/readme.md"], sha="deadbeef5")
    status, body = webhook.procesar(cuerpo, _firmar(secreto, cuerpo), "push")
    assert status == 200
    assert "Ningún archivo vigilado" in body["message"]

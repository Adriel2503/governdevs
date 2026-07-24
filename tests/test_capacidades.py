"""Tests de /capacidades y del booleano webhook_activo.

Ambos existen para que el frontend sepa qué ofrecer sin romper: qué funciones
están configuradas en este deploy, y si un repo tiene el push automático activo.
"""

import os

import pytest

from app.config import settings

REQUIERE_BD = pytest.mark.skipif(
    not (os.getenv("DATABASE_URL") and os.getenv("CREDENTIALS_KEY")),
    reason="requiere Postgres y CREDENTIALS_KEY",
)


# --- /capacidades ------------------------------------------------------------


def test_sin_api_key_la_auditoria_no_esta_disponible(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "wiki_microservicio_dir", str(tmp_path))

    assert main.capacidades() == {"auditoria": False, "wiki_bundle": False}


def test_con_api_key_la_auditoria_esta_disponible(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-loquesea")
    monkeypatch.setattr(settings, "wiki_microservicio_dir", str(tmp_path))

    assert main.capacidades()["auditoria"] is True


def test_carpeta_vacia_no_cuenta_como_wiki_bundleada(monkeypatch, tmp_path):
    """Existe el directorio pero no hay .md: sincronizar no traería nada, así que
    el frontend tiene que mandar al importador."""
    from app import main

    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "wiki_microservicio_dir", str(tmp_path))

    assert main.capacidades()["wiki_bundle"] is False


def test_detecta_los_md_en_subcarpetas(monkeypatch, tmp_path):
    from app import main

    (tmp_path / "capa").mkdir()
    (tmp_path / "capa" / "handlers.md").write_text("# Handlers", encoding="utf-8")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "wiki_microservicio_dir", str(tmp_path))

    assert main.capacidades()["wiki_bundle"] is True


def test_carpeta_inexistente_no_rompe(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "wiki_microservicio_dir", str(tmp_path / "no-existe"))

    assert main.capacidades()["wiki_bundle"] is False


# --- webhook_activo ----------------------------------------------------------


def test_las_columnas_publicas_no_filtran_el_secreto_del_webhook():
    """Regresión de seguridad: _COLS alimenta la API REST. El secreto del webhook
    solo puede salir por datos_webhook(), que es de uso interno."""
    from app import db

    assert "webhook_activo" in db._COLS
    assert "webhook_secret" not in db._COLS


@REQUIERE_BD
def test_webhook_activo_refleja_si_el_repo_tiene_hook():
    from app import cripto
    from app import db as repos_db

    nombre = "repo-test-webhook-activo"
    repos_db.upsert(nombre, "https://github.com/acme/wa.git", "/tmp/wa")
    try:
        assert repos_db.get(nombre)["webhook_activo"] is False

        repos_db.guardar_webhook(nombre, 999, cripto.cifrar("secreto"))
        repo = repos_db.get(nombre)

        assert repo["webhook_activo"] is True
        assert "webhook_secret_cifrado" not in repo
    finally:
        repos_db.delete(nombre)

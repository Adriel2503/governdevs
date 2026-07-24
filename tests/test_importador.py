"""Tests del importador de lineamientos por ZIP.

El caso que motivó esto: un export de wiki de 388 MB del que solo 2.9 MB son
.md — el resto es .git y adjuntos. Extraer todo era desperdiciar disco y abrir
la puerta a una bomba de descompresión.
"""

import io
import zipfile

import pytest

from app import importador


def _zip(archivos: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, contenido in archivos.items():
            z.writestr(nombre, contenido)
    return buf.getvalue()


def _limpiar(res):
    from app import git_repo

    git_repo.borrar_arbol(importador.IMPORTS_DIR / res["import_id"])


def test_solo_extrae_los_md_y_descarta_el_resto():
    """Lo que motivó el cambio: en el ZIP real, el 99% del peso son binarios que
    se descartarían igual al indexar."""
    datos = _zip(
        {
            "Lineamientos/handlers.md": b"# Handlers",
            "Lineamientos/queries.md": b"# Queries",
            ".attachments/diagrama.png": b"\x89PNG" + b"x" * 5000,
            ".git/objects/ab/cdef": b"binario",
            "README.txt": b"no es markdown",
        }
    )
    res = importador.scan_zip(datos, fuente="test.zip")
    base = importador.IMPORTS_DIR / res["import_id"]

    try:
        extraidos = sorted(p.name for p in base.rglob("*") if p.is_file() and p.name != ".fuente")
        assert extraidos == ["handlers.md", "queries.md"]
        assert not (base / ".attachments").exists()
        assert not (base / ".git").exists()
    finally:
        _limpiar(res)


def test_ignora_los_md_que_viven_dentro_de_git():
    datos = _zip({"docs/real.md": b"# Real", ".git/hooks/ejemplo.md": b"# Ruido"})
    res = importador.scan_zip(datos)
    base = importador.IMPORTS_DIR / res["import_id"]

    try:
        assert [p.name for p in base.rglob("*.md")] == ["real.md"]
    finally:
        _limpiar(res)


def test_el_arbol_de_carpetas_cuenta_los_md():
    datos = _zip(
        {
            "Wiki/Lineamientos/a.md": b"a",
            "Wiki/Lineamientos/b.md": b"b",
            "Wiki/Otros/c.md": b"c",
        }
    )
    res = importador.scan_zip(datos)

    try:
        por_ruta = {c["path"]: c["archivos_md"] for c in res["carpetas"]}
        assert por_ruta["Wiki/Lineamientos"] == 2
        assert por_ruta["Wiki"] == 3
        assert por_ruta[""] == 3  # la raíz: todo
    finally:
        _limpiar(res)


def test_zip_sin_markdown_da_error_claro():
    datos = _zip({"foto.png": b"binario", "notas.txt": b"texto"})
    with pytest.raises(importador.ImportadorError, match="ningún archivo .md"):
        importador.scan_zip(datos)


def test_zip_invalido_da_error_claro():
    with pytest.raises(importador.ImportadorError, match="inválido"):
        importador.scan_zip(b"esto no es un zip")


def test_zip_slip_no_escapa_de_la_carpeta_temporal():
    """Una entrada con ../ intentando escribir fuera del destino aborta todo."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../escapado.md", b"# No deberia salir")

    with pytest.raises(importador.ImportadorError, match="zip-slip"):
        importador.scan_zip(buf.getvalue())


def test_acepta_una_ruta_en_disco(tmp_path):
    """El camino de producción: la subida se escribe a disco por partes y se le
    pasa la ruta, no los bytes."""
    archivo = tmp_path / "wiki.zip"
    archivo.write_bytes(_zip({"docs/regla.md": b"# Regla"}))

    res = importador.scan_zip(archivo, fuente="wiki.zip")
    try:
        assert res["fuente"] == "wiki.zip"
        assert any(c["path"] == "docs" for c in res["carpetas"])
    finally:
        _limpiar(res)

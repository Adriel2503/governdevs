"""Tests de la capa git. No necesitan red ni Postgres."""

import os
import stat
from pathlib import Path

from app import git_repo


def test_url_con_token_inyecta_solo_en_https():
    assert (
        git_repo.url_con_token("https://github.com/o/r.git", "tok")
        == "https://tok@github.com/o/r.git"
    )
    # SSH no lleva token en la URL: se deja intacta.
    assert git_repo.url_con_token("git@github.com:o/r.git", "tok") == "git@github.com:o/r.git"
    # Sin token, la URL no se toca.
    assert git_repo.url_con_token("https://github.com/o/r.git", None) == "https://github.com/o/r.git"


def test_borrar_arbol_elimina_archivos_de_solo_lectura(tmp_path: Path):
    """Regresión: shutil.rmtree(ignore_errors=True) fallaba EN SILENCIO con los
    objetos de solo-lectura de .git y dejaba clones huérfanos en disco."""
    repo = tmp_path / "clon"
    objetos = repo / ".git" / "objects"
    objetos.mkdir(parents=True)
    archivo = objetos / "obj"
    archivo.write_text("contenido")
    os.chmod(archivo, stat.S_IREAD)  # como deja git sus objetos

    git_repo.borrar_arbol(repo)

    assert not repo.exists()


def test_borrar_arbol_no_falla_si_no_existe(tmp_path: Path):
    git_repo.borrar_arbol(tmp_path / "no-existe")  # no debe lanzar

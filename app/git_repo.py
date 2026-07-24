"""Operaciones git sobre los repos vigilados (clon persistente).

Dos decisiones que difieren de Dokploy a propósito:

1. **Clone COMPLETO, no `--depth 1`.** Dokploy hace shallow porque solo necesita
   el árbol para buildear. Nosotros necesitamos **historia**: `detect_changes` de
   cbm diffea `main` contra una rama, y con un clon shallow ese diff es imposible.

2. **El token NUNCA queda en `.git/config`.** Dokploy no se preocupa porque
   re-clona (con `rm -rf`) en cada deploy; nuestro clon **persiste** entre
   reindexados, así que el remote se guarda limpio y el token se inyecta de forma
   efímera solo en la operación en curso.

Sin shell: `subprocess.run` con lista de argumentos → inmune a command injection
(por eso no hace falta el `shell-quote` que sí necesita Dokploy).
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class GitError(RuntimeError):
    pass


def borrar_arbol(path: Path | str) -> None:
    """rmtree robusto para clones git.

    En Windows los objetos bajo `.git/objects` quedan de solo-lectura y
    `shutil.rmtree` falla; con `ignore_errors=True` el fallo pasa **en silencio**
    y el clon queda huérfano en disco. Acá se limpia el flag de solo-lectura y se
    reintenta, así el borrado es real en todas las plataformas.
    """
    p = Path(path)
    if not p.exists():
        return

    def _forzar(func, ruta, _exc):
        try:
            os.chmod(ruta, stat.S_IWRITE)
            func(ruta)
        except OSError:
            pass

    shutil.rmtree(p, onexc=_forzar)


def _run(
    args: list[str],
    *,
    desc: str,
    cwd: Path | None = None,
    timeout: int = 600,
    token: str | None = None,
) -> str:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {desc} excedió el tiempo límite ({timeout}s).") from e
    except FileNotFoundError as e:
        raise GitError("No se encontró el binario 'git' en el PATH.") from e

    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if token:
            err = err.replace(token, "***")  # nunca filtrar el token en un error
        raise GitError(f"git {desc} falló: {err}")
    return r.stdout.strip()


def url_con_token(url: str, token: str | None) -> str:
    """Inyecta el token en una URL https solo para la operación en curso."""
    if not token or not url.startswith("https://"):
        return url
    p = urlparse(url)
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    return urlunparse(p._replace(netloc=f"{token}@{host}"))


def clonar(url: str, token: str | None, dest: Path, rama: str = "main") -> None:
    """Clon completo y persistente de `rama`. Deja el remote SIN credencial."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        ["-c", "credential.helper=", "clone", "--branch", rama, url_con_token(url, token), str(dest)],
        desc="clone",
        token=token,
    )
    # El clon sobrevive entre reindexados: no dejamos el token escrito en disco.
    _run(["remote", "set-url", "origin", url], desc="remote set-url", cwd=dest, token=token)


def fetch(dest: Path, url: str, token: str | None, rama: str | None = None) -> None:
    """Trae novedades del remoto (incremental). El token va efímero en la URL."""
    args = ["-c", "credential.helper=", "fetch", "--prune", url_con_token(url, token)]
    if rama:
        args.append(f"+refs/heads/{rama}:refs/remotes/origin/{rama}")
    _run(args, desc="fetch", cwd=dest, token=token)


def checkout(dest: Path, ref: str) -> None:
    _run(["checkout", "--force", ref], desc="checkout", cwd=dest)


def actualizar(dest: Path, url: str, token: str | None, rama: str = "main") -> str:
    """Pone el clon persistente al día con el remoto (fetch + reset duro) y
    devuelve el SHA resultante. Es el primitivo del reindexado incremental que
    dispara el webhook: barato comparado con re-clonar."""
    fetch(dest, url, token, rama)
    _run(["reset", "--hard", f"origin/{rama}"], desc="reset --hard", cwd=dest)
    return commit_actual(dest)


def commit_actual(dest: Path, ref: str = "HEAD") -> str:
    return _run(["rev-parse", ref], desc="rev-parse", cwd=dest)


def archivos_cambiados(dest: Path, base: str, head: str) -> list[str]:
    """Archivos que difieren entre `base` y `head` (three-dot: desde que
    divergieron), que es exactamente el conjunto que introduce una rama/PR."""
    out = _run(["diff", "--name-only", f"{base}...{head}"], desc="diff", cwd=dest)
    return [line for line in out.splitlines() if line.strip()]

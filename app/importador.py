"""Importación de lineamientos desde una fuente externa.

Dos fuentes, mismo destino:
  - repo git (GitHub con token ahora; Azure DevOps en Fase 2)
  - archivo ZIP subido (sin token, sin depender de la red)

Flujo en dos pasos:
  1. escanear  → descarga/descomprime a una carpeta TEMPORAL y devuelve el árbol
                 de carpetas (con conteo de .md) para que el admin elija la raíz.
  2. indexar   → toma la carpeta raíz elegida (ej. "Microservicio"), indexa sus
                 .md en `lineamientos` y BORRA la descarga temporal.

Solo persiste en la BD lo de la carpeta seleccionada; la descarga completa es
efímera. Mismo patrón que los repos de código (clonar → usar → borrar).
"""

import io
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

from . import reglas
from .config import settings

IMPORTS_DIR = Path(settings.workspace_dir) / "imports"
_META = ".fuente"  # guarda la fuente entre el paso 1 y el 2


class ImportadorError(RuntimeError):
    pass


def _nueva_carpeta() -> tuple[str, Path]:
    import_id = uuid.uuid4().hex[:12]
    dest = IMPORTS_DIR / import_id
    dest.mkdir(parents=True, exist_ok=True)
    return import_id, dest


def _arbol_carpetas(base: Path) -> list[dict]:
    """Para cada carpeta que contenga .md (recursivo), cuánto .md hay debajo. El
    admin elige una como raíz a indexar. '' = raíz (todo)."""
    rutas = [md.relative_to(base) for md in base.rglob("*.md")]
    counts: dict[str, int] = {}
    for md in rutas:
        for anc in [md.parent, *md.parent.parents]:
            key = "" if str(anc) == "." else str(anc).replace("\\", "/")
            counts[key] = counts.get(key, 0) + 1
    return sorted(
        ({"path": k, "archivos_md": v} for k, v in counts.items()),
        key=lambda d: d["path"],
    )


def _resultado_scan(import_id: str, dest: Path, fuente: str) -> dict:
    (dest / _META).write_text(fuente, encoding="utf-8")
    return {"import_id": import_id, "fuente": fuente, "carpetas": _arbol_carpetas(dest)}


def scan_git(url: str, token: str | None = None) -> dict:
    """Clona (shallow) el repo a una carpeta temporal. Con token para repos
    privados (GitHub PAT). El token se inyecta en la URL solo para el clone y
    nunca se loguea ni se filtra en errores."""
    import_id, dest = _nueva_carpeta()
    clone_url = url
    if token and url.startswith("https://"):
        clone_url = url.replace("https://", f"https://{token}@", 1)
    try:
        r = subprocess.run(
            ["git", "-c", "credential.helper=", "clone", "--depth", "1", clone_url, str(dest)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise ImportadorError("El clone superó el tiempo límite (300s).")
    if r.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        err = (r.stderr or "").strip()
        if token:
            err = err.replace(token, "***")  # no filtrar el token
        raise ImportadorError(f"git clone falló: {err}")
    return _resultado_scan(import_id, dest, fuente=url)


def scan_zip(file_bytes: bytes, fuente: str = "zip") -> dict:
    """Descomprime un ZIP a una carpeta temporal, con protección básica contra
    zip-slip (entradas que escapen del destino)."""
    import_id, dest = _nueva_carpeta()
    dest_real = dest.resolve()
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            for nombre in z.namelist():
                destino = (dest / nombre).resolve()
                if not str(destino).startswith(str(dest_real)):
                    raise ImportadorError("El ZIP contiene rutas inseguras (zip-slip).")
            z.extractall(dest)
    except zipfile.BadZipFile:
        shutil.rmtree(dest, ignore_errors=True)
        raise ImportadorError("Archivo ZIP inválido.")
    except ImportadorError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return _resultado_scan(import_id, dest, fuente=fuente)


def indexar(import_id: str, carpeta: str = "") -> dict:
    """Indexa los .md bajo la carpeta elegida en `lineamientos` y borra la
    descarga temporal. carpeta='' indexa desde la raíz (todo)."""
    dest = IMPORTS_DIR / import_id
    if not dest.is_dir():
        raise ImportadorError("import_id no encontrado o ya procesado.")

    dest_real = dest.resolve()
    base = (dest / carpeta).resolve()
    if not str(base).startswith(str(dest_real)) or not base.is_dir():
        raise ImportadorError(f"Carpeta inválida: {carpeta!r}")

    fuente_path = dest / _META
    fuente = fuente_path.read_text(encoding="utf-8").strip() if fuente_path.exists() else "import"
    if carpeta:
        fuente = f"{fuente}#{carpeta}"

    try:
        count = reglas.indexar_carpeta(base, fuente)
    finally:
        # solo persiste en la BD lo de la carpeta elegida; la descarga se borra
        shutil.rmtree(dest, ignore_errors=True)
    return {"indexadas": count, "carpeta": carpeta or "(raíz)", "fuente": fuente}

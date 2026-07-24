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
import subprocess
import uuid
import zipfile
from pathlib import Path

from . import git_repo, reglas
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
        git_repo.borrar_arbol(dest)
        raise ImportadorError("El clone superó el tiempo límite (300s).")
    if r.returncode != 0:
        git_repo.borrar_arbol(dest)
        err = (r.stderr or "").strip()
        if token:
            err = err.replace(token, "***")  # no filtrar el token
        raise ImportadorError(f"git clone falló: {err}")
    return _resultado_scan(import_id, dest, fuente=url)


def _es_markdown_util(info: zipfile.ZipInfo) -> bool:
    """Solo interesan los .md: es lo único que se indexa. Un export de wiki suele
    venir con .git y adjuntos (imágenes, GIFs, diagramas) que pesan órdenes de
    magnitud más y se descartarían igual después."""
    if info.is_dir() or not info.filename.lower().endswith(".md"):
        return False
    partes = info.filename.replace("\\", "/").split("/")
    return ".git" not in partes


def scan_zip(origen: "bytes | bytearray | Path", fuente: str = "zip") -> dict:
    """Extrae los .md de un ZIP a una carpeta temporal.

    Acepta una ruta en disco (camino normal: la subida se escribe por partes y
    nunca entra completa en memoria) o los bytes ya cargados, que es lo cómodo
    para los tests.

    Del ZIP se extraen ÚNICAMENTE los .md. Además de ahorrar disco, acota el
    daño de un archivo hostil: una bomba de descompresión hecha de binarios
    gigantes no llega a escribirse.

    Protege contra zip-slip: una entrada cuyo destino resuelto caiga fuera de la
    carpeta temporal aborta todo.
    """
    import_id, dest = _nueva_carpeta()
    dest_real = dest.resolve()
    entrada = io.BytesIO(origen) if isinstance(origen, (bytes, bytearray)) else origen
    try:
        with zipfile.ZipFile(entrada) as z:
            miembros = []
            for info in z.infolist():
                if not _es_markdown_util(info):
                    continue
                destino = (dest / info.filename).resolve()
                if not str(destino).startswith(str(dest_real)):
                    raise ImportadorError("El ZIP contiene rutas inseguras (zip-slip).")
                miembros.append(info)
            if not miembros:
                raise ImportadorError("El ZIP no contiene ningún archivo .md.")
            z.extractall(dest, members=miembros)
    except zipfile.BadZipFile:
        git_repo.borrar_arbol(dest)
        raise ImportadorError("Archivo ZIP inválido.")
    except ImportadorError:
        git_repo.borrar_arbol(dest)
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
        git_repo.borrar_arbol(dest)
    return {"indexadas": count, "carpeta": carpeta or "(raíz)", "fuente": fuente}

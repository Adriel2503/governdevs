"""Prepara un ZIP liviano de una wiki para importarlo al hub.

Por qué existe
-------------
Un clon de wiki de Azure DevOps trae el contenido mezclado con todo lo que la
hace pesada. En la wiki de Real Plaza, medido:

    1084 .png   111.5 MB
       7 .gif    33.8 MB
      29 .zip    23.7 MB
     229 .md      2.9 MB   <- lo único que el hub indexa
     .git        202.0 MB

Comprimir la carpeta entera da ~366 MB. Subir eso falla: la petición tarda
minutos y algo en el camino la corta antes de que el servidor la reciba. Y no
tiene sentido — el hub descarta todo lo que no sea .md apenas lo recibe.

Este script hace ese descarte ANTES de subir: recorre la wiki, se queda solo con
los .md y **conserva la estructura de carpetas**, que es lo que después permite
elegir desde qué carpeta indexar.

    366 MB  ->  1.5 MB

Uso
---
    uv run python scripts/preparar_wiki.py "C:\\ruta\\a\\Wiki-Arquitectura.wiki"
    uv run python scripts/preparar_wiki.py <carpeta> -o wiki.zip
    uv run python scripts/preparar_wiki.py <carpeta> --incluir-vacios
    uv run python scripts/preparar_wiki.py <carpeta> --ext md,txt

Sobre los .md vacíos
--------------------
En una wiki de Azure DevOps, una página con hijas se guarda como un .md de 0
bytes MÁS una carpeta con el contenido real. Son portadas de carpeta, no
documentos: en esta wiki hay 32 de 229. Por defecto se excluyen — la estructura
de carpetas se conserva igual, porque la traen las rutas de los hijos. Con
--incluir-vacios se mantienen.

El formato es ZIP y no RAR: RAR es propietario y no se puede generar sin WinRAR.
El importador del hub acepta ZIP.
"""

import argparse
import sys
import zipfile
from pathlib import Path

EXCLUIR_SIEMPRE = {".git"}  # el historial pesa más que el contenido


def _legible(n_bytes: int) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024 or unidad == "GB":
            return f"{n_bytes:.1f} {unidad}" if unidad != "B" else f"{n_bytes} B"
        n_bytes /= 1024
    return f"{n_bytes:.1f} GB"


def recolectar(origen: Path, extensiones: set[str], incluir_vacios: bool):
    """Devuelve (a_incluir, descartados, vacios). Cada elemento es un Path."""
    a_incluir, descartados, vacios = [], [], []

    for p in sorted(origen.rglob("*")):
        if not p.is_file():
            continue
        if EXCLUIR_SIEMPRE & set(p.relative_to(origen).parts):
            descartados.append(p)
            continue
        if p.suffix.lower().lstrip(".") not in extensiones:
            descartados.append(p)
            continue
        if p.stat().st_size == 0 and not incluir_vacios:
            vacios.append(p)
            continue
        a_incluir.append(p)

    return a_incluir, descartados, vacios


def empaquetar(origen: Path, destino: Path, archivos: list[Path]) -> None:
    """Escribe el ZIP conservando la ruta relativa de cada archivo — sin eso, el
    hub no podría ofrecer el árbol de carpetas para elegir qué indexar."""
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in archivos:
            z.write(p, p.relative_to(origen).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deja solo la estructura de carpetas y los .md de una wiki, en un ZIP liviano.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("origen", type=Path, help="Carpeta de la wiki (el clon completo)")
    parser.add_argument("-o", "--salida", type=Path, help="ZIP a generar (default: <origen>-solo-md.zip)")
    parser.add_argument(
        "--ext",
        default="md",
        help="Extensiones a conservar, separadas por coma (default: md). El hub solo indexa .md.",
    )
    parser.add_argument(
        "--incluir-vacios",
        action="store_true",
        help="Conservar los .md de 0 bytes (portadas de carpeta de Azure DevOps).",
    )
    args = parser.parse_args()

    # La consola de Windows suele venir en cp1252: los acentos salen ilegibles y
    # una ruta con un caracter fuera de esa tabla aborta el script con
    # UnicodeEncodeError. errors="replace" además evita que un nombre exótico lo
    # tumbe justo al final, después de haber hecho todo el trabajo.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass  # stdout redirigido a algo que no lo soporta: seguimos igual

    origen = args.origen.expanduser().resolve()
    if not origen.is_dir():
        print(f"error: no existe la carpeta {origen}", file=sys.stderr)
        return 1

    destino = (args.salida or origen.parent / f"{origen.name}-solo-md.zip").expanduser().resolve()
    extensiones = {e.strip().lower().lstrip(".") for e in args.ext.split(",") if e.strip()}

    print(f"Origen : {origen}")
    print(f"Destino: {destino}")
    print(f"Conservando: {', '.join('.' + e for e in sorted(extensiones))}\n")

    a_incluir, descartados, vacios = recolectar(origen, extensiones, args.incluir_vacios)

    if not a_incluir:
        print("error: no se encontró ningún archivo para incluir.", file=sys.stderr)
        return 1

    empaquetar(origen, destino, a_incluir)

    peso_origen = sum(p.stat().st_size for p in a_incluir + descartados + vacios)
    peso_zip = destino.stat().st_size
    carpetas = {p.relative_to(origen).parent.as_posix() for p in a_incluir}

    print(f"  incluidos   : {len(a_incluir)} archivos en {len(carpetas)} carpetas")
    if vacios:
        print(f"  vacíos      : {len(vacios)} portadas de carpeta omitidas (--incluir-vacios para conservarlas)")
    print(f"  descartados : {len(descartados)} archivos ({_legible(sum(p.stat().st_size for p in descartados))})")
    print(f"\n  {_legible(peso_origen)} en disco  ->  {_legible(peso_zip)} comprimido")
    if peso_origen:
        print(f"  reducción del {100 * (1 - peso_zip / peso_origen):.1f}%")

    print(f"\nListo. Subilo desde el panel «Lineamientos oficiales» -> «Subir ZIP».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

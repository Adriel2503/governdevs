"""Tests del mapeo de documentos a reglas.

Medido sobre la wiki real de Real Plaza (229 .md): 32 son portadas de carpeta
vacías, 16 slugs colisionan afectando a 32 documentos, y hay 26 apariciones de
%2D en los nombres. Estos tests fijan el comportamiento que corrige eso.
"""

from app import reglas


# --- Decodificación de los nombres de Azure DevOps ---------------------------


def test_decodifica_el_guion_codificado_de_azure_devops():
    slug = reglas._slug_capa("Lineamientos-de-desarrollo/Custom-Exceptions-%2D-Status-Code-400%2Dx.md")
    assert "%2d" not in slug
    assert slug == "lineamientos-de-desarrollo/custom-exceptions-status-code-400-x"


def test_el_titulo_sale_legible():
    assert reglas._titulo("Lineamientos-de-desarrollo/Endpoints.md") == "Endpoints"
    assert reglas._titulo("2.-Application-Handler-con-Wolverine.md") == "Application Handler Con Wolverine"


# --- El slug sale de la ruta, no del nombre suelto ---------------------------


def test_dos_documentos_con_el_mismo_nombre_no_colisionan():
    """El caso real: Endpoints.md existe en la raíz del arquetipo y otra vez
    dentro de Lineamientos-de-desarrollo/, y son documentos distintos."""
    a = reglas._slug_capa("2.-Endpoints.md")
    b = reglas._slug_capa("Lineamientos-de-desarrollo/Endpoints.md")

    assert a == "endpoints"
    assert b == "lineamientos-de-desarrollo/endpoints"
    assert a != b


def test_quita_el_prefijo_de_orden_de_la_wiki():
    assert reglas._slug_capa("0.-Instalación.md").endswith("instalación")
    assert reglas._slug_capa("8.-Consideraciones/8.1-Claims-de-autenticación.md") == (
        "consideraciones/claims-de-autenticación"
    )


def test_normaliza_espacios_y_guiones_repetidos():
    assert reglas._slug_capa("Custom  Exceptions -- 400.md") == "custom-exceptions-400"


def test_ruta_profunda_conserva_toda_la_jerarquia():
    slug = reglas._slug_capa("Desarrollo/Arquetipos/Microservicio/9.-Logging.md")
    assert slug == "desarrollo/arquetipos/microservicio/logging"


# --- Sin colisiones sobre el corpus real -------------------------------------


def test_el_corpus_real_no_produce_colisiones():
    """Las 16 colisiones medidas en la wiki desaparecen al usar la ruta."""
    rutas = [
        "2.-Endpoints.md",
        "Lineamientos-de-desarrollo/Endpoints.md",
        "1.-Startup.md",
        "Otro-Arquetipo/1.-Startup.md",
        "4.-Application-Validators.md",
        "Lineamientos-de-desarrollo/Validators.md",
    ]
    slugs = [reglas._slug_capa(r) for r in rutas]
    assert len(set(slugs)) == len(slugs)

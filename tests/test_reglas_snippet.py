"""Tests del fragmento que devuelve la búsqueda BM25.

Regresión de la migración SQLite/FTS5 -> ParadeDB: cambió cómo el motor marca
las coincidencias (de '**' a <b>) y además empezó a escapar el texto como HTML,
pero los consumidores —la interfaz y el tool MCP `buscar_regla`— siguieron
leyéndolo con las reglas del motor viejo.
"""

from app.reglas import _partir_snippet


def test_las_coincidencias_quedan_marcadas():
    texto, tramos = _partir_snippet("usar <b>FluentValidation</b> siempre")

    assert texto == "usar FluentValidation siempre"
    assert tramos == [
        {"t": "usar ", "hit": False},
        {"t": "FluentValidation", "hit": True},
        {"t": " siempre", "hit": False},
    ]


def test_decodifica_las_entidades_html():
    """ParadeDB escapa el fragmento. Sin deshacerlo, en pantalla se leía
    literalmente `WHERE status = &#x27;active&#x27;`."""
    texto, _ = _partir_snippet("WHERE <b>status</b> = &#x27;active&#x27; AND a &lt; b")

    assert texto == "WHERE status = 'active' AND a < b"


def test_los_asteriscos_del_markdown_no_son_marcas():
    """El bug original: el frontend partía por '**', así que la negrita del
    documento se resaltaba como si fuera la coincidencia."""
    _, tramos = _partir_snippet("Mantener **cohesivas** y <b>legibles</b>")

    assert [t["t"] for t in tramos if t["hit"]] == ["legibles"]
    assert "**cohesivas**" in "".join(t["t"] for t in tramos)


def test_varias_coincidencias_en_un_fragmento():
    _, tramos = _partir_snippet("<b>Unit</b> <b>of</b> <b>Work</b>")

    assert [t["t"] for t in tramos if t["hit"]] == ["Unit", "of", "Work"]


def test_fragmento_sin_coincidencias_y_vacio():
    """paradedb.snippet() puede devolver NULL: no debe reventar la búsqueda."""
    assert _partir_snippet("solo texto") == ("solo texto", [{"t": "solo texto", "hit": False}])
    assert _partir_snippet(None) == ("", [])
    assert _partir_snippet("") == ("", [])


def test_marca_que_abarca_varias_lineas():
    texto, tramos = _partir_snippet("a <b>dos\nlineas</b> b")

    assert texto == "a dos\nlineas b"
    assert [t["t"] for t in tramos if t["hit"]] == ["dos\nlineas"]

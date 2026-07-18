"""Tests de las funciones puras de la vista Research (api/services/research_sql.py).

Congela la derivación de `tipo` desde el asunto (sin columna todavía) y el parseo
del destilado jsonb. La lectura SQL se prueba contra DB en integration. Doc madre:
docs/VISTA_RESEARCH.md.
"""
from __future__ import annotations

from api.services import research_sql as svc


def test_tipo_de_asunto():
    # el diario, aunque venga reenviado con RV:/Fwd:
    assert svc._tipo_de_asunto("El día en pocas líneas") == "diario"
    assert svc._tipo_de_asunto("RV: El día en pocas líneas") == "diario"
    assert svc._tipo_de_asunto("Fwd: El día en pocas lineas") == "diario"
    assert svc._tipo_de_asunto("Informe mensual de julio") == "mensual"
    assert svc._tipo_de_asunto("Otra cosa") == "otro"
    assert svc._tipo_de_asunto(None) == "otro"


def test_limpiar_para_mostrar():
    """Saca headers del reenvío (De:/Para:/Asunto:/Enviados:) y el pie de 1816,
    conserva el research y los párrafos. El crudo original no se toca."""
    crudo = (
        "De: Research 1816 <research@1816.com.ar>\n"
        "Enviados: viernes, 17 de julio de 2026 7:41:43\n"
        "Para: Mollo Nicolas <nicolas.mollo@acavalores.com.ar>\n"
        "Asunto: El día en pocas líneas\n"
        "\n"
        "________________________________\n"
        "\n"
        "[https://gallery.mailchimp.com/x/images/y.png] 17 de julio de 2026 EL DÍA EN POCAS LÍNEAS\n"
        "\n"
        "EL CENTRAL COMPRÓ USD 230 MM EN EL MLC. En las tres semanas anteriores... "
        "El aviso puede leerse acá https://mailchi.mp/abc/def. "
        "EL TESORO RETIRARÁ $2,4 B DEL SISTEMA HOY CON LA LIQUIDACIÓN. El stock subió.\n"
        "\n"
        "Cualquier duda estamos a disposición.\n"
        "1816 | ECONOMÍA Y ESTRATEGIA\n"
        "Copyright © 2026 1816\n"
    )
    out = svc._limpiar_para_mostrar(crudo)
    assert "research@1816.com.ar" not in out          # header de reenvío fuera
    assert "Para:" not in out and "Asunto:" not in out
    assert "mailchimp" not in out and "___" not in out  # imagen y separador fuera
    assert "http" not in out and "mailchi" not in out    # links (inline y de imagen) fuera
    assert "EL DÍA EN POCAS LÍNEAS" not in out          # masthead redundante fuera
    assert "EL CENTRAL COMPRÓ USD 230 MM" in out        # cuerpo queda
    assert "Copyright" not in out and "disposici" not in out  # pie cortado
    # los dos items quedan en párrafos separados (split por titular)
    assert "\n\nEL TESORO RETIRARÁ" in out
    assert svc._limpiar_para_mostrar(None) == ""
    assert svc._limpiar_para_mostrar("") == ""


def test_parse_destilado():
    d = {"resumen": "x", "temas": ["a"], "hechos": []}
    assert svc._parse_destilado(d) == d                      # ya dict → tal cual
    assert svc._parse_destilado('{"resumen": "x"}') == {"resumen": "x"}  # texto → parseado
    assert svc._parse_destilado(None) is None
    assert svc._parse_destilado("no es json") is None        # basura → None, no rompe

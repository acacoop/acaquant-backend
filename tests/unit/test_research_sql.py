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


def test_parse_destilado():
    d = {"resumen": "x", "temas": ["a"], "hechos": []}
    assert svc._parse_destilado(d) == d                      # ya dict → tal cual
    assert svc._parse_destilado('{"resumen": "x"}') == {"resumen": "x"}  # texto → parseado
    assert svc._parse_destilado(None) is None
    assert svc._parse_destilado("no es json") is None        # basura → None, no rompe

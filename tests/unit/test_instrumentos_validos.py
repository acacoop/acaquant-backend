"""LA validación de símbolos — una sola, aplicada en el WS para todos los motores.

Lo que se congela acá es el modo de fallar. Filtrar de más deja la mesa sin
precios; no filtrar deja pasar símbolos muertos, que es el comportamiento de
toda la vida. Ante la duda se elige el segundo, y eso tiene que estar clavado
en un test — es la clase de decisión que un refactor bien intencionado invierte.
"""
from __future__ import annotations

from core.instrumentos_validos import filtrar

_UNIVERSO = {"MERV - XMEV - AL30 - 24hs", "MERV - XMEV - AL30D - 24hs"}


def test_lo_que_no_existe_no_se_suscribe():
    ok, fuera = filtrar(
        ["MERV - XMEV - AL30 - 24hs", "MERV - XMEV - VSCWO - 24hs"], _UNIVERSO)
    assert ok == ["MERV - XMEV - AL30 - 24hs"]
    assert fuera == ["MERV - XMEV - VSCWO - 24hs"]


def test_sin_universo_no_se_filtra_nada():
    """`None` = no hay criterio confiable (Postgres caído, catálogo vacío). Un
    bug de infraestructura no puede dejar a los motores sin suscribir."""
    tickers = ["MERV - XMEV - LO_QUE_SEA - 24hs"]
    assert filtrar(tickers, None) == (tickers, [])


def test_universo_vacio_no_es_lo_mismo_que_none():
    """Un set vacío SÍ es un criterio (aunque raro) y descarta todo. Que se
    distinga de `None` es justamente lo que hace segura la degradación."""
    assert filtrar(["X"], set()) == ([], ["X"])


def test_preserva_el_orden_y_no_deduplica():
    """Quien llama arma sus lotes de 50: el filtro no le puede cambiar el
    contenido más allá de sacar lo inválido."""
    entrada = ["MERV - XMEV - AL30 - 24hs"] * 3
    assert filtrar(entrada, _UNIVERSO)[0] == entrada


def test_lista_vacia_no_rompe():
    assert filtrar([], _UNIVERSO) == ([], [])
    assert filtrar(None, _UNIVERSO) == ([], [])

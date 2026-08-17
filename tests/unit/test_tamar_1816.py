"""Tests de la lógica pura de `jobs/tamar_1816.py` (sin base ni red).

Lo que se protege son las tres decisiones que, si se pierden, hacen que el job
siga corriendo verde y escriba de menos o escriba mal:

  1. La GRAFÍA del ticker sale del catálogo de 1816, no se arma concatenando.
  2. Un `null` de 1816 no se persiste como si fuera un valor.
  3. La fecha de la rueda nunca queda en un día sin mercado.
"""
from __future__ import annotations

import datetime as dt

from core.mercado_1816 import _habil_anterior
from jobs.tamar_1816 import _mejor_por_pata, _sufijo


def test_habil_anterior_nunca_cae_en_fin_de_semana():
    """El incidente que lo motivó: pedir sin fecha un DOMINGO devolvió los 6
    campos en null y pareció que el campo `spread` no existía."""
    assert _habil_anterior(dt.date(2026, 8, 16)) == dt.date(2026, 8, 14)  # dom → vie
    assert _habil_anterior(dt.date(2026, 8, 15)) == dt.date(2026, 8, 14)  # sáb → vie
    assert _habil_anterior(dt.date(2026, 8, 17)) == dt.date(2026, 8, 14)  # lun → vie
    assert _habil_anterior(dt.date(2026, 8, 14)) == dt.date(2026, 8, 13)  # vie → jue


def test_sufijo_separa_la_pata_del_ticker():
    assert _sufijo("TXMD9 @TAMAR") == "TAMAR"
    assert _sufijo("TTD26 @TASA FIJA") == "TASA FIJA"
    assert _sufijo("GOB ARS ARG DUAL (TTD26) @BONCAP") == "BONCAP"
    assert _sufijo("TMF27") == ""          # TAMAR puro: no tiene patas que separar


def test_gana_la_grafia_que_TRAE_datos_no_la_primera():
    """El ALIAS. En TTD26 el catálogo guarda `@TASA FIJA` pero la denominación
    dice `@BONCAP`, y con la primera 1816 no devuelve nada (medido 2026-08-16).
    Se piden las dos y gana la que tiene tasa — si no, la pata fija del dual
    quedaría vacía para siempre y el bono seguiría con una sola tasa."""
    pedidos = {"TTD26 @TASA FIJA": ("TTD26", "fija"),
               "TTD26 @BONCAP": ("TTD26", "fija")}
    inst = {"TTD26 @TASA FIJA": {"tea": None},
            "TTD26 @BONCAP": {"tea": 0.29, "spread": None}}
    out = _mejor_por_pata(pedidos, inst)
    assert out[("TTD26", "fija")]["ticker_1816"] == "TTD26 @BONCAP"
    assert out[("TTD26", "fija")]["tea"] == 0.29


def test_una_pata_sin_tea_NO_se_escribe():
    """Pisar la fila anterior con nulls borraría una tasa buena. No escribir deja
    la vieja, y `actualizado_en` delata que quedó atrasada — que es información."""
    pedidos = {"TMF27": ("TMF27", "tamar"), "DHSGO": ("DHSGO", "tamar")}
    inst = {"TMF27": {"tea": 0.3037}, "DHSGO": {"tea": None}}
    out = _mejor_por_pata(pedidos, inst)
    assert list(out) == [("TMF27", "tamar")]


def test_un_ticker_que_1816_ni_devolvio_tampoco_rompe():
    """8 de nuestros 9 corporativos con pata TAMAR no vuelven en la respuesta."""
    assert _mejor_por_pata({"BNCYO": ("BNCYO", "tamar")}, {}) == {}


def test_las_dos_patas_de_un_dual_se_guardan_por_separado():
    """La razón de ser de la PK (ticker, pata): con el ticker solo, la segunda
    pata pisaría a la primera y el dual volvería a tener una sola tasa."""
    pedidos = {"TXMD9 @CER": ("TXMD9", "cer"), "TXMD9 @TAMAR": ("TXMD9", "tamar")}
    inst = {"TXMD9 @CER": {"tea": 0.0682},
            "TXMD9 @TAMAR": {"tea": 0.3862, "spread": 0.0973}}
    out = _mejor_por_pata(pedidos, inst)
    assert out[("TXMD9", "cer")]["tea"] == 0.0682
    assert out[("TXMD9", "tamar")]["spread"] == 0.0973


# ── Qué se escribe en market_snapshot (y qué NO) ─────────────────────────────


def _filas(*pares):
    return [{"ticker": tk, "pata": pata, "tea": tea}
            for tk, pata, tea in pares]


def test_a_market_snapshot_solo_manda_la_pata_PRINCIPAL(monkeypatch):
    """Escribir la pata TAMAR de un dual CER+TAMAR pisaría la tasa que el motor
    calcula EN VIVO para ese bono, y con una de otra pata y de otra rueda.

    El filtro de emisor va en el SQL (`emisor_tipo IS DISTINCT FROM
    'corporativo'`) y lo cubre `test_un_TAMAR_CORPORATIVO_lo_calcula_el_MOTOR`
    del lado de la vista: un TAMAR corporativo va a la rama `on`, o sea que el
    motor sí lo calcula y escribirle encima sería empezar una pelea que se
    resuelve por quién guardó último."""
    import jobs.tamar_1816 as j
    vistos = {}
    monkeypatch.setattr(j, "_q", lambda sql, params=(): [
        {"ticker": "TMF27", "instrumento": "MERV - XMEV - TMF27 - 24hs"}]
        if "TMF27" in str(params) else [])
    monkeypatch.setattr("core.pg_mirror.write_snapshot",
                        lambda t, k, rows: vistos.update(rows=rows) or len(rows))
    # TXMD9 es dual con ajuste='cer': su pata tamar NO tiene que salir del
    # `principales` (y además la query lo filtra por ajuste='tamar').
    n = j._a_market_snapshot(_filas(("TMF27", "tamar", 0.3037),
                                    ("TXMD9", "cer", 0.0682)))
    assert n == 1
    assert vistos["rows"][0]["ticker"] == "MERV - XMEV - TMF27 - 24hs"
    assert vistos["rows"][0]["tea"] == 0.3037


def test_a_market_snapshot_deriva_la_TEM_con_la_formula_de_la_vista(monkeypatch):
    """`TEM = (1+TEA)^(1/12)−1`. Con otra fórmula, el número no coincidiría con
    el que la mesa viene mirando en el resto de la tabla."""
    import jobs.tamar_1816 as j
    vistos = {}
    monkeypatch.setattr(j, "_q", lambda sql, params=(): [
        {"ticker": "TMF27", "instrumento": "X"}])
    monkeypatch.setattr("core.pg_mirror.write_snapshot",
                        lambda t, k, rows: vistos.update(rows=rows) or len(rows))
    j._a_market_snapshot(_filas(("TMF27", "tamar", 0.3037)))
    assert abs(vistos["rows"][0]["tem"] - ((1.3037) ** (1 / 12) - 1)) < 1e-12


def test_a_market_snapshot_no_escribe_nada_si_no_hay_pata_principal(monkeypatch):
    """Un dual cuyo `ajuste` no es tamar no debe generar NI la query."""
    import jobs.tamar_1816 as j

    def _explota(*a, **k):
        raise AssertionError("no debería consultar la base")

    monkeypatch.setattr(j, "_q", _explota)
    assert j._a_market_snapshot(_filas(("TXMD9", "cer", 0.0682))) == 0
    assert j._a_market_snapshot([]) == 0


def test_una_TEA_en_None_no_llega_al_snapshot(monkeypatch):
    import jobs.tamar_1816 as j

    def _explota(*a, **k):
        raise AssertionError("no debería consultar la base")

    monkeypatch.setattr(j, "_q", _explota)
    assert j._a_market_snapshot(_filas(("TMF27", "tamar", None))) == 0

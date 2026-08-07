"""Tests de core.eikon_segmentos — ingresos por segmento del feed Eikon.

Los números de acá son los REALES del discovery del 2026-08-07 (AAPL/NVDA/KO/
RKLB, ver docs/INTEGRACION_REUTERS.md §8). Fijan el criterio que hace que el
desglose cierre contra los ingresos totales: se tiran las filas de TOTAL y se
guardan las de AJUSTE.
"""
from __future__ import annotations

from core.eikon_segmentos import es_total, preparar_filas


def _fila(segmento, ingresos, codigo=None, orden=None, **kw):
    base = {"ticker": "KO", "tipo": "negocio", "periodo": "anual",
            "fecha": "2025-12-31", "segmento": segmento, "codigo": codigo,
            "orden": orden, "ingresos": ingresos}
    base.update(kw)
    return base


def test_es_total_por_codigo_y_por_nombre():
    """El criterio primario es el segmentCode; el nombre es la red por si el
    código viene vacío."""
    assert es_total("Segment Total", "SEGMTL")
    assert es_total("Consolidated Total", "CONSTL")
    assert es_total("Segment Total", None)          # sin código, cae por nombre
    assert es_total("consolidated total", "")
    # Los ajustes NO son totales: sin ellos la suma no cierra.
    assert not es_total("Eliminations", "ICELIM")
    assert not es_total("Corporate", "EXPOTH")
    assert not es_total("Compute & Networking", "518210,541511,541512")


def test_la_suma_de_lo_guardado_cierra_contra_el_consolidado():
    """Caso KO real: 5 segmentos suman 48.806 (Segment Total) pero el
    consolidado es 47.941 — la diferencia son las eliminaciones (−1.009) y
    corporate (+144). Guardando segmentos + ajustes y tirando los dos totales,
    la suma da EXACTAMENTE el consolidado."""
    payload = [
        _fila("Latin America", 6334, "311930"),
        _fila("North America", 19586, "311930"),
        _fila("Asia Pacific", 5638, "311930"),
        _fila("Bottling Investments", 5735, "311930"),
        _fila("Europe, Middle East & Africa", 11513, "311511"),
        _fila("Segment Total", 48806, "SEGMTL"),
        _fila("Eliminations", -1009, "ICELIM"),
        _fila("Corporate", 144, "EXPOTH"),
        _fila("Consolidated Total", 47941, "CONSTL"),
    ]
    filas = preparar_filas(payload)
    nombres = [f["segmento"] for f in filas]
    assert "Segment Total" not in nombres
    assert "Consolidated Total" not in nombres
    assert "Eliminations" in nombres and "Corporate" in nombres
    assert sum(f["ingresos"] for f in filas) == 47941


def test_descarta_basura_y_normaliza():
    """Ticker en mayúscula, tipo/período validados contra la lista cerrada,
    y sin ingresos no se guarda (una fila sin monto no aporta nada)."""
    filas = preparar_filas([
        _fila("Graphics", 22459, ticker="nvda", tipo="NEGOCIO", periodo="Anual"),
        _fila("Sin monto", None),
        _fila("Tipo raro", 100, tipo="inventado"),
        _fila("Período raro", 100, periodo="mensual"),
        _fila("Sin fecha", 100, fecha=None),
        _fila("", 100),
        "no soy un dict",
    ])
    assert len(filas) == 1
    assert filas[0]["ticker"] == "NVDA"
    assert filas[0]["tipo"] == "negocio"
    assert filas[0]["periodo"] == "anual"


def test_dedup_dentro_del_mismo_payload():
    """La PK es (ticker, tipo, periodo, fecha, segmento): dos filas con la misma
    clave en un solo POST harían fallar el upsert entero
    ('cannot affect row a second time'). Gana la última."""
    filas = preparar_filas([
        _fila("Launch Services", 100, ticker="RKLB"),
        _fila("Launch Services", 199, ticker="RKLB"),
        _fila("Space Systems", 402, ticker="RKLB"),
    ])
    assert len(filas) == 2
    por_nombre = {f["segmento"]: f["ingresos"] for f in filas}
    assert por_nombre == {"Launch Services": 199, "Space Systems": 402}


def test_el_mismo_segmento_en_distinto_periodo_no_se_pisa():
    """Mismo nombre en dos fechas son DOS filas (la fecha es parte de la PK)."""
    filas = preparar_filas([
        _fila("North America", 19586, fecha="2025-12-31"),
        _fila("North America", 18649, fecha="2024-12-31"),
    ])
    assert len(filas) == 2

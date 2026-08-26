"""Tests de la FICHA OPERATIVA de un cliente (api/services/perfil_cliente_sql.py).

Lo que cuidan: que VOLUMEN y ARANCEL no se filtren igual. Confundirlos no rompe
nada — devuelve un número plausible y equivocado.
"""
from __future__ import annotations

from datetime import date

from api.services import perfil_cliente_sql as P
from api.services.comercial_sql import _PESIF, _arancel_where, _pesif


# ── Volumen y arancel: DOS reglas distintas ─────────────────────────────────
def test_el_volumen_excluye_los_cierres():
    """La apertura de la caución ya contó el volumen; sumar el cierre lo cuenta
    dos veces y el número sigue pareciendo razonable."""
    assert "es_cierre" in P._VOL_WHERE
    assert "false" in P._VOL_WHERE
    assert "solicitud" in P._VOL_WHERE


def test_el_arancel_INCLUYE_los_cierres():
    """El arancel de una caución vive SOLO en el cierre: filtrarlo lo haría
    desaparecer entero sin que nada avise."""
    assert "es_cierre" not in _arancel_where("o")


def test_las_dos_reglas_no_son_la_misma():
    assert _arancel_where("o") != P._VOL_WHERE


# ── Pesificación: una sola definición ───────────────────────────────────────
def test_el_bruto_se_pesifica_al_mep_DEL_BOLETO():
    """Sumar pesos con dólares da un número que parece plata y no lo es. Y tiene
    que ser el mep del boleto, no el de hoy: si no, el share de un mes viejo se
    mueve con el dólar de esta mañana."""
    e = _pesif("o", "bruto")
    assert "o.moneda = 'ARS'" in e
    assert "o.bruto" in e and "o.mep" in e


def test_la_pesificacion_de_negocio_movimientos_no_cambio():
    """`_PESIF` lo comparten media docena de queries del Tablero Comercial: el
    refactor a función tiene que dar EXACTAMENTE el mismo SQL."""
    assert _PESIF == ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
                      "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")
    assert _pesif() == _PESIF


# ── La ventana de meses ─────────────────────────────────────────────────────
def test_la_ventana_incluye_el_mes_de_hasta():
    ini, meses = P._ventana(3, date(2026, 8, 31))
    assert ini == date(2026, 6, 1)
    assert [m for m, _, _ in meses] == ["2026-06", "2026-07", "2026-08"]
    assert [lab for _, lab, _ in meses] == ["jun-26", "jul-26", "ago-26"]


def test_la_ventana_cruza_el_año():
    ini, meses = P._ventana(3, date(2026, 1, 31))
    assert ini == date(2025, 11, 1)
    assert [lab for _, lab, _ in meses] == ["nov-25", "dic-25", "ene-26"]


def test_todos_los_meses_estan_en_la_lista():
    """La lista de meses se arma en Python y no sale de la query: un mes sin
    operaciones tiene que dibujar una barra en CERO. Un mes ausente y un mes en
    cero no se ven igual en un gráfico."""
    _, meses = P._ventana(12, date(2026, 8, 31))
    assert len(meses) == 12
    assert meses[0][1] == "sep-25" and meses[-1][1] == "ago-26"


def test_una_ventana_absurda_no_dispara_un_scan_de_toda_la_historia():
    # 0 / None son "no me dijeron nada" → default; un número enorme se capea.
    assert len(P._ventana(0, date(2026, 8, 31))[1]) == P.MESES_DEF
    assert len(P._ventana(None, date(2026, 8, 31))[1]) == P.MESES_DEF
    assert len(P._ventana(9999, date(2026, 8, 31))[1]) == P.MESES_MAX
    assert len(P._ventana(-5, date(2026, 8, 31))[1]) == 1
    assert 1 <= P.MESES_DEF <= P.MESES_MAX


def test_cada_mes_trae_su_fin_de_mes():
    _, meses = P._ventana(2, date(2026, 3, 31))
    assert [f for _, _, f in meses] == [date(2026, 2, 28), date(2026, 3, 31)]


# ── Juntar el CIERRE con su APERTURA ────────────────────────────────────────
# El cierre de una caución nunca tiene volumen (la apertura ya lo contó) pero se
# lleva TODO el arancel. Separados, una fila muestra 0% y toda la plata y la otra
# todo el volumen y arancel cero: las dos mienten sobre el mismo negocio.

def test_la_clave_saca_la_palabra_cierre():
    assert P._clave("Caución tomadora cierre") == P._clave("Caución tomadora")
    assert P._clave("CAUCIÓN TOMADORA CIERRE") == P._clave("caucion tomadora")
    assert P._clave("Caución colocadora") != P._clave("Caución tomadora")


def _fila(v, vol, ar, solo_cierre, n=1):
    return {"v": v, "volumen": vol, "arancel": ar, "n_boletos": n, "n_todos": n,
            "_solo_cierre": solo_cierre}


def test_el_cierre_se_junta_con_su_apertura():
    filas, fus = P._fusionar_cierres([
        _fila("Caución tomadora", 5_000_000.0, 0.0, False),
        _fila("Caución tomadora cierre", 0.0, 900.0, True),
    ])
    assert len(filas) == 1
    assert filas[0]["volumen"] == 5_000_000.0 and filas[0]["arancel"] == 900.0
    assert [(f["de"], f["a"]) for f in fus] == [("Caución tomadora cierre", "Caución tomadora")]


def test_un_cierre_SIN_apertura_no_se_inventa_un_destino():
    """«No pude emparejar» no es «lo mando a cualquier lado»: la fila queda sola
    y se ve, que es lo que permite darse cuenta de que falta algo."""
    filas, fus = P._fusionar_cierres([
        _fila("Compras PPT", 2_000_000.0, 800.0, False),
        _fila("Rescate cierre", 0.0, 120.0, True),
    ])
    assert len(filas) == 2 and fus == []
    huerfana = next(f for f in filas if f["v"] == "Rescate cierre")
    assert huerfana["arancel"] == 120.0


def test_una_fila_con_volumen_PROPIO_nunca_se_fusiona():
    """Si tiene volumen propio es un negocio aparte, aunque se llame «cierre»."""
    filas, fus = P._fusionar_cierres([
        _fila("Caución tomadora", 5_000_000.0, 0.0, False),
        _fila("Caución tomadora cierre", 3_000_000.0, 900.0, False),
    ])
    assert len(filas) == 2 and fus == []


def test_el_destino_no_puede_ser_otro_cierre():
    filas, fus = P._fusionar_cierres([
        _fila("Algo cierre", 0.0, 100.0, True),
        _fila("Algo cierres", 0.0, 50.0, True),
    ])
    assert len(filas) == 2 and fus == []


def test_la_fusion_no_pierde_ni_un_peso_de_arancel():
    entrada = [
        _fila("Caución tomadora", 5_000_000.0, 10.0, False),
        _fila("Caución tomadora cierre", 0.0, 900.0, True),
        _fila("Rescate cierre", 0.0, 120.0, True),
        _fila("Compras PPT", 2_000_000.0, 800.0, False),
    ]
    total_antes = sum(f["arancel"] for f in entrada)
    filas, _ = P._fusionar_cierres(entrada)
    assert sum(f["arancel"] for f in filas) == total_antes


# ── Los nombres que ya vienen lindos no se tocan ────────────────────────────
def test_no_se_destroza_un_nombre_ya_legible():
    """En esta base `operacion` ya trae texto para mostrar. `.capitalize()` sobre
    «Ventas PPT» devolvía «Ventas ppt»."""
    from api.services.profundidad_sql import _op_label
    assert _op_label("Ventas PPT") == "Ventas PPT"
    assert _op_label("Caución tomadora") == "Caución tomadora"
    # Un token técnico sí se prettifica.
    assert _op_label("futuro_dlr") == "Futuro dlr"

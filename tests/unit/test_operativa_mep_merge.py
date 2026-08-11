"""Tests del merge de la operativa MEP y del cálculo de efectivos.

Nacen del reporte "en dólar MEP no se completan todos los datos de la columna,
hay algunos vacíos" (2026-08-11). Las columnas BUY / SELL / USD EFECT / MEP
EFECT salían siempre vacías, y las que sí se veían eran justo las que NO
necesitan joinear contra `operaciones.ordenes_live`.

Causa: `_persistir_resultado_operativa` escribe las claves con la dot-notation
del `$set` de Mongo (`"buy.cl_ord_id"`), y el upsert a Postgres las mergeaba
PLANAS — la operativa nunca quedaba enlazada a sus dos patas.
"""
from __future__ import annotations

from api.services.operativa_mep import aplicar_fields, calcular_efectivos

# ─── Merge con dot-notation ──────────────────────────────────────────────────


def test_clave_con_punto_se_escribe_ANIDADA():
    """El bug exacto: `{**doc, **fields}` creaba una clave de primer nivel
    llamada literalmente 'buy.cl_ord_id' y dejaba el id anidado en None."""
    doc = {"buy": {"cl_ord_id": None, "ticker": "MERV - XMEV - AL30 - CI"}}

    out = aplicar_fields(doc, {"buy.cl_ord_id": "ABC123"})

    assert out["buy"]["cl_ord_id"] == "ABC123"
    assert "buy.cl_ord_id" not in out, "la clave plana no debe existir"


def test_el_merge_anidado_NO_pisa_los_otros_campos_del_subdoc():
    """Si el merge reemplazara el subdoc entero, `ticker` se perdería y el
    drilldown quedaría sin saber qué instrumento era cada pata."""
    doc = {"sell": {"cl_ord_id": None, "ticker": "MERV - XMEV - AL30D - CI"}}

    out = aplicar_fields(doc, {"sell.cl_ord_id": "XYZ"})

    assert out["sell"] == {"cl_ord_id": "XYZ", "ticker": "MERV - XMEV - AL30D - CI"}


def test_no_muta_el_doc_original():
    """El doc viene de la DB; mutarlo haría que un fallo posterior deje el
    objeto en memoria distinto de lo persistido."""
    doc = {"buy": {"cl_ord_id": None}}

    aplicar_fields(doc, {"buy.cl_ord_id": "ABC"})

    assert doc["buy"]["cl_ord_id"] is None


def test_las_claves_sin_punto_siguen_siendo_planas():
    out = aplicar_fields({"status": "PENDING"}, {"status": "OK", "buy_error": None})
    assert out["status"] == "OK"
    assert out["buy_error"] is None


# ─── USD / MEP efectivos por sentido de la operativa ─────────────────────────
# Bonos por 100 VN: 1000 VN a 1200 = 1000 × 1200 × 0.01 = $12.000.


def _orden(cum: float, avg: float) -> dict:
    return {"cum_qty": cum, "avg_px": avg}


def test_compra_los_usd_salen_de_la_pata_SELL():
    """compra = ARS → USD: BUY AL30 paga pesos, SELL AL30D cobra dólares."""
    ef = calcular_efectivos("compra", buy_ord=_orden(1000, 120_000), sell_ord=_orden(1000, 10_000))

    assert ef["usd_efectivo"] == 100_000.0        # SELL AL30D
    assert ef["ars_operados"] == 1_200_000.0      # BUY AL30
    assert ef["mep_efectivo"] == 12.0


def test_venta_los_usd_salen_de_la_pata_BUY():
    """venta = USD → ARS: BUY AL30D paga dólares, SELL AL30 cobra pesos.

    Asumir siempre el shape de la compra hacía que USD EFECT mostrara PESOS en
    las filas de venta — y las dos vistas listan las operativas del día sin
    filtrar por tipo, así que el número mal se veía en ambas.
    """
    ef = calcular_efectivos("venta", buy_ord=_orden(1000, 10_000), sell_ord=_orden(1000, 120_000))

    assert ef["usd_efectivo"] == 100_000.0        # BUY AL30D
    assert ef["ars_operados"] == 1_200_000.0      # SELL AL30
    assert ef["mep_efectivo"] == 12.0


def test_los_precios_se_rotulan_por_instrumento_no_por_side():
    """En la venta el AL30 se VENDE y el AL30D se COMPRA: si los precios se
    tomaran por side, el drilldown mostraría el precio del bono equivocado."""
    ef = calcular_efectivos("venta", buy_ord=_orden(1000, 10_000), sell_ord=_orden(1000, 120_000))

    assert ef["precio_al30"] == 120_000           # la pata SELL
    assert ef["precio_al30d"] == 10_000           # la pata BUY


def test_pata_sin_ejecutar_no_inventa_numeros():
    """Una BUY que no llenó nada no puede producir un MEP efectivo."""
    ef = calcular_efectivos("compra", buy_ord=_orden(0, 0), sell_ord=_orden(1000, 10_000))

    assert ef["usd_efectivo"] == 100_000.0
    assert ef["ars_operados"] is None
    assert ef["mep_efectivo"] is None


def test_sin_patas_devuelve_todo_none():
    ef = calcular_efectivos("compra", buy_ord=None, sell_ord=None)
    assert ef["usd_efectivo"] is None and ef["mep_efectivo"] is None

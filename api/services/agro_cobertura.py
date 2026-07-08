"""Service — Pase con Cobertura (AGRO).

Hogar de los cálculos del "Pase con Cobertura": una card por commodity × pase,
valuada SIEMPRE a hoy. Se construye por pasos junto con la mesa. Hoy solo vive
el primer derivado (Descuento a Tasa de Caución 7D → "Monto Pesos Cau 7D"); el
resto de la cadena (interés ON, tipo de cambio ON, compra USD/Tn, compra futuro,
ganancia; y la columna Pagaré/Sintético) se agrega en las siguientes iteraciones.

Fuentes de los inputs (todos manuales de la tab DATOS por ahora):
  - precio disponible por commodity → `camara_cereales.get_camara_cereales` (precio_ars)
  - tasa de caución a 7 días (TNA %)  → `camara_cereales.get_tasas_cobertura` (tasa_caucion_7d)
  - tipo de cambio Matba Rofex        → `camara_cereales.get_dolares_referencia` (dolar_matba)
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

# Universo de commodities del Pase (TRIGO · MAÍZ · SOJA). La Cámara tiene 5
# cereales, pero el Pase con Cobertura se arma solo para estos 3.
PASE_COMMODITIES = ("TRIGO", "MAIZ", "SOJA")

# Convención de caución: la tasa es TNA en %, y el descuento es proporcional a
# 7 días sobre base 365.
_CAUCION_DIAS = 7
_ANIO_BASE = 365

# Gastos de la pata futuro: MATBA + ALyC = (0,175% der. mercado + 0,05% apertura)
# × 2 (ida y vuelta) = 0,45%. Se aplican sobre el "Valor en US$" del Pase Agro.
GASTOS_FUTURO_PCT = 0.0045

_MESES_ES = {
    "01": "ENERO", "02": "FEBRERO", "03": "MARZO", "04": "ABRIL",
    "05": "MAYO", "06": "JUNIO", "07": "JULIO", "08": "AGOSTO",
    "09": "SEPTIEMBRE", "10": "OCTUBRE", "11": "NOVIEMBRE", "12": "DICIEMBRE",
}


def _posicion_label(commodity: str, vto: str | None) -> str:
    """'SOJA SEPTIEMBRE 26' a partir del vencimiento YYYYMMDD / YYYY-MM-DD."""
    s = (vto or "").replace("-", "")
    if len(s) != 8:
        return commodity
    return f"{commodity} {_MESES_ES.get(s[4:6], '?')} {s[2:4]}"


def descuento_caucion_7d(precio_ars: float | None, tasa_caucion_7d: float | None) -> float | None:
    """Precio disponible descontado a la tasa de caución por 7 días.

        descuento = precio_ars × (1 − (tasa_caucion_7d/100) × 7/365)

    `tasa_caucion_7d` viene en % (ej. 21.5). Devuelve None si falta algún input.
    """
    if precio_ars is None or tasa_caucion_7d is None:
        return None
    factor = 1.0 - (tasa_caucion_7d / 100.0) * (_CAUCION_DIAS / _ANIO_BASE)
    return precio_ars * factor


def get_descuento_caucion() -> dict[str, Any]:
    """Tabla "Descuento a Tasa de Caución de 7D" (TRIGO/MAIZ/SOJA).

    Cruza el precio disponible manual (Cámara) con la tasa de caución 7D manual.

    Output:
        {
          "ts": datetime,
          "tasa_caucion_7d": float | None,   # TNA % usada
          "commodities": [
             {"commodity": "TRIGO", "precio_ars": float|None, "descuento_cau_7d": float|None},
             ...
          ]
        }
    """
    from api.services.camara_cereales import get_camara_cereales, get_tasas_cobertura

    tasa = get_tasas_cobertura().get("tasa_caucion_7d")
    by_cereal = {r["cereal"]: r for r in get_camara_cereales()["cereales"]}

    commodities: list[dict[str, Any]] = []
    for c in PASE_COMMODITIES:
        precio = (by_cereal.get(c) or {}).get("precio_ars")
        commodities.append({
            "commodity":        c,
            "precio_ars":       precio,
            "descuento_cau_7d": descuento_caucion_7d(precio, tasa),
        })

    return {
        "ts":              datetime.now(UTC),
        "tasa_caucion_7d": tasa,
        "commodities":     commodities,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CARD "PASE CON COBERTURA" — Obligación Negociable (ON)
# ─────────────────────────────────────────────────────────────────────────────
# Una card por commodity × pase (fila futuro del Pase Agro con precio). Valuada
# SIEMPRE a hoy. Cadena de cálculo (verificada contra la planilla de la mesa):
#
#   dias        = Fecha Pase (vto) − Hoy
#   interes     = TC − TC / (1 + TasaON/100 × dias/365)
#   TC_ON       = TC − interes
#   compra_usd  = Venta Dispo (ARS) / TC_ON
#   total_gast  = Valor US$ Pase Agro × 0,45%
#   compra_fut  = Valor US$ Pase Agro + total_gast
#   ganancia_on = compra_usd − compra_fut        ← va a la tabla resumen (col ON)
#
# TC = dólar Matba Rofex (manual), Venta Dispo = Cámara precio_ars (manual),
# Valor US$ Pase Agro = precio del futuro (fila `us` del Pase Agro).
# La columna Pagaré / Sintético se agrega en las próximas iteraciones.


def pase_on_card(
    *,
    posicion: str,
    ticker: str | None,
    vto: str | None,
    dias: int | None,
    pase_lleno: float | None,
    valor_pase_agro_usd: float | None,
    tc: float | None,
    venta_dispo_ars: float | None,
    monto_pesos_cau_7d: float | None,
    tasa_on_pct: float | None,
) -> dict[str, Any]:
    """Card 'Obligación Negociable' de un pase. Cada campo es None si le falta
    algún input (los inputs manuales pueden no estar cargados todavía).

    `monto_pesos_cau_7d` (descuento a caución) es display-only: se muestra en la
    card pero la compra_usd usa la Venta Dispo directa (así lo da la planilla)."""
    interes = tc_on = compra_usd = total_gastos = compra_futuro = ganancia = None

    if tc is not None and tasa_on_pct is not None and dias is not None:
        interes = tc - tc / (1 + (tasa_on_pct / 100.0) * (dias / _ANIO_BASE))
        tc_on = tc - interes
    if venta_dispo_ars is not None and tc_on:
        compra_usd = venta_dispo_ars / tc_on
    if valor_pase_agro_usd is not None:
        total_gastos = valor_pase_agro_usd * GASTOS_FUTURO_PCT
        compra_futuro = valor_pase_agro_usd + total_gastos
    if compra_usd is not None and compra_futuro is not None:
        ganancia = compra_usd - compra_futuro

    return {
        "posicion":             posicion,
        "ticker":               ticker,
        "vto":                  vto,
        "dias":                 dias,
        "pase_lleno":           pase_lleno,
        "tc":                   tc,
        "venta_dispo_ars":      venta_dispo_ars,
        "monto_pesos_cau_7d":   monto_pesos_cau_7d,
        "tasa_on":              tasa_on_pct,
        "interes":              interes,
        "tc_on":                tc_on,
        "compra_usd":           compra_usd,
        "valor_pase_agro_usd":  valor_pase_agro_usd,
        "gastos_pct":           GASTOS_FUTURO_PCT,
        "total_gastos":         total_gastos,
        "compra_futuro":        compra_futuro,
        "ganancia_on_usd":      ganancia,
    }


def get_pase_cobertura(bloques: list[dict[str, Any]]) -> dict[str, Any]:
    """Cards del Pase con Cobertura (ON) por commodity, a partir de los bloques
    del Pase Agro ya construidos (para no recalcular futuros/pizarra).

    Output:
        {
          "hoy": "YYYY-MM-DD", "tc_matba": float|None, "tasa_on": float|None,
          "commodities": [
            {"commodity": "SOJA", "venta_dispo_ars": float|None,
             "cards": [ <pase_on_card>, ... ]},
            ...
          ]
        }
    """
    from api.services import derivados_agro as _agro
    from api.services.camara_cereales import (
        get_camara_cereales,
        get_dolares_referencia,
        get_tasas_cobertura,
    )

    tasas = get_tasas_cobertura()
    tasa_on = tasas.get("tasa_on")
    tasa_caucion = tasas.get("tasa_caucion_7d")
    tc = get_dolares_referencia().get("dolar_matba")
    precios = {r["cereal"]: r.get("precio_ars") for r in get_camara_cereales()["cereales"]}
    hoy = date.today()

    commodities: list[dict[str, Any]] = []
    for b in bloques:
        commodity = b.get("commodity")
        vd = precios.get(commodity)
        monto_cau = descuento_caucion_7d(vd, tasa_caucion)  # display-only en la card
        cards: list[dict[str, Any]] = []
        for r in b.get("rows", []):
            if r.get("tipo") != "futuro" or r.get("us") is None:
                continue
            vto = r.get("vencimiento")
            # dias a hoy (valuado hoy siempre); fallback al dias_a_vto del snapshot.
            dias = _agro._dias_entre(vto, hoy) if vto else r.get("dias_a_vto")
            cards.append(pase_on_card(
                posicion=_posicion_label(commodity, vto),
                ticker=r.get("ticker"),
                vto=vto,
                dias=dias,
                pase_lleno=r.get("pase"),
                valor_pase_agro_usd=r.get("us"),
                tc=tc,
                venta_dispo_ars=vd,
                monto_pesos_cau_7d=monto_cau,
                tasa_on_pct=tasa_on,
            ))
        commodities.append({
            "commodity":       commodity,
            "venta_dispo_ars": vd,
            "cards":           cards,
        })

    return {
        "hoy":         hoy.isoformat(),
        "tc_matba":    tc,
        "tasa_on":     tasa_on,
        "commodities": commodities,
    }

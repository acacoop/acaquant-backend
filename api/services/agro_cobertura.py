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
# CARD "PASE CON COBERTURA" — Obligación Negociable (ON) + Pagaré
# ─────────────────────────────────────────────────────────────────────────────
# Una card por commodity × pase (fila futuro del Pase Agro con precio). Valuada
# SIEMPRE a hoy. Cadena de cálculo (verificada contra la planilla de la mesa):
#
# Común a ambas columnas:
#   dias        = Fecha Pase (vto) − Hoy
#   total_gast  = Valor US$ Pase Agro × 0,45%
#   compra_fut  = Valor US$ Pase Agro + total_gast
#
# Obligación Negociable (ON) — usa dólar Matba (TC) + Tasa ON:
#   interes     = TC − TC / (1 + TasaON/100 × dias/365)
#   TC_ON       = TC − interes
#   compra_usd  = Venta Dispo (ARS) / TC_ON
#   ganancia_on = compra_usd − compra_fut        ← tabla resumen, col ON
#
# Pagaré — usa BNA Comprador T−1 (ayer) + Tasa Pagaré:
#   interes_desc = BNA_T1 − BNA_T1 / (1 + TasaPagare/100 × dias/365)
#   TC_pagare    = BNA_T1 − interes_desc
#   compra_usd_p = Venta Dispo (ARS) / TC_pagare
#   ganancia_pag = compra_usd_p − compra_fut      ← tabla resumen, col Pagaré
#
# Venta Dispo = Cámara precio_ars (manual); Valor US$ Pase Agro = precio del
# futuro (fila `us`). La columna Sintético se agrega en la próxima iteración.


def _interes_descontado(base_tc: float | None, tasa_pct: float | None,
                        dias: int | None) -> float | None:
    """Interés implícito de descontar `base_tc` a `tasa_pct` (TNA %) por `dias`.
    Estructura compartida por ON (dólar Matba) y Pagaré (BNA T−1)."""
    if base_tc is None or tasa_pct is None or dias is None:
        return None
    return base_tc - base_tc / (1 + (tasa_pct / 100.0) * (dias / _ANIO_BASE))


def pase_card(
    *,
    posicion: str,
    ticker: str | None,
    vto: str | None,
    dias: int | None,
    pase_lleno: float | None,
    valor_pase_agro_usd: float | None,
    tc: float | None,
    bna_comprador_t1: float | None,
    venta_dispo_ars: float | None,
    monto_pesos_cau_7d: float | None,
    tasa_on_pct: float | None,
    tasa_pagare_pct: float | None,
) -> dict[str, Any]:
    """Card de un pase con las columnas ON y Pagaré. Cada campo es None si le
    falta algún input (los inputs manuales pueden no estar cargados todavía).

    `monto_pesos_cau_7d` (descuento a caución) es display-only: se muestra en la
    card pero la compra_usd usa la Venta Dispo directa (así lo da la planilla)."""
    # Común: gastos + compra futuro (misma para ON y Pagaré).
    total_gastos = compra_futuro = None
    if valor_pase_agro_usd is not None:
        total_gastos = valor_pase_agro_usd * GASTOS_FUTURO_PCT
        compra_futuro = valor_pase_agro_usd + total_gastos

    # Columna ON (dólar Matba + Tasa ON).
    interes = _interes_descontado(tc, tasa_on_pct, dias)
    tc_on = (tc - interes) if (tc is not None and interes is not None) else None
    compra_usd = (venta_dispo_ars / tc_on) if (venta_dispo_ars is not None and tc_on) else None
    ganancia_on = (compra_usd - compra_futuro) if (
        compra_usd is not None and compra_futuro is not None) else None

    # Columna Pagaré (BNA Comprador T−1 + Tasa Pagaré).
    interes_desc = _interes_descontado(bna_comprador_t1, tasa_pagare_pct, dias)
    tc_pagare = (bna_comprador_t1 - interes_desc) if (
        bna_comprador_t1 is not None and interes_desc is not None) else None
    compra_usd_pagare = (venta_dispo_ars / tc_pagare) if (
        venta_dispo_ars is not None and tc_pagare) else None
    ganancia_pagare = (compra_usd_pagare - compra_futuro) if (
        compra_usd_pagare is not None and compra_futuro is not None) else None

    return {
        "posicion":             posicion,
        "ticker":               ticker,
        "vto":                  vto,
        "dias":                 dias,
        "pase_lleno":           pase_lleno,
        "tc":                   tc,
        "venta_dispo_ars":      venta_dispo_ars,
        "monto_pesos_cau_7d":   monto_pesos_cau_7d,
        "valor_pase_agro_usd":  valor_pase_agro_usd,
        "gastos_pct":           GASTOS_FUTURO_PCT,
        "total_gastos":         total_gastos,
        "compra_futuro":        compra_futuro,
        # ON
        "tasa_on":              tasa_on_pct,
        "interes":              interes,
        "tc_on":                tc_on,
        "compra_usd":           compra_usd,
        "ganancia_on_usd":      ganancia_on,
        # Pagaré
        "bna_comprador_t1":     bna_comprador_t1,
        "tasa_pagare":          tasa_pagare_pct,
        "interes_descontado":   interes_desc,
        "tc_pagare":            tc_pagare,
        "compra_usd_pagare":    compra_usd_pagare,
        "ganancia_pagare_usd":  ganancia_pagare,
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
    tasa_pagare = tasas.get("tasa_pagare")
    tasa_caucion = tasas.get("tasa_caucion_7d")
    dolares = get_dolares_referencia()
    tc = dolares.get("dolar_matba")
    bna_t1 = dolares.get("bna_comprador_t1")
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
            cards.append(pase_card(
                posicion=_posicion_label(commodity, vto),
                ticker=r.get("ticker"),
                vto=vto,
                dias=dias,
                pase_lleno=r.get("pase"),
                valor_pase_agro_usd=r.get("us"),
                tc=tc,
                bna_comprador_t1=bna_t1,
                venta_dispo_ars=vd,
                monto_pesos_cau_7d=monto_cau,
                tasa_on_pct=tasa_on,
                tasa_pagare_pct=tasa_pagare,
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

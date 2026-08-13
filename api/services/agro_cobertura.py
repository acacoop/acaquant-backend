"""Service — Pase con Cobertura (AGRO).

Hogar de los cálculos del "Pase con Cobertura": una card por commodity × pase,
valuada SIEMPRE a hoy. Tres columnas de resultado (US$/Tn): ON, Pagaré y
Sintético. ON y Pagaré comparten la fórmula de descuento con tasa manual (tab
DATOS); la columna Sintético usa la misma fórmula que ON (mismo dólar Matba)
pero la tasa NO es manual: sale de la TNA del sintético del mismo mes que el
pase (Mercados → Sintéticos, tabla LONG ROFEX + LONG LECAP).

Fuentes de los inputs:
  - precio disponible por commodity → `camara_cereales.get_camara_cereales` (precio_ars)
  - tasa de caución a 7 días (TNA %)  → `camara_cereales.get_tasas_cobertura` (tasa_caucion_7d)
  - tasas ON / Pagaré (TNA %, manual) → `camara_cereales.get_tasas_cobertura`
  - tipo de cambio Matba Rofex        → `camara_cereales.get_dolares_referencia` (dolar_matba)
  - tasa Sintético (TNA %, por mes)   → `sinteticos.get_sinteticos` (long_rofex_long_lecap.tna)
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

# Gastos de la pata futuro ("Costo Pase"): MATBA + ALyC. Desglose por pata:
# 0,175% derechos de mercado + 0,05% derecho de apertura/registro = 0,225%;
# × 2 patas (ida y vuelta) = 0,45%. Se aplican sobre el "Valor en US$" del
# Pase Agro (el precio del futuro) → a precios actuales da ≈ US$ 1 por Tn.
GASTOS_DER_MERCADO_PCT = 0.00175
GASTOS_APERTURA_PCT = 0.0005
GASTOS_FUTURO_PCT = (GASTOS_DER_MERCADO_PCT + GASTOS_APERTURA_PCT) * 2  # 0,45%

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


def _ym(s: str | None) -> tuple[int, int] | None:
    """(año, mes) de un vencimiento YYYYMMDD o YYYY-MM-DD. None si no parsea.
    Se usa para matchear el pase (mes del futuro Matba) con el sintético del
    mismo mes en la tabla LONG ROFEX + LONG LECAP."""
    s = (s or "").replace("-", "")
    if len(s) < 6:
        return None
    try:
        return int(s[:4]), int(s[4:6])
    except ValueError:
        return None


def _sinteticos_por_ym() -> dict[tuple[int, int], dict[str, Any]]:
    """(año, mes) → fila del sintético LONG ROFEX + LONG LECAP de ese mes.

    Fuente de la tasa de la columna Sintético del Pase con Cobertura: en vez de
    una tasa manual (como ON/Pagaré), se usa la TNA del sintético del MISMO mes
    que el pase (Mercados → Sintéticos). Si hay más de uno en el mes, gana el de
    vencimiento más temprano (la lista viene ordenada por vto)."""
    from api.services.sinteticos import get_sinteticos

    out: dict[tuple[int, int], dict[str, Any]] = {}
    for r in get_sinteticos().get("long_rofex_long_lecap", []):
        ym = _ym(r.get("vto_fecha"))
        if ym:
            out.setdefault(ym, r)
    return out


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
    tasa_sintetico_pct: float | None = None,
    sintetico_ticker: str | None = None,
) -> dict[str, Any]:
    """Card de un pase con las columnas ON, Pagaré y Sintético. Cada campo es None
    si le falta algún input (los inputs manuales pueden no estar cargados todavía).

    `monto_pesos_cau_7d` (descuento a caución) es display-only: se muestra en la
    card pero la compra_usd usa la Venta Dispo directa (así lo da la planilla).

    La columna Sintético usa la MISMA fórmula que ON (mismo TC dólar Matba), pero
    la tasa NO es manual: sale del sintético del mismo mes (`tasa_sintetico_pct`)."""
    # Común: costo pase (gastos MATBA+ALyC) + compra futuro (misma para ON y Pagaré).
    total_gastos = compra_futuro = None
    if valor_pase_agro_usd is not None:
        total_gastos = valor_pase_agro_usd * GASTOS_FUTURO_PCT
        compra_futuro = valor_pase_agro_usd + total_gastos

    # Pase Lleno NETO del costo pase (pedido de la mesa 2026-07-14): el pase
    # bruto (pizarra − futuro) le restaba ~US$ 1/Tn de gastos que el operador
    # paga sí o sí. `pase_bruto` queda para el explicador paso a paso.
    pase_bruto = pase_lleno
    if pase_bruto is not None and total_gastos is not None:
        pase_lleno = pase_bruto - total_gastos

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

    # Columna Sintético (dólar Matba + TNA del sintético del mismo mes).
    interes_sint = _interes_descontado(tc, tasa_sintetico_pct, dias)
    tc_sint = (tc - interes_sint) if (tc is not None and interes_sint is not None) else None
    compra_usd_sint = (venta_dispo_ars / tc_sint) if (
        venta_dispo_ars is not None and tc_sint) else None
    ganancia_sint = (compra_usd_sint - compra_futuro) if (
        compra_usd_sint is not None and compra_futuro is not None) else None

    return {
        "posicion":             posicion,
        "ticker":               ticker,
        "vto":                  vto,
        "dias":                 dias,
        "pase_bruto":           pase_bruto,
        "pase_lleno":           pase_lleno,   # neto del costo pase (bruto − gastos)
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
        # Sintético (tasa desde Mercados → Sintéticos, mismo mes)
        "sintetico_ticker":     sintetico_ticker,
        "tasa_sintetico":       tasa_sintetico_pct,
        "interes_sintetico":    interes_sint,
        "tc_sintetico":         tc_sint,
        "compra_usd_sintetico": compra_usd_sint,
        "ganancia_sintetico_usd": ganancia_sint,
    }


def get_costo_pase() -> dict[str, Any]:
    """Panel "COSTO PASE" de la tab DATOS — 100% automático, nada editable.

    Muestra CÓMO se genera el costo de la pata futuro (gastos MATBA + ALyC):
    el desglose porcentual (constantes de mercado) y, como referencia viva, el
    costo en US$/Tn por commodity aplicando el 0,45% sobre el precio dispo US$
    de la Cámara (≈ US$ 1/Tn a precios actuales). En las cards el costo exacto
    se calcula sobre el US$ de CADA futuro — esto es la referencia del panel.
    """
    from api.services.camara_cereales import get_camara_cereales

    por_cereal = {r["cereal"]: r.get("precio_usd") for r in get_camara_cereales()["cereales"]}
    commodities = []
    for c in PASE_COMMODITIES:
        us_ref = por_cereal.get(c)
        commodities.append({
            "commodity": c,
            "us_ref":    us_ref,
            "costo_usd": round(us_ref * GASTOS_FUTURO_PCT, 2) if us_ref else None,
        })
    return {
        "der_mercado_pct": GASTOS_DER_MERCADO_PCT * 100,   # 0.175
        "apertura_pct":    GASTOS_APERTURA_PCT * 100,      # 0.05
        "por_pata_pct":    (GASTOS_DER_MERCADO_PCT + GASTOS_APERTURA_PCT) * 100,  # 0.225
        "total_pct":       GASTOS_FUTURO_PCT * 100,        # 0.45
        "commodities":     commodities,
    }


def get_pase_cobertura(
    bloques: list[dict[str, Any]], *, plaza: str = "rosario",
    tasas: dict[str, Any] | None = None,
    dolares: dict[str, Any] | None = None,
    cam: dict[str, Any] | None = None,
    sinteticos: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cards del Pase con Cobertura (ON) por commodity, a partir de los bloques
    del Pase Agro ya construidos (para no recalcular futuros/pizarra).

    `plaza`: qué disponible (pizarra) se usa para la valuación.
      - "rosario" (default): Cámara Arbitral Rosario. El Pase Lleno sale del
        `pase` ya calculado en el bloque (pizarra Rosario − futuro).
      - "bahia": Cámara Bahía Blanca. El Pase Lleno se RECALCULA con la pizarra
        de Bahía (precio_usd Bahía − futuro). Todo lo demás (tasas, dólar Matba,
        BNA T−1, sintéticos, futuros) es idéntico — solo cambia el disponible.

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
        get_camara_cereales_bahia,
        get_dolares_referencia,
        get_tasas_cobertura,
    )

    # Los 4 insumos se LEEN solo si no vinieron inyectados. Quien llama dos
    # veces seguidas (una por plaza) ya los tiene: tasas, dólares y sintéticos
    # son IDÉNTICOS para Rosario y Bahía — solo cambia la cámara. Sin esto,
    # /api/derivados/agro releía lo mismo hasta 3 veces por request y cada
    # lectura es un viaje de ~8.5ms (cProfile 2026-08-13: 66 viajes en total).
    # Con `None` el comportamiento es EXACTAMENTE el de antes.
    tasas = get_tasas_cobertura() if tasas is None else tasas
    tasa_on = tasas.get("tasa_on")
    tasa_pagare = tasas.get("tasa_pagare")
    tasa_caucion = tasas.get("tasa_caucion_7d")
    dolares = get_dolares_referencia() if dolares is None else dolares
    tc = dolares.get("dolar_matba")
    bna_t1 = dolares.get("bna_comprador_t1")
    es_bahia = plaza == "bahia"
    if cam is None:
        cam = get_camara_cereales_bahia() if es_bahia else get_camara_cereales()
    # Disponible en ARS (venta) y en USD (pizarra) por cereal para la plaza.
    precios = {r["cereal"]: r.get("precio_ars") for r in cam["cereales"]}
    precios_usd = {r["cereal"]: r.get("precio_usd") for r in cam["cereales"]}
    hoy = date.today()
    # Sintéticos LONG ROFEX + LONG LECAP por mes: la columna Sintético toma la
    # TNA del sintético del MISMO mes que el pase (no es una tasa manual).
    sinteticos = _sinteticos_por_ym() if sinteticos is None else sinteticos

    commodities: list[dict[str, Any]] = []
    for b in bloques:
        commodity = b.get("commodity")
        vd = precios.get(commodity)
        piz_us = precios_usd.get(commodity)  # pizarra USD de la plaza (para Bahía)
        monto_cau = descuento_caucion_7d(vd, tasa_caucion)  # display-only en la card
        cards: list[dict[str, Any]] = []
        for r in b.get("rows", []):
            if r.get("tipo") != "futuro" or r.get("us") is None:
                continue
            vto = r.get("vencimiento")
            # dias a hoy (valuado hoy siempre); fallback al dias_a_vto del snapshot.
            dias = _agro._dias_entre(vto, hoy) if vto else r.get("dias_a_vto")
            # Pase bruto (pizarra − futuro): en Bahía se recalcula con la pizarra
            # de Bahía; en Rosario se usa el `pase` que ya trae el bloque.
            futuro_us = r.get("us")
            if es_bahia:
                pase_bruto = round(piz_us - futuro_us, 4) if (
                    piz_us and futuro_us is not None) else None
            else:
                pase_bruto = r.get("pase")
            # Match del sintético por mes del futuro. Sin sintético en ese mes,
            # la columna Sintético queda vacía (no se inventa una tasa).
            sint = sinteticos.get(_ym(vto)) if vto else None
            tna_sint = sint.get("tna") if sint else None
            cards.append(pase_card(
                posicion=_posicion_label(commodity, vto),
                ticker=r.get("ticker"),
                vto=vto,
                dias=dias,
                pase_lleno=pase_bruto,
                valor_pase_agro_usd=futuro_us,
                tc=tc,
                bna_comprador_t1=bna_t1,
                venta_dispo_ars=vd,
                monto_pesos_cau_7d=monto_cau,
                tasa_on_pct=tasa_on,
                tasa_pagare_pct=tasa_pagare,
                tasa_sintetico_pct=(tna_sint * 100.0) if tna_sint is not None else None,
                sintetico_ticker=(sint.get("ticker") if sint else None),
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

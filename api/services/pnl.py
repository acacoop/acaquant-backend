"""Motor de PnL por (cuenta, ticker) con cost-basis weighted-average.

Para cada ticker se mantienen DOS canales de PnL:

  pnl_realizado   = Σ (precio_venta − precio_promedio) × qty_vendida
                    para cada venta histórica (compras/ventas se cancelan
                    en orden cronológico; la ganancia "queda" cerrada).
  pnl_no_realizado = qty_actual_calc × precio_actual − costo_remanente
                     (= valor de mercado del stock vivo − lo que pagaste
                     por esas qty específicas).

Más:
  pnl_pasivo  = Σ importes de `categoria=acreencia` (cupones, dividendos,
                amortizaciones). NO afectan cantidad, solo aportan cash
                cobrado independiente.

  pnl_total   = pnl_realizado + pnl_no_realizado + pnl_pasivo

Cost-basis weighted-average:
  Compra (precio P, cantidad Q):
    costo_remanente += P × Q
    qty_actual      += Q
  Venta (precio P, cantidad Q):
    avg_cost          = costo_remanente / qty_actual
    pnl_realizado    += (precio_efectivo − avg_cost) × Q
    costo_remanente  −= avg_cost × Q   ← descuenta solo la porción "viva"
    qty_actual       −= Q

Pesificación: cada importe USD/USDC se convierte al MEP de su fecha. Si
falta MEP → fallback en moneda original (flag fechas_sin_mep).

Limitación conocida (opción A): si la cuenta tenía posiciones ANTES del
primer boleto disponible, qty_actual_calc < qty del AuM. En ese caso el
costo_remanente está incompleto y pnl_no_realizado queda subestimado
(la porción pre-data no aporta ganancia "papel"). Flag completeness =
"parcial". Para tickers sin ningún boleto: "sin_boletos".

`importe` viene neto de comisiones del lado de Aunesa, así que la suma
con signo nativo cubre comisiones automáticamente.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from api.cache import cached
from api.db import get_db_cashflow, get_db_valuaciones
from api.services._mep import get_mep_for_date

_CATS_PAGO         = {"compra", "suscripcion_fci"}
_CATS_COBRO_VENTA  = {"venta", "rescate_fci"}
_CATS_COBRO_PASIVO = {"acreencia"}
_CATS_RELEVANTES   = _CATS_PAGO | _CATS_COBRO_VENTA | _CATS_COBRO_PASIVO

_OPS_PASIVOS = ("Cash dividend", "Interest payment", "Partial redemption")

# Fallback regex sólo si `Valuaciones.Assets.TICKER` no está set (gap de
# metadata). Path principal: leer el TICKER del doc de Assets directo,
# que es la tabla maestra del mapping unidad ↔ ticker corto.
_RE_TICKER_FALLBACK = re.compile(r"^\[\d+\]\s*(.+?)(?=\s+-\s+|$)")


def _ticker_corto_fallback(unidad: str) -> str:
    """Solo si Valuaciones.Assets no tiene TICKER para esta unidad."""
    m = _RE_TICKER_FALLBACK.match(unidad or "")
    return m.group(1).strip() if m else (unidad or "")


def _build_unidad_to_ticker_map(db_v) -> dict[str, str]:
    """Lee Valuaciones.Assets (UPPERCASE, fuente de verdad) y devuelve
    {unidad: ticker_match}. Precedencia:

      1. `CAFCI` — para FCI (campo derivado de la unidad por
         `_sincronizar_assets`). Match con `boleto.ticker` que viene del
         parser como código CAFCI (ej `CAFCI3580-1199`).
      2. `TICKER` — para acciones / bonos / ONs / etc, donde el TICKER
         humano (ej `AL30`) coincide con `boleto.ticker`.
      3. Fallback regex sobre la unidad si los dos anteriores son vacíos.

    Cacheado dentro del lifetime del request — Assets cambia poco.
    """
    out: dict[str, str] = {}
    placeholders = {"", "NO APLICA"}
    for d in db_v["Assets"].find(
        {}, {"_id": 0, "unidad": 1, "TICKER": 1, "CAFCI": 1}
    ):
        unidad = d.get("unidad")
        if not unidad:
            continue
        cafci = (d.get("CAFCI") or "").strip()
        if cafci and cafci not in placeholders:
            out[unidad] = cafci
            continue
        ticker = (d.get("TICKER") or "").strip()
        if ticker and ticker not in placeholders:
            out[unidad] = ticker
        else:
            out[unidad] = _ticker_corto_fallback(unidad)
    return out


def _new_state() -> dict:
    return {
        "qty_actual":       0.0,    # cantidad neta — running
        "costo_remanente":  0.0,    # cost basis del stock vivo (en ARS)
        "importe_invertido": 0.0,   # Σ |importe| de TODAS las compras
                                    # (incluye posiciones ya cerradas)
        "pnl_realizado":    0.0,    # ganancias/pérdidas de ventas pasadas
        "pnl_pasivo":       0.0,    # cupones + divs + amorts
        "breakdown_pasivo": {op: 0.0 for op in _OPS_PASIVOS},
        "breakdown_otros":  0.0,    # acreencia con op desconocido
        "qty_compras":      0.0,    # bruto, para detectar pre-data
        "qty_ventas":       0.0,
        "monedas":          set(),
        "fechas_sin_mep":   set(),
        "n_movimientos":    0,
        "boletos":          [],     # detalle audit per ticker
    }


@cached(ttl=300)
def pnl_por_cuenta(id_cuenta: str) -> dict:
    """PnL por ticker para una cuenta — ver docstring del módulo."""
    db_cf = get_db_cashflow()
    db_v = get_db_valuaciones()

    # ── 1. Boletos en orden cronológico ─────────────────────────────────
    # Crítico: el cost-basis depende del orden de procesamiento.
    # El campo `mep` viene en cada doc desde el job (snapshot inmutable
    # del día del boleto). Solo caemos a `get_mep_for_date` si no está
    # (boletos pre-fix sin reingestar, fechas anteriores al feed).
    boletos = list(db_cf["NegocioMovimientos"].find(
        {
            "cuenta":    {"$regex": f"^\\[{id_cuenta}\\]"},
            "categoria": {"$in": list(_CATS_RELEVANTES)},
            "ticker":    {"$ne": None},
        },
        {"_id": 0, "fecha": 1, "categoria": 1, "op": 1,
         "ticker": 1, "cantidad": 1, "precio": 1, "importe": 1,
         "moneda": 1, "comprobante": 1, "mep": 1},
    ).sort([("fecha", 1), ("comprobante", 1)]))

    # ── 2. Pesificación helper ──────────────────────────────────────────
    # Cache solo para fallback (fechas que no tenían mep en el doc).
    mep_fallback_cache: dict[str, float | None] = {}

    def _pesificar(b: dict) -> tuple[float, bool]:
        """Devuelve (importe_ars, mep_missing). Lee `mep` directo del doc;
        si no está, fallback a Valuaciones.Dolar."""
        try:
            importe = float(b.get("importe") or 0)
        except (TypeError, ValueError):
            return 0.0, False
        moneda = b.get("moneda") or "ARS"
        if moneda == "ARS":
            return importe, False

        mep = b.get("mep")
        if mep is None:
            fecha = b.get("fecha") or ""
            if fecha not in mep_fallback_cache:
                mep_fallback_cache[fecha] = get_mep_for_date(fecha)
            mep = mep_fallback_cache[fecha]
        if mep is None or mep <= 0:
            return importe, True  # fallback: queda en moneda original
        return importe * mep, False

    # ── 3. Procesar boletos en orden, mantener cost-basis running ───────
    state: dict[str, dict] = defaultdict(_new_state)

    for b in boletos:
        ticker = (b.get("ticker") or "").strip()
        if not ticker:
            continue
        try:
            importe = float(b.get("importe") or 0)
            cantidad = abs(float(b.get("cantidad") or 0))
        except (TypeError, ValueError):
            continue
        if importe == 0 and cantidad == 0:
            continue

        moneda = b.get("moneda") or "ARS"
        fecha  = b.get("fecha") or ""
        cat    = b.get("categoria")
        op     = b.get("op") or ""
        importe_ars, mep_missing = _pesificar(b)

        st = state[ticker]
        st["monedas"].add(moneda)
        st["n_movimientos"] += 1
        if mep_missing:
            st["fechas_sin_mep"].add(fecha)

        # Detalle audit — guardamos cada boleto procesado para que el
        # frontend pueda mostrarlos y vos puedas reconciliar contra los
        # KPIs y totales del ticker.
        try:
            cant_signed = float(b.get("cantidad") or 0)
        except (TypeError, ValueError):
            cant_signed = 0.0
        try:
            precio_b = float(b.get("precio") or 0)
        except (TypeError, ValueError):
            precio_b = 0.0
        st["boletos"].append({
            "fecha":       fecha,
            "categoria":   cat,
            "op":          op,
            "cantidad":    cant_signed,
            "precio":      precio_b,
            "importe":     importe,
            "importe_ars": importe_ars,
            "moneda":      moneda,
            "mep":         b.get("mep"),
        })

        if cat in _CATS_PAGO:
            # Compra: importe negativo → uso |importe| como costo invertido
            costo_total = abs(importe_ars)
            st["costo_remanente"] += costo_total
            st["qty_actual"]      += cantidad
            st["qty_compras"]     += cantidad

        elif cat in _CATS_COBRO_VENTA:
            ingreso_total = importe_ars   # positivo
            qty_a_vender  = min(cantidad, st["qty_actual"]) if st["qty_actual"] > 0 else 0
            if qty_a_vender > 0 and st["qty_actual"] > 0:
                avg_cost = st["costo_remanente"] / st["qty_actual"]
                # Si la venta excede el stock conocido (puede pasar con
                # boletos pre-data), proporcionalizamos el ingreso
                # para no inflar el realizado.
                ingreso_proporcional = (
                    ingreso_total * (qty_a_vender / cantidad)
                    if cantidad > 0 else 0
                )
                st["pnl_realizado"]   += ingreso_proporcional - (avg_cost * qty_a_vender)
                st["costo_remanente"] -= avg_cost * qty_a_vender
                st["qty_actual"]      -= qty_a_vender
            # Si qty_a_vender == 0 (no había stock conocido), la venta
            # queda como "fantasma" — no genera realizado en opción A.
            # En opción B sumaríamos el ingreso directo. Acá conservador.
            st["qty_ventas"] += cantidad

        elif cat in _CATS_COBRO_PASIVO:
            # Acreencia: cupón / dividendo / amortización. Cobro suelto
            # que NO afecta cantidad ni cost basis.
            st["pnl_pasivo"] += importe_ars
            if op in st["breakdown_pasivo"]:
                st["breakdown_pasivo"][op] += importe_ars
            else:
                st["breakdown_otros"] += importe_ars

    # ── 4. Posición actual del AuM (último snapshot) ───────────────────
    # Map unidad → TICKER desde Valuaciones.Assets (fuente de verdad del
    # mapeo). Si una unidad no tiene TICKER en Assets, cae al regex fallback.
    unidad_to_ticker = _build_unidad_to_ticker_map(db_v)

    last = db_v["AuM"].find_one(
        {"id_cuenta": id_cuenta},
        {"_id": 0, "fecha_snapshot": 1},
        sort=[("fecha_snapshot", -1)],
    )
    fecha_actual = last["fecha_snapshot"] if last else None
    aum_por_ticker: dict[str, dict] = {}
    if fecha_actual:
        for d in db_v["AuM"].find(
            {"id_cuenta": id_cuenta, "fecha_snapshot": fecha_actual},
            {"_id": 0, "unidad": 1, "cantidad": 1, "precio": 1, "valuacion": 1},
        ):
            unidad = d.get("unidad", "")
            ticker = unidad_to_ticker.get(unidad) or _ticker_corto_fallback(unidad)
            if not ticker:
                continue
            aum_por_ticker[ticker] = {
                "unidad":    unidad,
                "cantidad":  float(d.get("cantidad") or 0),
                "precio":    float(d.get("precio") or 0),
                "valuacion": float(d.get("valuacion") or 0),
            }

    # ── 5. Construir filas y totales ────────────────────────────────────
    rows: list[dict[str, Any]] = []
    tot = {
        "costo_remanente":  0.0,
        "valor_actual":     0.0,    # del AuM (lo que físicamente tenés)
        "pnl_realizado":    0.0,
        "pnl_no_realizado": 0.0,
        "pnl_pasivo":       0.0,
        "pnl_total":        0.0,
    }

    todos = set(state) | set(aum_por_ticker)
    for ticker in todos:
        st  = state.get(ticker) or _new_state()
        aum = aum_por_ticker.get(ticker, {})

        qty_aum       = float(aum.get("cantidad") or 0)
        precio_actual = float(aum.get("precio") or 0)
        valor_aum     = float(aum.get("valuacion") or 0)
        qty_calc      = st["qty_actual"]
        costo_rem     = st["costo_remanente"]
        pnl_real      = st["pnl_realizado"]
        pnl_pas       = st["pnl_pasivo"]

        # PnL no-realizado: siempre usamos `valor_aum` (la valuación del
        # último snapshot, que YA tiene aplicado el divisor /100 para
        # bonos y demás reglas). Multiplicar qty_calc * precio_actual da
        # números irreales para bonos porque precio_actual viene en
        # paridad cruda.
        if qty_aum > 0 and qty_calc > 0:
            pnl_no_real: float | None = valor_aum - costo_rem
        else:
            pnl_no_real = None  # cerrado o sin boletos — no calculamos

        # Completeness: para entender qué tan confiable es el cálculo.
        if st["n_movimientos"] == 0:
            completeness = "sin_boletos"
        elif abs(qty_calc - qty_aum) < 0.01:
            completeness = "completa"
        else:
            completeness = "parcial"

        # Total = realizado + no-realizado + pasivo. Si no-realizado es
        # None (sin boletos), no lo sumamos.
        pnl_total = pnl_real + pnl_pas + (pnl_no_real or 0.0)

        breakdown = {k: round(v, 2) for k, v in st["breakdown_pasivo"].items() if v != 0}
        if st["breakdown_otros"] != 0:
            breakdown["Otros"] = round(st["breakdown_otros"], 2)

        precio_promedio = (
            costo_rem / qty_calc if qty_calc > 0 else None
        )

        rows.append({
            "ticker":            ticker,
            "unidad":            aum.get("unidad", ""),
            "qty_aum":           round(qty_aum, 4),
            "qty_calc":          round(qty_calc, 4),
            "qty_compras":       round(st["qty_compras"], 4),
            "qty_ventas":        round(st["qty_ventas"], 4),
            "precio_actual":     round(precio_actual, 4),
            "precio_promedio":   round(precio_promedio, 4) if precio_promedio is not None else None,
            "costo_remanente":   round(costo_rem, 2),
            "valor_actual_aum":  round(valor_aum, 2),
            "pnl_realizado":     round(pnl_real, 2),
            "pnl_no_realizado":  round(pnl_no_real, 2) if pnl_no_real is not None else None,
            "pnl_pasivo":        round(pnl_pas, 2),
            "breakdown_pasivo":  breakdown,
            "pnl_total":         round(pnl_total, 2),
            "completeness":      completeness,
            "moneda_mixta":      len(st["monedas"]) > 1,
            "n_movimientos":     st["n_movimientos"],
            "fechas_sin_mep":    sorted(st["fechas_sin_mep"]),
            "boletos":           st["boletos"],
        })

        tot["costo_remanente"]  += costo_rem
        tot["valor_actual"]     += valor_aum
        tot["pnl_realizado"]    += pnl_real
        tot["pnl_no_realizado"] += (pnl_no_real or 0.0)
        tot["pnl_pasivo"]       += pnl_pas
        tot["pnl_total"]        += pnl_total

    rows.sort(key=lambda r: -r["pnl_total"])

    return {
        "id_cuenta":    id_cuenta,
        "fecha_actual": fecha_actual,
        "rows":         rows,
        "totales": {
            "costo_remanente":  round(tot["costo_remanente"], 2),
            "valor_actual":     round(tot["valor_actual"], 2),
            "pnl_realizado":    round(tot["pnl_realizado"], 2),
            "pnl_no_realizado": round(tot["pnl_no_realizado"], 2),
            "pnl_pasivo":       round(tot["pnl_pasivo"], 2),
            "pnl_total":        round(tot["pnl_total"], 2),
        },
        "n_tickers": len(rows),
    }

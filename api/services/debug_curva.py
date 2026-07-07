"""Debug paso-a-paso del cálculo de TEA/TNA/Duration que hace engines/curvas.py.

Para un ticker dado, replica la lógica de `calcular_campos()` y devuelve
TODOS los inputs intermedios + el resultado recalculado, comparándolo
contra lo que está persistido en MarketSnapshot.metrics (la fuente que usan
el motor y la vista). Sirve para entender qué flujos, qué CER, qué TC y qué
settlement se usaron. El precio sale de MarketSnapshot.last_price (precio de
pantalla del market data), NO de un trade — un bono puede cotizar sin operar.

NO toca el motor — usa los mismos helpers (`xirr`, `macaulay_duration`,
`monto_flujo_*`, etc.) y reproduce el flow leyendo data fresca de Mongo.

Soporta: tasa_fija, cer, soberanos, dolar_linked y ONs (on / on_<sector>).
Para ONs explica por qué NO hay TEA (XIRR no convergió o fuera del rango del
motor) mostrando el cashflow — útil para los ARS con escala de precio rara.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from engines.curvas import (
    cargar_a3500_actual,
    cargar_cer,
    cargar_dias_habiles,
    cargar_mep_actual,
    convexity,
    fecha_flujo,
    get_cer_liquidacion,
    macaulay_duration,
    monto_flujo,
    monto_flujo_cer,
    monto_flujo_soberano,
    precio_soberano_a_usd,
    siguiente_dia_habil,
    xirr,
)


def _last_trade(ticker_full: str) -> dict | None:
    """Precio + métricas del MarketSnapshot — la MISMA fuente que usan el motor
    (engines/curvas.py) y la vista (renta_fija.listar_curva). NO TimeSales: un
    bono puede tener precio de pantalla (market data) sin haber operado, así que
    leer TimeSales daba 'sin trades' aunque la vista muestre precio."""
    from core.market_snapshot import snapshot_docs
    doc = snapshot_docs([ticker_full]).get(ticker_full)  # SQL-only
    if not doc:
        return None
    m = doc.get("metrics") or {}
    return {
        "timestamp":    doc.get("updated_at"),
        "price":        m.get("last_price"),
        "TEA":          m.get("TEA"),
        "TEM":          m.get("TEM"),
        "duration":     m.get("duration"),
        "mod_duration": m.get("mod_duration"),
        "convexity":    m.get("convexity"),
        "paridad":      m.get("paridad"),
    }


def _norm_diff(actual: float | None, esperado: float | None, tol: float = 1e-3) -> str:
    """OK si está dentro de tol, sino devuelve el delta firmado."""
    if actual is None and esperado is None:
        return "—"
    if actual is None or esperado is None:
        return "MISSING"
    delta = actual - esperado
    if abs(delta) < tol:
        return "OK"
    return f"{delta:+.6f}"


def debug_calculo_tea(ticker_corto: str) -> dict[str, Any]:
    """Reproduce el cálculo de TEA paso a paso para un ticker.

    Lee instrumento de mercado.curvas (por ticker_corto), precio/métricas del
    MarketSnapshot (SQL mercado.market_snapshot), dependencias (CER / MEP /
    A3500 actuales), y devuelve los pasos intermedios + el resultado
    recalculado contra el persistido.
    """
    # ── 1. Buscar el instrumento ─────────────────────────────────────────
    from core import curvas_sql
    inst = curvas_sql.find_one(ticker_corto)   # mercado.curvas (SQL)
    if not inst:
        return {
            "ok":      False,
            "message": f"No encontré ticker_corto '{ticker_corto}' en mercado.curvas.",
        }

    ticker_full = inst.get("ticker") or inst.get("curva", "")
    curva = inst.get("curva", "")
    flujos_raw = inst.get("flujos") or []
    valor_nominal = float(inst.get("valor_nominal", 100))
    fecha_vto_str = inst.get("fecha_vencimiento", "")

    instrumento_resp = {
        "ticker":            ticker_full,
        "ticker_corto":      ticker_corto,
        "curva":             curva,
        "fecha_emision":     str(inst.get("fecha_emision", ""))[:10],
        "fecha_vencimiento": fecha_vto_str[:10] if fecha_vto_str else None,
        "valor_nominal":     valor_nominal,
        "cer_emision":       inst.get("cer_emision"),
        "n_flujos":          len(flujos_raw),
    }

    # ── 2. Precio del MarketSnapshot (market data — puede no haber operado) ──
    trade = _last_trade(ticker_full)
    if not trade or trade.get("price") in (None, 0):
        return {
            "ok":           False,
            "message":      f"Sin precio (last_price) en MarketSnapshot para '{ticker_full}'. "
                            "El bono no tiene precio de pantalla todavía (¿motor sin suscribirlo?).",
            "instrumento":  instrumento_resp,
        }

    timestamp = trade.get("timestamp")
    precio = float(trade.get("price") or 0)
    fecha_trade = timestamp.date() if isinstance(timestamp, datetime) else None

    trade_resp = {
        "timestamp":           timestamp.isoformat() if isinstance(timestamp, datetime) else None,
        "price":               precio,
        "TEA_persistido":      trade.get("TEA"),
        "TEM_persistido":      trade.get("TEM"),
        "duration_persistido": trade.get("duration"),
        "mod_duration_persistido": trade.get("mod_duration"),
        "convexity_persistido": trade.get("convexity"),
        "paridad_persistido":  trade.get("paridad"),
    }

    if precio <= 0 or not fecha_trade or not fecha_vto_str:
        return {
            "ok":          False,
            "message":     "Precio o fechas inválidos.",
            "instrumento": instrumento_resp,
            "trade":       trade_resp,
        }

    fecha_vto = date.fromisoformat(fecha_vto_str[:10])
    dias_a_vto = (fecha_vto - fecha_trade).days

    # ── 3. Cargar dependencias según curva ──────────────────────────────
    # cargar_* ya son SQL-native (el param client es vestigial) — no se pasa.
    dias_habiles = cargar_dias_habiles()
    cer_dict = cargar_cer() if curva == "cer" else {}
    _es_on = curva == "on" or curva.startswith("on_")
    mep = cargar_mep_actual() if (curva == "soberanos" or _es_on) else None
    tc_a3500 = cargar_a3500_actual() if (curva == "dolar_linked" or _es_on) else None

    # ── 4. Settlement ───────────────────────────────────────────────────
    settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
    fecha_settlement = (
        date.fromisoformat(settlement_str) if settlement_str else fecha_trade
    )
    settlement_resp = {
        "fecha_trade":       fecha_trade.isoformat(),
        "fecha_settlement":  fecha_settlement.isoformat(),
        "dias_a_vto_trade":  dias_a_vto,
        "dias_a_vto_settle": (fecha_vto - fecha_settlement).days,
        "regla":             "T+1 hábil para todas las curvas (incluso tasa_fija desde 2026-04-29)",
    }

    # ── 5. Branch por curva ─────────────────────────────────────────────
    cer_info = None
    tc_info = None
    flujos_futuros: list[dict] = []
    cashflow: list[dict] = []
    calculado: dict[str, Any] = {}
    error_calc: str | None = None
    fecha_base_calc: date

    try:
        if curva == "tasa_fija":
            # Fecha base = settlement (T+1 hábil), igual que las otras curvas.
            # Misma convención que la calculadora local de la mesa:
            # =POW(payoff/precio, 30.4166/dias_settle) - 1
            fecha_base_calc = fecha_settlement
            dias_a_vto_efectivo = (fecha_vto - fecha_settlement).days
            futuros = [
                (fecha_flujo(f), monto_flujo(f), f)
                for f in flujos_raw
                if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement and monto_flujo(f) > 0
            ]
            flujos_futuros = [
                {"fecha": fd.isoformat(), "monto": round(m, 4), "raw": f}
                for fd, m, f in futuros
            ]

            if futuros:
                fechas_dt = [datetime.combine(fecha_base_calc, datetime.min.time())] + [
                    datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros
                ]
                cf = [-precio] + [m for _, m, _ in futuros]
                tea = xirr(fechas_dt, cf)
            else:
                flujo_vto = inst.get("flujo_vencimiento")
                if flujo_vto and flujo_vto > 0:
                    tea = (flujo_vto / precio) ** (365.0 / dias_a_vto_efectivo) - 1
                    futuros = [(fecha_vto, float(flujo_vto), {"fecha": fecha_vto_str, "flujo_vto": flujo_vto})]
                    flujos_futuros = [{"fecha": fecha_vto.isoformat(), "monto": float(flujo_vto), "raw": {"flujo_vto": flujo_vto}}]
                else:
                    raise ValueError("Sin flujos futuros y sin flujo_vencimiento.")

            cashflow = [{"fecha": fecha_base_calc.isoformat(), "monto": -round(precio, 4), "concepto": "PRECIO (-)"}]
            cashflow += [{"fecha": fd.isoformat(), "monto": round(m, 4), "concepto": "FLUJO"} for fd, m, _ in futuros]

            if tea is not None:
                calculado["TEA"] = round(tea, 6)
                calculado["TEM"] = round((1 + tea) ** (1 / 12) - 1, 6)
                if futuros:
                    fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros]
                    montos_flujos = [m for _, m, _ in futuros]
                    fecha_base_dt = datetime.combine(fecha_base_calc, datetime.min.time())
                    dur = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                    conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                    if dur is not None:
                        calculado["duration"] = dur
                        calculado["mod_duration"] = round(dur / (1 + tea), 4)
                    if conv is not None:
                        calculado["convexity"] = conv

        elif curva == "cer":
            fecha_base_calc = fecha_settlement
            cer_emision = inst.get("cer_emision")
            if not cer_emision:
                raise ValueError("Sin cer_emision en el instrumento.")
            cer_liq = get_cer_liquidacion(cer_dict, dias_habiles, settlement_str, n=10)
            if not cer_liq:
                raise ValueError("Sin CER de liquidación (T-10 hábiles).")
            ratio = cer_liq / cer_emision
            cer_info = {
                "cer_emision": float(cer_emision),
                "cer_liq":     float(cer_liq),
                "ratio":       round(ratio, 6),
            }

            futuros = [
                (fecha_flujo(f), monto_flujo_cer(f, valor_nominal) * ratio, f)
                for f in flujos_raw
                if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement and monto_flujo_cer(f, valor_nominal) > 0
            ]
            flujos_futuros = [
                {"fecha": fd.isoformat(), "monto": round(m, 4), "raw": f}
                for fd, m, f in futuros
            ]

            if not futuros:
                raise ValueError("Sin flujos futuros al settlement.")

            fechas_dt = [datetime.combine(fecha_base_calc, datetime.min.time())] + [
                datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros
            ]
            cf = [-precio] + [m for _, m, _ in futuros]
            tea = xirr(fechas_dt, cf)
            cashflow = [{"fecha": fecha_base_calc.isoformat(), "monto": -round(precio, 4), "concepto": "PRECIO (-)"}]
            cashflow += [{"fecha": fd.isoformat(), "monto": round(m, 4), "concepto": "FLUJO×ratio"} for fd, m, _ in futuros]

            precio_tecnico = valor_nominal * ratio
            calculado["paridad"] = round(precio / precio_tecnico * 100, 4)

            if tea is not None:
                calculado["TEA"] = round(tea, 6)
                fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros]
                montos_flujos = [m for _, m, _ in futuros]
                fecha_base_dt = datetime.combine(fecha_base_calc, datetime.min.time())
                dur = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                if dur is not None:
                    calculado["duration"] = dur
                    calculado["mod_duration"] = round(dur / (1 + tea), 4)
                if conv is not None:
                    calculado["convexity"] = conv

        elif curva == "soberanos":
            fecha_base_calc = fecha_settlement
            tc_info = {"fuente": "MEP", "valor": mep}
            precio_usd = precio_soberano_a_usd(precio, ticker_full, mep)
            if precio_usd is None:
                raise ValueError("Precio_USD = None (sin MEP o ticker no soporta conversión).")
            tc_info["precio_usd"] = round(precio_usd, 6)

            futuros = [
                (fecha_flujo(f), monto_flujo_soberano(f, valor_nominal), f)
                for f in flujos_raw
                if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement and monto_flujo_soberano(f, valor_nominal) > 0
            ]
            flujos_futuros = [
                {"fecha": fd.isoformat(), "monto": round(m, 4), "raw": f}
                for fd, m, f in futuros
            ]
            if not futuros:
                raise ValueError("Sin flujos futuros USD al settlement.")

            primer = futuros[0][2]
            residual_vivo = float(primer.get("residual_previo_pct", 100))
            if residual_vivo > 0:
                calculado["paridad"] = round(precio_usd / residual_vivo * 100, 4)

            fechas_dt = [datetime.combine(fecha_base_calc, datetime.min.time())] + [
                datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros
            ]
            cf = [-precio_usd] + [m for _, m, _ in futuros]
            tea = xirr(fechas_dt, cf)
            cashflow = [{"fecha": fecha_base_calc.isoformat(), "monto": -round(precio_usd, 4), "concepto": "PRECIO_USD (-)"}]
            cashflow += [{"fecha": fd.isoformat(), "monto": round(m, 4), "concepto": "FLUJO_USD"} for fd, m, _ in futuros]

            if tea is not None:
                calculado["TEA"] = round(tea, 6)
                fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros]
                montos_flujos = [m for _, m, _ in futuros]
                fecha_base_dt = datetime.combine(fecha_base_calc, datetime.min.time())
                dur = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                if dur is not None:
                    calculado["duration"] = dur
                    calculado["mod_duration"] = round(dur / (1 + tea), 4)
                if conv is not None:
                    calculado["convexity"] = conv

        elif curva == "dolar_linked":
            fecha_base_calc = fecha_settlement
            if not tc_a3500:
                raise ValueError("Sin TC A3500 (feed MAE mayorista offline).")
            tc_info = {"fuente": "A3500_mayorista_mae", "valor": tc_a3500}
            precio_usd = precio / tc_a3500
            tc_info["precio_usd"] = round(precio_usd, 6)

            # Igual que el motor (engines.curvas rama dolar_linked): flujos en shape
            # porcentual sobre VN → monto_flujo_soberano, NO monto_flujo (que busca
            # amortizacion/interes absolutos y devuelve 0 para estos → falso "sin flujos").
            futuros = [
                (fecha_flujo(f), monto_flujo_soberano(f, valor_nominal), f)
                for f in flujos_raw
                if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement
                and monto_flujo_soberano(f, valor_nominal) > 0
            ]
            flujos_futuros = [
                {"fecha": fd.isoformat(), "monto": round(m, 4), "raw": f}
                for fd, m, f in futuros
            ]
            if not futuros:
                raise ValueError("Sin flujos futuros USD al settlement.")

            fechas_dt = [datetime.combine(fecha_base_calc, datetime.min.time())] + [
                datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros
            ]
            cf = [-precio_usd] + [m for _, m, _ in futuros]
            tea = xirr(fechas_dt, cf)
            cashflow = [{"fecha": fecha_base_calc.isoformat(), "monto": -round(precio_usd, 4), "concepto": "PRECIO_USD (-)"}]
            cashflow += [{"fecha": fd.isoformat(), "monto": round(m, 4), "concepto": "FLUJO_USD"} for fd, m, _ in futuros]

            if tea is not None:
                calculado["TEA"] = round(tea, 6)
                calculado["TEM"] = round((1 + tea) ** (1 / 12) - 1, 6)
                fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros]
                montos_flujos = [m for _, m, _ in futuros]
                fecha_base_dt = datetime.combine(fecha_base_calc, datetime.min.time())
                dur = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                if dur is not None:
                    calculado["duration"] = dur
                    calculado["mod_duration"] = round(dur / (1 + tea), 4)
                if conv is not None:
                    calculado["convexity"] = conv

        elif _es_on:
            # ONs: USD → precio a USD (igual que soberanos); ARS → precio peso
            # directo. Flujos en shape nativo BondsMaster (montos absolutos):
            # monto = amortizacion + interes.
            fecha_base_calc = fecha_settlement
            moneda = (inst.get("moneda_flujo") or "USD").upper()
            if moneda == "USD":
                precio_calc = precio_soberano_a_usd(precio, ticker_full, mep)
                if precio_calc is None:
                    raise ValueError("Precio_USD = None (sin MEP o ticker no convertible).")
                tc_info = {"fuente": "MEP", "valor": mep, "precio_usd": round(precio_calc, 6)}
            elif moneda == "DL":
                # Pata peso (precio ~144.000) → ÷A3500. Pata USD (precio ~100) →
                # tal cual. Detecta por escala (igual que engines/curvas.py).
                if precio >= 1000:
                    if not tc_a3500 or tc_a3500 <= 0:
                        raise ValueError("Sin TC A3500 (feed MAE mayorista offline) para dólar-linked.")
                    precio_calc = precio / tc_a3500
                    tc_info = {"fuente": "A3500_mayorista_mae (dólar-linked, pata peso)",
                               "valor": tc_a3500, "precio_usd": round(precio_calc, 6)}
                else:
                    precio_calc = precio
                    tc_info = {"fuente": "DL pata USD (precio ya en dólares)", "valor": None,
                               "precio_usd": round(precio_calc, 6)}
            else:
                precio_calc = precio
                tc_info = {"fuente": "ARS (peso directo)", "valor": None, "precio_calc": round(precio_calc, 6)}

            futuros = [
                (fecha_flujo(f), monto_flujo(f), f)
                for f in flujos_raw
                if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement and monto_flujo(f) > 0
            ]
            flujos_futuros = [
                {"fecha": fd.isoformat(), "monto": round(m, 4), "raw": f}
                for fd, m, f in futuros
            ]
            if not futuros:
                raise ValueError(
                    f"Sin flujos futuros > settlement {fecha_settlement.isoformat()} "
                    f"con monto>0 (de {len(flujos_raw)} flujos del instrumento)."
                )

            # Residual vivo = Σ amortizaciones futuras (robusto, no depende de
            # valor_residual que puede venir en otra escala que el flujo).
            residual_vivo = sum(float(f.get("amortizacion") or 0) for _, _, f in futuros)
            if residual_vivo > 0:
                calculado["paridad"] = round(precio_calc / residual_vivo * 100, 4)

            fechas_dt = [datetime.combine(fecha_base_calc, datetime.min.time())] + [
                datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros
            ]
            cf = [-precio_calc] + [m for _, m, _ in futuros]
            tea = xirr(fechas_dt, cf)
            cashflow = [{"fecha": fecha_base_calc.isoformat(), "monto": -round(precio_calc, 4),
                         "concepto": f"PRECIO ({moneda}) (-)"}]
            cashflow += [{"fecha": fd.isoformat(), "monto": round(m, 4), "concepto": "FLUJO"}
                         for fd, m, _ in futuros]

            # El motor solo PERSISTE la TEA si entra en (-0.5, 50). Si XIRR dio un
            # número fuera de ese rango (o None), por eso no se ve TEA en la vista.
            if tea is None:
                error_calc = ("XIRR no convergió → el motor cae a duration naïve y NO "
                              "persiste TEA. Mirá el cashflow: precio vs montos de flujo "
                              "(típico en ARS: escala del precio ≠ escala del flujo por 100 VN).")
            elif not (-0.5 < tea < 50):
                error_calc = (f"XIRR dio tea={tea:.4f} FUERA del rango del motor (-0.5, 50) "
                              f"→ NO persiste TEA. Revisá el cashflow (escala precio vs flujo).")
            else:
                calculado["TEA"] = round(tea, 6)
                calculado["TEM"] = round((1 + tea) ** (1 / 12) - 1, 6)
                fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in futuros]
                montos_flujos = [m for _, m, _ in futuros]
                fecha_base_dt = datetime.combine(fecha_base_calc, datetime.min.time())
                dur = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                if dur is not None:
                    calculado["duration"] = dur
                    calculado["mod_duration"] = round(dur / (1 + tea), 4)
                if conv is not None:
                    calculado["convexity"] = conv

        else:
            error_calc = f"Curva '{curva}' no soportada por debug_curva."

    except Exception as e:
        error_calc = f"{type(e).__name__}: {e}"

    # ── 6. Diff persistido vs calculado ──────────────────────────────────
    diff = {
        "TEA":          _norm_diff(calculado.get("TEA"),          trade.get("TEA")),
        "TEM":          _norm_diff(calculado.get("TEM"),          trade.get("TEM")),
        "duration":     _norm_diff(calculado.get("duration"),     trade.get("duration")),
        "mod_duration": _norm_diff(calculado.get("mod_duration"), trade.get("mod_duration")),
        "convexity":    _norm_diff(calculado.get("convexity"),    trade.get("convexity")),
        "paridad":      _norm_diff(calculado.get("paridad"),      trade.get("paridad")),
    }

    return {
        "ok":              True,
        "instrumento":     instrumento_resp,
        "trade":           trade_resp,
        "settlement":      settlement_resp,
        "cer_info":        cer_info,
        "tc_info":         tc_info,
        "flujos_futuros":  flujos_futuros,
        "cashflow_xirr":   cashflow,
        "calculado":       calculado,
        "diff":            diff,
        "error_calc":      error_calc,
    }

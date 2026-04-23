"""Análisis de sensibilidad de retorno total a 1 año por escenario de TIR.

Para cada bono de la curva soberanos, simula:
  1. Cuánto valdría el bono dentro de N días si su TIR converge a un valor
     dado (PV de los flujos remanentes post-horizonte descontados a esa TIR).
  2. Cupones + amortizaciones cobrados durante el horizonte (carry).
  3. Retorno total = (precio_proyectado + carry) / precio_actual − 1.

La grilla típica: 5 bonos × 5 TIRs (9-13%) → tabla de 25 celdas con %
de retorno esperado en cada combinación.

Usado por la tab "Retorno Total" de /retorno en acaquant-web.
"""
from __future__ import annotations

from datetime import date, timedelta

from api.cache import cached
from api.db import get_db_trading
from engines.curvas import fecha_flujo, monto_flujo_soberano

_DEFAULT_TIRS: tuple[float, ...] = (0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11)
_DEFAULT_HORIZONTE_DIAS = 365


def _precio_actual_usd(db, ticker_full: str) -> float | None:
    """Último precio del ticker desde MarketSnapshot. Ya viene en USD para
    los soberanos D/C; conversión MEP de tickers en pesos no soportada
    en esta primera versión."""
    snap = db["MarketSnapshot"].find_one(
        {"ticker": ticker_full},
        {"_id": 0, "metrics.last_price": 1},
    )
    if snap and snap.get("metrics", {}).get("last_price"):
        return float(snap["metrics"]["last_price"])
    return None


def _flujos_calendario(flujos_raw, vn: float) -> list[tuple[date, float]]:
    """Convierte la lista de flujos del prospecto a [(fecha, monto USD)]."""
    out: list[tuple[date, float]] = []
    for f in flujos_raw or []:
        fd = fecha_flujo(f)
        if not fd:
            continue
        m = monto_flujo_soberano(f, vn)
        if m > 0:
            out.append((fd, m))
    return sorted(out, key=lambda x: x[0])


def _precio_proyectado(
    flujos_post_horizonte: list[tuple[date, float]],
    tir: float,
    fecha_horizonte: date,
) -> float:
    """PV de los flujos a la fecha_horizonte descontados a la TIR.

    Usa convención actual/365 (consistente con xirr en engines.curvas).
    """
    pv = 0.0
    for fd, monto in flujos_post_horizonte:
        t = (fd - fecha_horizonte).days / 365.0
        if t <= 0:
            continue
        pv += monto / ((1 + tir) ** t)
    return pv


@cached(ttl=60)
def sensibilidad_retorno_total(
    curva: str = "soberanos",
    tirs: tuple[float, ...] = _DEFAULT_TIRS,
    horizonte_dias: int = _DEFAULT_HORIZONTE_DIAS,
    modo: str = "absoluta",
    tipos: tuple[str, ...] | None = None,
) -> list[dict]:
    """Devuelve tabla de sensibilidad: 1 entrada por bono, con escenarios.

    `modo`:
      - "absoluta"  → `tirs` se interpretan como TIRs finales (ej. 0.06 = 6%).
      - "relativa"  → `tirs` se interpretan como SHOCKS en puntos porcentuales
                       sobre la TEA actual de cada bono. Ej. tirs=(-0.02, 0,
                       0.02) y tea_actual=0.092 → escenarios reales 7.2%,
                       9.2%, 11.2% para ese bono. Permite comparar bonos
                       con rangos centrados en su propia TEA.

    Output:
    [
      {
        ticker, ticker_completo, fecha_vencimiento,
        precio_actual, tea_actual, duration, paridad,
        cobrado_anio, n_flujos_anio,
        escenarios: [{tir, shock_pp, precio_1anio, retorno_total}, ...]
      },
      ...
    ]
    """
    db = get_db_trading()
    modo = (modo or "absoluta").lower()
    if modo not in ("absoluta", "relativa"):
        modo = "absoluta"

    filtro: dict = {"curva": curva}
    if tipos:
        # Normaliza a lowercase y matchea contra Trading.Curvas.tipo (que
        # ya está en lowercase: 'globales' / 'bonares' / etc).
        filtro["tipo"] = {"$in": [t.lower() for t in tipos]}

    bonos = list(db["Curvas"].find(
        filtro,
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
         "valor_nominal": 1, "fecha_vencimiento": 1, "flujos": 1},
    ))

    hoy = date.today()
    horizonte = hoy + timedelta(days=horizonte_dias)

    out: list[dict] = []
    for bono in bonos:
        ticker_full = bono.get("ticker")
        ticker_corto = bono.get("ticker_corto")
        vn = float(bono.get("valor_nominal") or 100)

        flujos = _flujos_calendario(bono.get("flujos", []), vn)
        if not flujos:
            continue

        precio_actual = _precio_actual_usd(db, ticker_full)
        if precio_actual is None or precio_actual <= 0:
            continue

        flujos_anio = [(fd, m) for fd, m in flujos if hoy < fd <= horizonte]
        cobrado_anio = sum(m for _, m in flujos_anio)

        flujos_post = [(fd, m) for fd, m in flujos if fd > horizonte]

        # TEA / duration / paridad del MarketSnapshot (escritos por motor_curvas).
        # Se necesita ANTES del cálculo de escenarios porque el modo relativo
        # los aplica como shock sobre tea_actual.
        snap = db["MarketSnapshot"].find_one(
            {"ticker": ticker_full},
            {"_id": 0, "metrics.TEA": 1, "metrics.duration": 1, "metrics.paridad": 1},
        )
        ms = (snap or {}).get("metrics") or {}
        tea_actual = ms.get("TEA")

        # Construir las TIRs reales según el modo.
        if modo == "relativa":
            if tea_actual is None:
                # Sin TEA actual no hay base para shockear → skip bono.
                continue
            tirs_reales = [(s, tea_actual + s) for s in tirs]
        else:
            tirs_reales = [(None, t) for t in tirs]

        if not flujos_post:
            # Bono que vence dentro del horizonte → retorno solo carry.
            ret_carry = cobrado_anio / precio_actual - 1
            escenarios = [
                {"shock_pp":      round(shock, 6) if shock is not None else None,
                 "tir":           round(tir, 6),
                 "precio_1anio":  0.0,
                 "retorno_total": round(ret_carry, 6)}
                for shock, tir in tirs_reales
            ]
        else:
            escenarios = []
            for shock, tir in tirs_reales:
                precio_1anio = _precio_proyectado(flujos_post, tir, horizonte)
                retorno_total = (precio_1anio + cobrado_anio) / precio_actual - 1
                escenarios.append({
                    "shock_pp":      round(shock, 6) if shock is not None else None,
                    "tir":           round(tir, 6),
                    "precio_1anio":  round(precio_1anio, 4),
                    "retorno_total": round(retorno_total, 6),
                })

        out.append({
            "ticker":            ticker_corto,
            "ticker_completo":   ticker_full,
            "tipo":              bono.get("tipo"),
            "fecha_vencimiento": str(bono.get("fecha_vencimiento"))[:10]
                                 if bono.get("fecha_vencimiento") else None,
            "precio_actual":     round(precio_actual, 4),
            "tea_actual":        ms.get("TEA"),
            "duration":          ms.get("duration"),
            "paridad":           ms.get("paridad"),
            "cobrado_anio":      round(cobrado_anio, 4),
            "n_flujos_anio":     len(flujos_anio),
            "escenarios":        escenarios,
        })

    # Ordenar por fecha de vencimiento ascendente (corto → largo).
    out.sort(key=lambda x: x.get("fecha_vencimiento") or "9999")
    return out

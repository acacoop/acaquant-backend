"""Análisis de sensibilidad de retorno total a escenarios de TIR.

Para cada bono de la curva soberanos, responde:
  "Si dentro de N días el bono cotiza a TIR X, ¿cuál es el retorno total
   (precio proyectado + cupones/amortizaciones cobradas) vs el precio
   actual?"

  upside = (precio_objetivo + cobrado_horizonte) / precio_actual − 1

donde precio_objetivo = PV de los flujos post-horizonte descontados a la TIR
del escenario desde fecha_horizonte, y cobrado_horizonte = suma de cupones y
amortizaciones que caen entre hoy y el horizonte.

Usado por la tab "Análisis Sensibilidad" de /retorno en acaquant-web.
"""
from __future__ import annotations

from datetime import date, timedelta

from api.cache import cached
from core import curvas_sql
from engines.curvas import fecha_flujo, monto_flujo_soberano

_DEFAULT_TIRS: tuple[float, ...] = (0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11)
_DEFAULT_HORIZONTE_DIAS = 0  # 0 = upside instantáneo (sin pull-to-par)


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


def _pv_a_tir(
    flujos_futuros: list[tuple[date, float]],
    tir: float,
    fecha_ref: date,
) -> float:
    """PV de los flujos descontados a la TIR desde fecha_ref.

    Usa convención actual/365 (consistente con xirr en engines.curvas).
    """
    pv = 0.0
    for fd, monto in flujos_futuros:
        t = (fd - fecha_ref).days / 365.0
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
    """Devuelve tabla de sensibilidad de precio: 1 entrada por bono, con
    escenarios de TIR.

    `horizonte_dias`: a cuántos días proyectar el precio. 0 = hoy (upside
    instantáneo); 365 = dentro de 1 año (incluye pull-to-par pero NO carry);
    etc. Capital-only siempre — el upside no suma los cupones cobrados en
    la ventana.

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
        cobrado_horizonte, n_flujos_horizonte,
        escenarios: [{tir, shock_pp, precio_objetivo, upside}, ...]
      },
      ...
    ]

    Con horizonte=0, cobrado_horizonte=0 → el upside colapsa a upside de
    precio puro (capital-only, sin paso del tiempo).
    """
    modo = (modo or "absoluta").lower()
    if modo not in ("absoluta", "relativa"):
        modo = "absoluta"

    # Master desde SQL mercado.curvas. El filtro por `tipo` ($in) no mapea a
    # columna → se aplica en Python sobre el doc completo (tipo ya lowercase).
    bonos = curvas_sql.por_curva(curva)
    if tipos:
        tset = {t.lower() for t in tipos}
        bonos = [b for b in bonos if (b.get("tipo") or "").lower() in tset]

    hoy = date.today()
    horizonte = hoy + timedelta(days=max(horizonte_dias, 0))

    # Pre-carga de MarketSnapshot por TODOS los tickers en UNA query (antes
    # eran 2 find_one por bono → 2N round-trips a Atlas). last_price + TEA +
    # duration + paridad de una.
    tickers_full = [b.get("ticker") for b in bonos if b.get("ticker")]
    # SQL-only (mercado.market_snapshot): metrics anidado, claves Mongo (TEA/paridad…).
    from core.market_snapshot import snapshot_docs
    snap_map: dict[str, dict] = {
        t: doc["metrics"] for t, doc in snapshot_docs(tickers_full).items()
    }

    out: list[dict] = []
    for bono in bonos:
        ticker_full = bono.get("ticker")
        ticker_corto = bono.get("ticker_corto")
        vn = float(bono.get("valor_nominal") or 100)

        flujos = _flujos_calendario(bono.get("flujos", []), vn)
        if not flujos:
            continue

        _lp = snap_map.get(ticker_full, {}).get("last_price")
        precio_actual = float(_lp) if _lp else None
        if precio_actual is None or precio_actual <= 0:
            continue

        # Flujos que quedan DESPUÉS del horizonte (componen el precio
        # objetivo) y los que caen ENTRE hoy y el horizonte (componen el
        # carry cobrado). Si horizonte=0, el carry es 0 y flujos_post =
        # todos los flujos futuros.
        flujos_post = [(fd, m) for fd, m in flujos if fd > horizonte]
        flujos_periodo = [(fd, m) for fd, m in flujos if hoy < fd <= horizonte]
        cobrado_horizonte = sum(m for _, m in flujos_periodo)
        if not flujos_post:
            # Bono que vence antes del horizonte → no hay precio proyectado.
            continue

        # TEA / duration / paridad del MarketSnapshot (escritos por motor_curvas),
        # ya pre-cargados en snap_map (sin query por bono).
        ms = snap_map.get(ticker_full, {})
        tea_actual = ms.get("TEA")

        # Construir las TIRs reales según el modo.
        if modo == "relativa":
            if tea_actual is None:
                # Sin TEA actual no hay base para shockear → skip bono.
                continue
            tirs_reales = [(s, tea_actual + s) for s in tirs]
        else:
            tirs_reales = [(None, t) for t in tirs]

        escenarios = []
        for shock, tir in tirs_reales:
            precio_objetivo = _pv_a_tir(flujos_post, tir, horizonte)
            upside = (precio_objetivo + cobrado_horizonte) / precio_actual - 1
            escenarios.append({
                "shock_pp":         round(shock, 6) if shock is not None else None,
                "tir":              round(tir, 6),
                "precio_objetivo":  round(precio_objetivo, 4),
                "upside":           round(upside, 6),
            })

        out.append({
            "ticker":              ticker_corto,
            "ticker_completo":     ticker_full,
            "tipo":                bono.get("tipo"),
            "fecha_vencimiento":   str(bono.get("fecha_vencimiento"))[:10]
                                   if bono.get("fecha_vencimiento") else None,
            "precio_actual":       round(precio_actual, 4),
            "tea_actual":          ms.get("TEA"),
            "duration":            ms.get("duration"),
            "paridad":             ms.get("paridad"),
            "cobrado_horizonte":   round(cobrado_horizonte, 4),
            "n_flujos_horizonte":  len(flujos_periodo),
            "escenarios":          escenarios,
        })

    # Ordenar por fecha de vencimiento ascendente (corto → largo).
    out.sort(key=lambda x: x.get("fecha_vencimiento") or "9999")
    return out

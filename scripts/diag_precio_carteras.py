"""diag_precio_carteras.py — de dónde sale el PRECIO de cada posición en CARTERAS.

Responde tres preguntas, TODAS con dato medido (nada de estimaciones):

  1) ¿Por qué MSFT muestra el CIERRE y no el LIVE?
     Replica exactamente la cadena de fallback de `pnl._valor_actual_live` posición
     por posición, pero desglosando el tier real: last_price / closing_price /
     snapshots_cierre / valor del AuM. El código de producción marca los DOS
     primeros como "live" — este diag los separa para que se vea cuál disparó.

  2) ¿Hay UNA sola fuente de verdad del precio si entran varias personas a la vez?
     Mide la frescura real de `valuaciones.portfolio_snapshot` (max/percentiles de
     `updated_at`) — la tabla es única, así que lo que puede diferir entre usuarios
     es la antigüedad, no la fuente.

  3) ¿Cuánto cuesta recalcular constantemente?
     Cronometra el motor de PnL con cache FRÍO, desglosado por etapa (mapas de
     assets / pricing live / pricing cierre / boletos / posición / motor), y lo
     cruza con la latencia REAL de los endpoints en `manager.latencia_endpoints`.

Read-only. No escribe nada.

Ejecutar (desde /root/TradingAV):
    python -m scripts.diag_precio_carteras
    python -m scripts.diag_precio_carteras --cuenta 805 --ticker MSFT
"""
from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime

from api.services._sql import _f, _q

# Mismo conjunto que usa el motor para descartar un instrumento inválido.
from api.services.pnl import _PLACEHOLDERS_INSTRUMENTO


def _sep(t: str) -> None:
    print("\n" + "=" * 100)
    print(t)
    print("=" * 100)


def _edad_min(ts) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds() / 60.0


def _fmt(v, n: int = 2) -> str:
    return "—" if v is None else f"{v:,.{n}f}"


# ── carga de tablas base ─────────────────────────────────────────────────────


def _instrumentos_by_unidad() -> dict[str, str]:
    out: dict[str, str] = {}
    for d in _q("SELECT unidad, instrumento FROM portafolio.assets"):
        instr = (d.get("instrumento") or "").strip()
        if d.get("unidad") and instr and instr not in _PLACEHOLDERS_INSTRUMENTO:
            out[d["unidad"]] = instr
    return out


def _snap_by_ticker() -> dict[str, dict]:
    return {
        d["ticker"]: d
        for d in _q("SELECT ticker, last_price, closing_price, updated_at "
                    "FROM valuaciones.portfolio_snapshot")
    }


def _cierre_by_ticker() -> dict[str, dict]:
    return {
        d["ticker"]: d
        for d in _q("SELECT ticker, last_price, fecha FROM mercado.snapshots_cierre")
    }


def _tier(unidad: str, instr_map: dict, snap_map: dict, cierre_map: dict) -> tuple[str, dict]:
    """Replica `_valor_actual_live` pero devolviendo el tier REAL (4 valores, no 3).

    Devuelve (tier, evidencia). tier ∈ {sin_instrumento, live_last, live_closing,
    cierre, aum}. En producción `live_last` y `live_closing` se reportan AMBOS
    como fuente="live" — esa es exactamente la confusión que este diag desarma.
    """
    ev: dict = {}
    instr = instr_map.get(unidad, "")
    ev["instrumento"] = instr
    if not instr:
        return "sin_instrumento", ev

    snap = snap_map.get(instr)
    ev["en_portfolio_snapshot"] = snap is not None
    if snap:
        ev["last_price"] = _f(snap.get("last_price"))
        ev["closing_price"] = _f(snap.get("closing_price"))
        ev["updated_at"] = snap.get("updated_at")
        ev["edad_min"] = _edad_min(snap.get("updated_at"))
        if ev["last_price"] and ev["last_price"] > 0:
            return "live_last", ev
        if ev["closing_price"] and ev["closing_price"] > 0:
            return "live_closing", ev

    snc = cierre_map.get(instr)
    if snc:
        ev["cierre_price"] = _f(snc.get("last_price"))
        ev["cierre_fecha"] = snc.get("fecha")
        if ev["cierre_price"] and ev["cierre_price"] > 0:
            return "cierre", ev
    return "aum", ev


# ── 1. detalle por cuenta ────────────────────────────────────────────────────


def seccion_cuenta(cuenta: str, ticker_focus: str | None,
                   instr_map: dict, snap_map: dict, cierre_map: dict) -> None:
    _sep(f"1) DETALLE DE PRECIO — cuenta {cuenta} (posición t1 de portafolio.tenencia_live)")

    filas = _q(
        "SELECT unidad, cantidad, precio, valuacion, cartera "
        "FROM portafolio.tenencia_live "
        "WHERE id_cuenta = %(idc)s AND horizonte = 't1' AND aum = 'si' "
        "  AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live) "
        "ORDER BY unidad",
        {"idc": cuenta},
    )
    if not filas:
        print(f"  (sin filas en tenencia_live para {cuenta} — ¿corrió el daemon hoy?)")
        return

    print(f"  {len(filas)} posiciones\n")
    hdr = (f"  {'TIER':<15} {'UNIDAD':<38} {'INSTRUMENTO':<26} "
           f"{'LAST':>12} {'CLOSING':>12} {'EDAD_min':>9} {'CIERRE':>12} {'CIERRE_F':<11}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    conteo: dict[str, int] = {}
    for f in filas:
        tier, ev = _tier(f["unidad"], instr_map, snap_map, cierre_map)
        conteo[tier] = conteo.get(tier, 0) + 1
        marca = ""
        if ticker_focus and ticker_focus.upper() in (f["unidad"] or "").upper():
            marca = "  ◄◄◄"
        print(f"  {tier:<15} {(f['unidad'] or '')[:38]:<38} {(ev.get('instrumento') or '')[:26]:<26} "
              f"{_fmt(ev.get('last_price')):>12} {_fmt(ev.get('closing_price')):>12} "
              f"{_fmt(ev.get('edad_min'), 1):>9} {_fmt(ev.get('cierre_price')):>12} "
              f"{ev.get('cierre_fecha') or '—'!s:<11}{marca}")

    print("\n  RESUMEN DE TIERS EN ESTA CUENTA:")
    for t, n in sorted(conteo.items(), key=lambda x: -x[1]):
        etiqueta = " (el front lo muestra como LIVE)" if t.startswith("live_") else ""
        print(f"    {t:<15} {n:>4}{etiqueta}")
    if conteo.get("live_closing"):
        print("\n  ⚠️  Hay posiciones en `live_closing`: el precio es el CIERRE que manda "
              "pyRofex\n      (CLOSING_PRICE = cierre del día hábil anterior) pero el motor "
              "las etiqueta LIVE.")


# ── 2. cobertura global + frescura ───────────────────────────────────────────


def seccion_cobertura(instr_map: dict, snap_map: dict, cierre_map: dict) -> None:
    _sep("2) COBERTURA GLOBAL — ¿qué parte de la posición se puede pricear LIVE?")

    unidades = _q(
        "SELECT DISTINCT unidad FROM portafolio.tenencia_live "
        "WHERE horizonte = 't1' AND aum = 'si' "
        "  AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live)"
    )
    if not unidades:
        print("  (tenencia_live vacía)")
        return

    conteo: dict[str, int] = {}
    for u in unidades:
        tier, _ = _tier(u["unidad"], instr_map, snap_map, cierre_map)
        conteo[tier] = conteo.get(tier, 0) + 1
    total = len(unidades)
    print(f"  Unidades DISTINTAS en la posición del día: {total}\n")
    for t, n in sorted(conteo.items(), key=lambda x: -x[1]):
        print(f"    {t:<15} {n:>5}   {n / total * 100:5.1f}%")

    # Contar unidades es engañoso: 500 unidades chicas pesan menos que 5 grandes.
    # Lo que decide es la PLATA. Se agrupa por moneda para no sumar peras con manzanas.
    _sep("2c) LO MISMO PERO PONDERADO POR PLATA (Σ valuación, por moneda)")
    filas = _q(
        "SELECT unidad, coalesce(moneda,'?') AS moneda, sum(valuacion) AS v "
        "FROM portafolio.tenencia_live "
        "WHERE horizonte = 't1' AND aum = 'si' "
        "  AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live) "
        "GROUP BY 1, 2"
    )
    por_moneda: dict[str, dict[str, float]] = {}
    for f in filas:
        tier, _ = _tier(f["unidad"], instr_map, snap_map, cierre_map)
        m = f["moneda"]
        por_moneda.setdefault(m, {})
        por_moneda[m][tier] = por_moneda[m].get(tier, 0.0) + abs(_f(f["v"]) or 0.0)
    for m, tiers in sorted(por_moneda.items()):
        tot = sum(tiers.values()) or 1.0
        print(f"\n  MONEDA {m}   (Σ|valuación| = {tot:,.0f})")
        for t, v in sorted(tiers.items(), key=lambda x: -x[1]):
            print(f"    {t:<15} {v:>18,.0f}   {v / tot * 100:5.1f}%")

    _sep("2d) LAS 25 UNIDADES SIN INSTRUMENTO MÁS GRANDES (precio = el de Aunesa, T-1)")
    print("  Cash (ARS/USD/USDC/etc.) es NORMAL acá: no tiene precio de mercado.\n"
          "  Lo que importa son los TÍTULOS: cada uno se está valuando con el precio\n"
          "  del backfill diario en vez del live.\n")
    sin_instr = [(f["unidad"], f["moneda"], abs(_f(f["v"]) or 0.0)) for f in filas
                 if _tier(f["unidad"], instr_map, snap_map, cierre_map)[0] == "sin_instrumento"]
    sin_instr.sort(key=lambda x: -x[2])
    print(f"  {'UNIDAD':<52} {'MONEDA':<8} {'Σ|VALUACIÓN|':>20}")
    print("  " + "-" * 82)
    for u, m, v in sin_instr[:25]:
        print(f"  {u[:52]:<52} {m:<8} {v:>20,.0f}")


def seccion_ticker(ticker: str, instr_map: dict, snap_map: dict, cierre_map: dict) -> None:
    """Busca un ticker en TODA la posición del día, sin importar la cuenta."""
    _sep(f"2e) RASTREO DE '{ticker}' EN TODA LA POSICIÓN DEL DÍA")
    filas = _q(
        "SELECT DISTINCT unidad FROM portafolio.tenencia_live "
        "WHERE horizonte = 't1' AND aum = 'si' "
        "  AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live) "
        "  AND upper(unidad) LIKE %(p)s",
        {"p": f"%{ticker.upper()}%"},
    )
    if not filas:
        print(f"  (ninguna unidad de la posición del día contiene '{ticker}')")
        return
    for f in filas:
        tier, ev = _tier(f["unidad"], instr_map, snap_map, cierre_map)
        print(f"\n  {f['unidad']}")
        print(f"    tier real      : {tier}"
              f"{'   ← el front lo muestra como LIVE' if tier.startswith('live_') else ''}")
        print(f"    instrumento    : {ev.get('instrumento') or '(SIN MAPEO en portafolio.assets)'}")
        print(f"    last_price     : {_fmt(ev.get('last_price'))}")
        print(f"    closing_price  : {_fmt(ev.get('closing_price'))}")
        print(f"    updated_at     : {ev.get('updated_at') or '—'}   "
              f"(hace {_fmt(ev.get('edad_min'), 1)} min)")
        print(f"    cierre persist.: {_fmt(ev.get('cierre_price'))}  "
              f"fecha {ev.get('cierre_fecha') or '—'}")


def seccion_frescura(instr_map: dict, snap_map: dict, cierre_map: dict) -> None:
    _sep("2b) FRESCURA DEL MOTOR DE PRECIOS (valuaciones.portfolio_snapshot)")
    r = _q("SELECT count(*) AS n, count(updated_at) AS con_ts, max(updated_at) AS ult "
           "FROM valuaciones.portfolio_snapshot")[0]
    print(f"  Tickers en la tabla       : {r['n']}")
    print(f"  Con updated_at            : {r['con_ts']}")
    print(f"  Último update             : {r['ult']}  "
          f"(hace {_fmt(_edad_min(r['ult']), 1)} min)")

    tramos = _q("""
        SELECT CASE
                 WHEN updated_at IS NULL                       THEN '9. sin updated_at'
                 WHEN updated_at > now() - interval '2 min'    THEN '1. < 2 min'
                 WHEN updated_at > now() - interval '15 min'   THEN '2. 2-15 min'
                 WHEN updated_at > now() - interval '60 min'   THEN '3. 15-60 min'
                 WHEN updated_at > now() - interval '1 day'    THEN '4. 1-24 h'
                 ELSE '5. > 24 h'
               END AS tramo, count(*) AS n
        FROM valuaciones.portfolio_snapshot GROUP BY 1 ORDER BY 1
    """)
    print("\n  Antigüedad de cada precio:")
    for t in tramos:
        print(f"    {t['tramo']:<20} {t['n']:>5}")
    print("\n  Nota: la tabla es UPSERT por ticker y NO se limpia. Un ticker que dejó de\n"
          "  operar conserva su último `last_price` para siempre — sin mirar `updated_at`\n"
          "  no se distingue de un precio de hace 5 segundos.")

    # Lo anterior mira la TABLA ENTERA (incluye tickers que ya nadie tiene). Lo que
    # de verdad importa es la antigüedad de los precios que HOY se están sirviendo.
    _sep("2f) ANTIGÜEDAD DE LOS PRECIOS QUE SE ESTÁN SIRVIENDO COMO 'LIVE'")
    unidades = _q(
        "SELECT DISTINCT unidad FROM portafolio.tenencia_live "
        "WHERE horizonte = 't1' AND aum = 'si' "
        "  AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live)"
    )
    bandas = {"< 2 min": 0, "2-15 min": 0, "15-60 min": 0, "1-24 h": 0,
              "> 24 h": 0, "sin updated_at": 0}
    viejos: list[tuple[str, str, float | None]] = []
    for u in unidades:
        tier, ev = _tier(u["unidad"], instr_map, snap_map, cierre_map)
        if not tier.startswith("live_"):
            continue
        e = ev.get("edad_min")
        if e is None:
            bandas["sin updated_at"] += 1
        elif e < 2:
            bandas["< 2 min"] += 1
        elif e < 15:
            bandas["2-15 min"] += 1
        elif e < 60:
            bandas["15-60 min"] += 1
        elif e < 1440:
            bandas["1-24 h"] += 1
        else:
            bandas["> 24 h"] += 1
        if e is None or e >= 60:
            viejos.append((u["unidad"], ev.get("instrumento") or "", e))

    tot = sum(bandas.values()) or 1
    for k, v in bandas.items():
        print(f"    {k:<18} {v:>5}   {v / tot * 100:5.1f}%")
    print(f"\n  Total de posiciones servidas como LIVE: {tot}")

    if viejos:
        viejos.sort(key=lambda x: (-(x[2] or 1e9)))
        print(f"\n  Las {min(len(viejos), 25)} más viejas (≥ 1 h o sin timestamp) — "
              "ESTAS son las que dicen LIVE y no lo son:")
        print(f"  {'UNIDAD':<44} {'INSTRUMENTO':<30} {'ANTIGÜEDAD':>14}")
        print("  " + "-" * 90)
        for u, i, e in viejos[:25]:
            edad = "sin timestamp" if e is None else (
                f"{e / 60:,.1f} h" if e < 1440 else f"{e / 1440:,.1f} días")
            print(f"  {u[:44]:<44} {i[:30]:<30} {edad:>14}")


# ── 3. costo ─────────────────────────────────────────────────────────────────


def seccion_costo(cuenta: str) -> None:
    _sep("3) COSTO REAL DE RECALCULAR — cronómetro con cache FRÍO")

    from api.cache import invalidate
    from api.services import pnl_sql

    # Vaciar los @cached para medir el PEOR caso (primer request tras el TTL).
    invalidate("_mapas_assets", "_pricing_live", "_pricing_cierre")

    def cron(label: str, fn):
        t0 = time.perf_counter()
        out = fn()
        ms = (time.perf_counter() - t0) * 1000
        print(f"    {label:<34} {ms:>8.1f} ms")
        return out, ms

    print("  Etapas del loader (`pnl_sql._deps_sql`), cache frío:\n")
    _, t_mapas = cron("mapas de assets (unidad↔ticker)", pnl_sql._mapas_assets)
    snap, t_live = cron("pricing LIVE (portfolio_snapshot)", pnl_sql._pricing_live)
    cie, t_cie = cron("pricing CIERRE (snapshots_cierre)", pnl_sql._pricing_cierre)
    bol, t_bol = cron("boletos de la cuenta (cost-basis)",
                      lambda: pnl_sql._boletos_by_cuenta(cuenta))
    _, t_pos = cron("posición del día (tenencia_live)",
                    lambda: _q("SELECT id_cuenta, unidad, cantidad, precio, valuacion, "
                               "tipo_titulo, cartera FROM portafolio.tenencia_live "
                               "WHERE id_cuenta = %(idc)s AND horizonte = 't1' AND aum='si' "
                               "AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live)",
                               {"idc": cuenta}))

    n_bol = sum(len(v) for v in (bol or {}).values())
    print(f"\n    (precios live cargados: {len(snap)} tickers · cierres: {len(cie)} · "
          f"boletos de la cuenta: {n_bol})")

    invalidate("_mapas_assets", "_pricing_live", "_pricing_cierre")
    t0 = time.perf_counter()
    res = pnl_sql.pnl_por_cuenta_sql(cuenta, base="live_t1")
    t_total_frio = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    pnl_sql.pnl_por_cuenta_sql(cuenta, base="live_t1")
    t_total_caliente = (time.perf_counter() - t0) * 1000

    n_pos = len(res.get("rows") or [])
    print(f"\n  PnL COMPLETO de la cuenta {cuenta} ({n_pos} posiciones):")
    print(f"    1er request (cache frío)           {t_total_frio:>8.1f} ms")
    print(f"    2do request (cache caliente)       {t_total_caliente:>8.1f} ms")
    print(f"    → lo que ahorra el cache            "
          f"{t_total_frio - t_total_caliente:>8.1f} ms")
    print(f"\n  Reparto del costo frío: pricing = {t_live + t_cie:.0f} ms · "
          f"cost-basis (boletos) = {t_bol:.0f} ms · mapas = {t_mapas:.0f} ms · "
          f"posición = {t_pos:.0f} ms")
    print("  El pricing es lo BARATO y lo que cambia todo el tiempo; el cost-basis es lo\n"
          "  CARO y casi no cambia (solo cuando entra un boleto nuevo).")

    _sep("3b) LATENCIA MEDIDA EN PRODUCCIÓN (manager.latencia_endpoints, últimas 72h)")
    lat = _q("""
        SELECT endpoint, sum(n) AS n, sum(total_ms)/nullif(sum(n),0) AS avg_ms,
               max(max_ms) AS max_ms, sum(lentas) AS lentas, sum(errores) AS errores
        FROM manager.latencia_endpoints
        WHERE hora > now() - interval '72 hours'
          AND (endpoint LIKE '%%posiciones-actuales%%'
            OR endpoint LIKE '%%carteras/pnl%%'
            OR endpoint LIKE '%%valuaciones%%')
        GROUP BY endpoint ORDER BY sum(n) DESC LIMIT 15
    """)
    if not lat:
        print("  (sin telemetría para estos endpoints en 72h)")
        return
    print(f"  {'ENDPOINT':<58} {'N':>7} {'AVG ms':>9} {'MAX ms':>9} {'>1s':>6} {'ERR':>5}")
    print("  " + "-" * 98)
    for r in lat:
        print(f"  {r['endpoint'][:58]:<58} {r['n']:>7} {_fmt(_f(r['avg_ms']), 1):>9} "
              f"{r['max_ms']:>9} {r['lentas']:>6} {r['errores']:>5}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cuenta", default="805", help="id_cuenta a auditar (default 805)")
    ap.add_argument("--ticker", default="MSFT", help="ticker a marcar en el detalle")
    args = ap.parse_args()

    print(f"DIAG PRECIO CARTERAS — {datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC")

    instr_map = _instrumentos_by_unidad()
    snap_map = _snap_by_ticker()
    cierre_map = _cierre_by_ticker()

    seccion_cuenta(args.cuenta, args.ticker, instr_map, snap_map, cierre_map)
    seccion_cobertura(instr_map, snap_map, cierre_map)
    seccion_ticker(args.ticker, instr_map, snap_map, cierre_map)
    seccion_frescura(instr_map, snap_map, cierre_map)
    seccion_costo(args.cuenta)

    _sep("FIN")


if __name__ == "__main__":
    main()

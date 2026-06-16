"""api/services/pnl_sql.py — PnL Títulos leyendo de Postgres, REUSANDO el motor de pnl.py.

El motor de cost-basis (`_pnl_por_cuenta_core`) es lógica pura sobre dicts inyectados (boletos,
posiciones, maps). Acá construimos esos dicts desde SQL y se los pasamos → CERO re-copia del
motor (misma matemática que Mongo). Solo cambia de dónde salen los datos.

Fuentes SQL: negocio_movimientos (boletos), aum (posición), assets (unidad↔ticker + instrumento),
portfolio_snapshot / snapshots_cierre (pricing). Todo numérico → float (el motor mezcla con float;
Decimal rompería). fecha → ISO string (el motor compara fechas como string, igual que Mongo).

Dual-run: el router elige pnl.pnl_por_cuenta (Mongo) vs pnl_sql.pnl_por_cuenta_sql (SQL) por flag.
"""
from __future__ import annotations

from datetime import date

from psycopg.rows import dict_row

from api.db import get_db_cashflow, get_db_trading, get_db_valuaciones
from api.services._mep import get_mep_for_date
from api.services.pnl import (
    _CATS_RELEVANTES,
    _PLACEHOLDERS_INSTRUMENTO,
    _pnl_por_cuenta_core,
    _ticker_corto_fallback,
)
from core.postgres import get_pool

_PLACEHOLDERS = {"", "NO APLICA"}


def _q(sql: str, params=None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _f(x):
    return float(x) if x is not None else None


def _iso(d):
    return d.isoformat() if hasattr(d, "isoformat") else (str(d)[:10] if d else None)


def _build_unidad_maps_sql() -> tuple[dict[str, str], dict[str, str]]:
    """= _build_unidad_maps pero desde la tabla assets. unidad→match_key (CAFCI|TICKER|fallback)
    y match_key→display."""
    unidad_to_match: dict[str, str] = {}
    match_to_display: dict[str, str] = {}
    for d in _q("SELECT unidad, ticker, cafci FROM portafolio.assets"):
        unidad = d.get("unidad")
        if not unidad:
            continue
        cafci = (d.get("cafci") or "").strip()
        ticker = (d.get("ticker") or "").strip()
        ticker_clean = ticker if ticker and ticker not in _PLACEHOLDERS else None
        cafci_clean = cafci if cafci and cafci not in _PLACEHOLDERS else None
        if cafci_clean:
            match_key = cafci_clean
        elif ticker_clean:
            match_key = ticker_clean
        else:
            match_key = _ticker_corto_fallback(unidad)
        unidad_to_match[unidad] = match_key
        match_to_display.setdefault(match_key, ticker_clean or match_key)
    return unidad_to_match, match_to_display


def _deps_sql(only_cuenta: str | None) -> dict:
    """Arma las deps del motor desde SQL (= _load_pnl_bulk_deps pero en Postgres)."""
    unidad_to_match, match_to_display = _build_unidad_maps_sql()

    instrumentos_by_unidad = {}
    for d in _q("SELECT unidad, instrumento FROM portafolio.assets WHERE instrumento IS NOT NULL"):
        instr = (d.get("instrumento") or "").strip()
        if d.get("unidad") and instr and instr not in _PLACEHOLDERS_INSTRUMENTO:
            instrumentos_by_unidad[d["unidad"]] = instr

    portfolio_snap_by_ticker = {
        d["ticker"]: {"last_price": _f(d["last_price"]), "closing_price": _f(d["closing_price"])}
        for d in _q("SELECT ticker, last_price, closing_price FROM portfolio_snapshot")
    }
    snapshots_cierre_by_ticker = {
        d["ticker"]: {"last_price": _f(d["last_price"]), "fecha": _iso(d["fecha"])}
        for d in _q("SELECT ticker, last_price, fecha FROM snapshots_cierre")
    }

    # Boletos por cuenta (scopeado si only_cuenta).
    where = "categoria = ANY(%(cats)s) AND ticker IS NOT NULL"
    p: dict = {"cats": list(_CATS_RELEVANTES)}
    if only_cuenta is not None:
        where += " AND id_cuenta = %(idc)s"
        p["idc"] = only_cuenta
    boletos_by_id_cuenta: dict[str, list] = {}
    for b in _q(f"SELECT id_cuenta, fecha, categoria, op, ticker, cantidad, precio, importe, "
                f"moneda, comprobante, mep FROM negocio_movimientos WHERE {where} "
                f"ORDER BY fecha, comprobante", p):
        cid = str(b.get("id_cuenta") or "").strip()
        if not cid:
            continue
        boletos_by_id_cuenta.setdefault(cid, []).append({
            "fecha": _iso(b["fecha"]), "categoria": b["categoria"], "op": b["op"],
            "ticker": b["ticker"], "cantidad": _f(b["cantidad"]), "precio": _f(b["precio"]),
            "importe": _f(b["importe"]), "moneda": b["moneda"], "comprobante": b["comprobante"],
            "mep": _f(b["mep"]),
        })

    # AuM último snapshot global (posición).
    fecha_actual_aum_global = None
    aum_rows_by_id_cuenta: dict[str, list] = {}
    snap = _q("SELECT max(fecha) AS f FROM portafolio.tenencia WHERE aum = 'si'")[0]["f"]
    if snap is not None:
        fecha_actual_aum_global = _iso(snap)
        wa = "fecha = %(f)s AND aum = 'si'"
        pa: dict = {"f": snap}
        if only_cuenta is not None:
            wa += " AND id_cuenta = %(idc)s"
            pa["idc"] = only_cuenta
        for d in _q(f"SELECT id_cuenta, unidad, cantidad, precio, valuacion, tipo_titulo, cartera "
                    f"FROM portafolio.tenencia WHERE {wa}", pa):
            cid = d.get("id_cuenta")
            if cid is not None:
                aum_rows_by_id_cuenta.setdefault(str(cid), []).append({
                    "id_cuenta": cid, "unidad": d["unidad"], "cantidad": _f(d["cantidad"]),
                    "precio": _f(d["precio"]), "valuacion": _f(d["valuacion"]),
                    "tipoTitulo": d["tipo_titulo"], "cartera": d["cartera"],
                })

    return {
        "unidad_to_match": unidad_to_match, "match_to_display": match_to_display,
        "instrumentos_by_unidad": instrumentos_by_unidad,
        "portfolio_snap_by_ticker": portfolio_snap_by_ticker,
        "snapshots_cierre_by_ticker": snapshots_cierre_by_ticker,
        "boletos_by_id_cuenta": boletos_by_id_cuenta,
        "aum_rows_by_id_cuenta": aum_rows_by_id_cuenta,
        "fecha_actual_aum_global": fecha_actual_aum_global,
    }


def pnl_por_cuenta_sql(id_cuenta: str) -> dict:
    """PnL por (cuenta, ticker) reusando el motor, con datos de SQL."""
    deps = _deps_sql(only_cuenta=str(id_cuenta))
    return _pnl_por_cuenta_core(
        id_cuenta=str(id_cuenta),
        db_cf=get_db_cashflow(), db_v=get_db_valuaciones(), db_t=get_db_trading(),
        mep_hoy=get_mep_for_date(date.today().isoformat()),
        mep_cache={},
        **deps,
    )


def pnl_todas_cuentas_compute_sql() -> list[dict]:
    """= pnl.pnl_todas_cuentas_compute pero con TODAS las deps de SQL (boletos +
    posición + cuentas). Lo corre el cron `jobs.pnl_totales_precompute` → persiste
    en Valuaciones.PnLTotalesCache. NegocioMovimientos ya vive en SQL, así que el
    cron no toca Mongo para los boletos.

    Pre-carga bulk de boletos + posición (2 queries grandes) y reusa el motor por
    cuenta — los boletos viajan por kwarg → 0 query por cuenta."""
    from api.services import portfolio_sql

    cuentas = portfolio_sql.listar_cuentas()
    if not cuentas:
        return []

    deps = _deps_sql(only_cuenta=None)
    # Guard anti-N+1 / anti-colección-vacía: sin boletos NI posición abortamos
    # para no persistir un cache vacío que pisaría el bueno.
    if not deps.get("boletos_by_id_cuenta") and not deps.get("aum_rows_by_id_cuenta"):
        raise RuntimeError(
            "pnl_todas_cuentas_compute_sql: precarga bulk vacía (boletos y posición) — abortado")

    mep_hoy = get_mep_for_date(date.today().isoformat())
    db_cf, db_v, db_t = get_db_cashflow(), get_db_valuaciones(), get_db_trading()
    mep_cache: dict[str, float | None] = {}

    out: list[dict] = []
    for c in cuentas:
        id_cta = c.get("id_cuenta")
        if not id_cta:
            continue
        try:
            r = _pnl_por_cuenta_core(
                id_cuenta=str(id_cta),
                db_cf=db_cf, db_v=db_v, db_t=db_t,
                mep_hoy=mep_hoy, mep_cache=mep_cache, **deps,
            )
        except Exception:
            continue
        out.append({
            "id_cuenta": id_cta,
            "cuenta":    c.get("cuenta") or "",
            "rows":      r.get("rows", []),
            "totales":   r.get("totales", {}) or {},
        })
    return out

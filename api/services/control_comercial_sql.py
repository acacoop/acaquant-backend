"""api/services/control_comercial_sql.py — vista CONTROL COMERCIAL (jefatura).

3 bloques (ver docs/img_1.png):
  1. Datos totales ALyC — por períodos FIJOS (Día/Semana/Mes/YTD/12M/2025/2024/Total),
     NO depende del filtro Desde/Hasta. Clientes activos + volumen + comisiones + % vs período
     anterior inmediato equivalente.
  2. Datos por operador — depende de Desde/Hasta. Por comercial: clientes activos/inactivos +
     volumen + comisiones + % vs el rango anterior de igual largo.
  3. Objetivos comerciales — Volumen/Comisiones Actual vs Objetivo (cargado por el jefe) +
     % alcanzado. Objetivos guardados por (operador, año, mes) en SQL, editables in-view.

Activo = la cuenta operó ≥1 vez en el período (negocio_movimientos). Inactivo = comitente
Activa del comercial que NO operó en el período. Volumen = Σ pesificado de negocio_movimientos
(cats de volumen). Comisiones = Σ arancel de operaciones (etapa <> 'solicitud').

SQL-native (no toca Mongo). Reusa helpers de comercial_sql (_PESIF, _CATS_VOLUMEN, _factor_usd).
"""
from __future__ import annotations

from datetime import UTC, date

from psycopg.rows import dict_row

from core.postgres import get_pool

# ── Tabla de objetivos (self-create, igual patrón que valuaciones.consolidado) ──
_DDL = """
CREATE SCHEMA IF NOT EXISTS clientes;
CREATE TABLE IF NOT EXISTS clientes.objetivos_comerciales (
    operador_email      text NOT NULL,
    anio                int  NOT NULL,
    mes                 int  NOT NULL CHECK (mes BETWEEN 1 AND 12),
    volumen_objetivo    numeric,
    comisiones_objetivo numeric,
    actualizado_por     text,
    actualizado_at      timestamptz DEFAULT now(),
    PRIMARY KEY (operador_email, anio, mes)
);
"""


def _ensure() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()


def listar_objetivos(*, anio: int, mes_desde: int = 1, mes_hasta: int = 12) -> dict:
    """Objetivos cargados en [anio, mes_desde..mes_hasta] (para el editor + Tabla 3).
    Devuelve filas crudas; el frontend agrega por operador según el período elegido."""
    _ensure()
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT operador_email, anio, mes, volumen_objetivo, comisiones_objetivo, "
            "actualizado_por, actualizado_at FROM clientes.objetivos_comerciales "
            "WHERE anio = %s AND mes BETWEEN %s AND %s ORDER BY operador_email, mes",
            (anio, mes_desde, mes_hasta))
        rows = cur.fetchall()
    for r in rows:
        if r.get("actualizado_at") is not None:
            r["actualizado_at"] = str(r["actualizado_at"])[:19]
        r["volumen_objetivo"] = float(r["volumen_objetivo"]) if r["volumen_objetivo"] is not None else None
        r["comisiones_objetivo"] = float(r["comisiones_objetivo"]) if r["comisiones_objetivo"] is not None else None
    return {"anio": anio, "objetivos": rows}


def set_objetivo(*, operador_email: str, anio: int, mes: int,
                 volumen_objetivo: float | None, comisiones_objetivo: float | None,
                 actor: str = "") -> dict:
    """Upsert del objetivo de un comercial para un (año, mes). Lo edita el jefe in-view."""
    _ensure()
    if not (1 <= int(mes) <= 12):
        return {"ok": False, "error": f"mes inválido: {mes!r}"}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO clientes.objetivos_comerciales "
            "(operador_email, anio, mes, volumen_objetivo, comisiones_objetivo, actualizado_por, actualizado_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (operador_email, anio, mes) DO UPDATE SET "
            "volumen_objetivo = EXCLUDED.volumen_objetivo, "
            "comisiones_objetivo = EXCLUDED.comisiones_objetivo, "
            "actualizado_por = EXCLUDED.actualizado_por, actualizado_at = now()",
            (operador_email, int(anio), int(mes),
             volumen_objetivo, comisiones_objetivo, actor))
        conn.commit()
    return {"ok": True, "operador_email": operador_email, "anio": anio, "mes": mes}


def _hoy_art() -> date:
    """Hoy en ART (UTC-3) sin depender de tz del server. Igual criterio que comercial_sql."""
    from datetime import datetime, timedelta
    return (datetime.now(UTC) - timedelta(hours=3)).date()


# ── Cálculo de las 3 tablas (etapa 2) ────────────────────────────────────────
# Reusa los helpers de comercial_sql (misma definición de volumen/comisiones/conversión).
from datetime import timedelta  # noqa: E402

from api.services.comercial_sql import (  # noqa: E402
    _CATS_VOLUMEN,
    _PESIF,
    _cv,
    _f,
    _factor_usd,
    _q,
)


def _prev_biz(d: date) -> date:
    """Día hábil anterior (salta sábado/domingo)."""
    x = d - timedelta(days=1)
    while x.weekday() >= 5:  # 5=sáb, 6=dom
        x -= timedelta(days=1)
    return x


def _lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _ancla() -> date:
    """Última fecha con operaciones (negocio_movimientos), capeada a hoy. Si no hay, hoy."""
    hoy = _hoy_art()
    r = _q("SELECT max(fecha) AS f FROM negocio_movimientos WHERE fecha <= %(h)s", {"h": hoy})
    return r[0]["f"] if r and r[0]["f"] else hoy


def _agg_total(desde: date, hasta: date, factor: float) -> dict:
    """Mesa completa en [desde, hasta]: clientes activos (operaron ≥1), volumen, comisiones."""
    p = {"d": desde, "h": hasta, "cats": list(_CATS_VOLUMEN)}
    v = _q(f"SELECT COUNT(DISTINCT id_cuenta) AS act, COALESCE(SUM({_PESIF}),0) AS vol "
           f"FROM negocio_movimientos WHERE categoria = ANY(%(cats)s) "
           f"AND unidad IS DISTINCT FROM 'USDL' AND fecha >= %(d)s AND fecha <= %(h)s", p)[0]
    c = _q("SELECT COALESCE(SUM(arancel),0) AS com FROM operaciones "
           "WHERE arancel > 0 AND etapa IS DISTINCT FROM 'solicitud' "
           "AND concertacion >= %(d)s AND concertacion <= %(h)s", {"d": desde, "h": hasta})[0]
    return {"clientes_activos": int(v["act"] or 0),
            "volumen": _cv(_f(v["vol"]), factor),
            "comisiones": _cv(_f(c["com"]), factor)}


def _pct(cur: float, prev: float) -> float | None:
    """% de variación cur vs prev. None si prev es 0 (no hay base de comparación)."""
    return round((cur - prev) / prev * 100, 1) if prev else None


def datos_totales_alyc(*, moneda: str = "ARS") -> dict:
    """Tabla 1: totales de la mesa por períodos FIJOS (no usa Desde/Hasta) + % vs el período
    anterior inmediato equivalente. Ancla = última fecha con operaciones."""
    factor = _factor_usd(moneda)
    a = _ancla()
    # (label, desde, hasta, prev_desde, prev_hasta). prev=None → sin comparación.
    pd = _prev_biz(a)
    lun = _lunes(a)
    m1 = a.replace(day=1)
    y1 = a.replace(month=1, day=1)
    defs: list[tuple] = [
        ("Día",      a,   a,   pd, pd),
        ("Semana",   lun, a,   lun - timedelta(days=7), lun - timedelta(days=1)),
        ("Mes",      m1,  a,   _add_months(m1, -1), m1 - timedelta(days=1)),
        ("YTD",      y1,  a,   y1.replace(year=y1.year - 1), _add_months(a, -12)),
        ("12 Meses", _add_months(a, -12) + timedelta(days=1), a,
                     _add_months(a, -24) + timedelta(days=1), _add_months(a, -12)),
        ("2025", date(2025, 1, 1), date(2025, 12, 31), date(2024, 1, 1), date(2024, 12, 31)),
        ("2024", date(2024, 1, 1), date(2024, 12, 31), date(2023, 1, 1), date(2023, 12, 31)),
        ("Total", date(2000, 1, 1), a, None, None),
    ]
    filas = []
    for label, d, h, pdde, phasta in defs:
        cur = _agg_total(d, h, factor)
        prev = _agg_total(pdde, phasta, factor) if pdde else None
        fila = {"periodo": label, **cur}
        for k in ("clientes_activos", "volumen", "comisiones"):
            fila[f"{k}_pct"] = _pct(cur[k], prev[k]) if prev else None
        filas.append(fila)
    return {"moneda": moneda, "ancla": a.isoformat(), "filas": filas}


def _por_operador(desde: date, hasta: date) -> dict[str, dict]:
    """{operador_email: {activos, volumen(ARS), comisiones(ARS)}} en [desde, hasta]. Agrega por
    cuenta (sin alias, para reusar _PESIF) y mapea a operador en Python."""
    op_de = {r["id_cuenta"]: r["operador_email"] for r in _q(
        "SELECT id_cuenta, operador_email FROM comitentes WHERE estado='Activa' "
        "AND operador_email IS NOT NULL")}
    out: dict[str, dict] = {}
    p = {"d": desde, "h": hasta, "cats": list(_CATS_VOLUMEN)}
    for r in _q(f"SELECT id_cuenta, COALESCE(SUM({_PESIF}),0) AS vol FROM negocio_movimientos "
                f"WHERE categoria = ANY(%(cats)s) AND unidad IS DISTINCT FROM 'USDL' "
                f"AND fecha >= %(d)s AND fecha <= %(h)s GROUP BY id_cuenta", p):
        op = op_de.get(r["id_cuenta"])
        if not op:
            continue
        s = out.setdefault(op, {"activos": 0, "volumen": 0.0, "comisiones": 0.0})
        s["activos"] += 1
        s["volumen"] += _f(r["vol"])
    for r in _q("SELECT id_cuenta, COALESCE(SUM(arancel),0) AS com FROM operaciones "
                "WHERE arancel > 0 AND etapa IS DISTINCT FROM 'solicitud' "
                "AND concertacion >= %(d)s AND concertacion <= %(h)s GROUP BY id_cuenta",
                {"d": desde, "h": hasta}):
        op = op_de.get(r["id_cuenta"])
        if not op:
            continue
        out.setdefault(op, {"activos": 0, "volumen": 0.0, "comisiones": 0.0})["comisiones"] += _f(r["com"])
    return out


def datos_por_operador(*, desde: str, hasta: str, moneda: str = "ARS") -> dict:
    """Tabla 2: por comercial en [desde, hasta]: clientes activos/inactivos + volumen +
    comisiones, cada uno con % vs el rango ANTERIOR de igual largo."""
    factor = _factor_usd(moneda)
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    dias = (d1 - d0).days
    pd1 = d0 - timedelta(days=1)            # rango anterior: termina el día previo a `desde`
    pd0 = pd1 - timedelta(days=dias)        # y arranca `dias` antes → mismo largo
    cur = _por_operador(d0, d1)
    prev = _por_operador(pd0, pd1)
    nombre = {r["email"]: r["nombre"] for r in _q(
        "SELECT c.operador_email AS email, o.nombre AS nombre FROM comitentes c "
        "LEFT JOIN operadores o ON o.email = c.operador_email "
        "WHERE c.estado='Activa' AND c.operador_email IS NOT NULL GROUP BY c.operador_email, o.nombre")}
    total_clientes = {r["operador_email"]: int(r["n"]) for r in _q(
        "SELECT operador_email, COUNT(*) AS n FROM comitentes WHERE estado='Activa' "
        "AND operador_email IS NOT NULL GROUP BY operador_email")}
    filas = []
    for op in sorted(set(cur) | set(total_clientes), key=lambda o: -cur.get(o, {}).get("volumen", 0.0)):
        c = cur.get(op, {"activos": 0, "volumen": 0.0, "comisiones": 0.0})
        pv = prev.get(op, {"activos": 0, "volumen": 0.0, "comisiones": 0.0})
        activos = c["activos"]
        inactivos = max(0, total_clientes.get(op, 0) - activos)
        fila = {
            "operador_email": op, "operador_nombre": nombre.get(op) or op,
            "clientes_activos": activos,
            "clientes_activos_pct": _pct(activos, pv["activos"]),
            "clientes_inactivos": inactivos,
            "volumen": _cv(c["volumen"], factor),
            "volumen_pct": _pct(c["volumen"], pv["volumen"]),
            "comisiones": _cv(c["comisiones"], factor),
            "comisiones_pct": _pct(c["comisiones"], pv["comisiones"]),
        }
        filas.append(fila)
    return {"moneda": moneda, "desde": desde, "hasta": hasta, "filas": filas}


def objetivos_vs_actual(*, desde: str, hasta: str, moneda: str = "ARS") -> dict:
    """Tabla 3: por comercial, Volumen/Comisiones ACTUAL en [desde, hasta] vs OBJETIVO (suma de
    los objetivos mensuales que caen en el rango) + % alcanzado."""
    _ensure()
    factor = _factor_usd(moneda)
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    actual = _por_operador(d0, d1)
    # Meses que toca el rango [d0, d1] → suma de objetivos de esos (anio, mes).
    meses: list[tuple[int, int]] = []
    cur = d0.replace(day=1)
    while cur <= d1:
        meses.append((cur.year, cur.month))
        cur = _add_months(cur, 1)
    obj: dict[str, dict] = {}
    if meses:
        claves = [y * 100 + m for (y, m) in meses]   # 2026-06 → 202606 (evita array de tuplas)
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur2:
            cur2.execute(
                "SELECT operador_email, COALESCE(SUM(volumen_objetivo),0) AS vo, "
                "COALESCE(SUM(comisiones_objetivo),0) AS co FROM clientes.objetivos_comerciales "
                "WHERE (anio * 100 + mes) = ANY(%s) GROUP BY operador_email",
                (claves,))
            for r in cur2.fetchall():
                obj[r["operador_email"]] = {"vo": _f(r["vo"]), "co": _f(r["co"])}
    nombre = {r["operador_email"]: r["nombre"] for r in _q(
        "SELECT c.operador_email, o.nombre AS nombre FROM comitentes c "
        "LEFT JOIN operadores o ON o.email = c.operador_email "
        "WHERE c.estado='Activa' AND c.operador_email IS NOT NULL GROUP BY c.operador_email, o.nombre")}
    filas = []
    for op in sorted(set(actual) | set(obj), key=lambda o: -actual.get(o, {}).get("volumen", 0.0)):
        a = actual.get(op, {"volumen": 0.0, "comisiones": 0.0})
        o = obj.get(op, {"vo": 0.0, "co": 0.0})
        vol_act, com_act = _cv(a["volumen"], factor), _cv(a["comisiones"], factor)
        vol_obj, com_obj = _cv(o["vo"], factor), _cv(o["co"], factor)
        # % alcanzado: promedio simple de avance de volumen y comisiones (los 2 que tienen objetivo).
        avances = [x for x in (
            (vol_act / vol_obj * 100) if vol_obj else None,
            (com_act / com_obj * 100) if com_obj else None,
        ) if x is not None]
        filas.append({
            "operador_email": op, "operador_nombre": nombre.get(op) or op,
            "volumen_actual": vol_act, "volumen_objetivo": vol_obj,
            "comisiones_actual": com_act, "comisiones_objetivo": com_obj,
            "pct_alcanzado": round(sum(avances) / len(avances), 1) if avances else None,
        })
    return {"moneda": moneda, "desde": desde, "hasta": hasta, "filas": filas}

"""api/services/opciones_sql.py — opciones leyendo Postgres (chain + meta + charts).

Espejo SQL-native de `opciones.py` para la vista /derivados → OPCIONES:
  * `get_opciones`         ← mercado.options_snapshot  (grid live)
  * `get_opciones_meta`    ← mercado.options_metadata  (tasa risk-free + vol referencia)
  * `get_historico_opciones`← mercado.options_data     (chart intradía, bucket 15 min)
  * `get_vr_ggal_serie`    ← mercado.options_vr        (spot GGAL local/ADR, 2º eje)
  * `get_griegas_historico`← mercado.options_data_hist (chart de griegas por contrato)
  * `estrategia_historico` ← mercado.options_data      (costo intradía de una estrategia)

Los greeks los calcula el motor vía quant/black_scholes — acá se DEVUELVEN tal cual (no se
recalculan). Política "solo vigente" en `mercado.options_data`/`options_snapshot`: la purga de
series viejas la hacen el motor (_purgar_snapshots_fuera_de_mapa) + jobs/archive_options_data
(ts < hoy ART). El shape de cada función == el path Mongo de `opciones.py`.

SQL-only (decomiso Mongo).
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool

# Proyección del chain (mismo shape que el path Mongo: symbol→instrumento + estos campos).
_OPC_FIELDS = ("bid", "offer", "last", "open", "high", "low", "ev", "spot", "strike",
               "tipo", "vence", "closing_price", "delta", "gamma", "iv", "theta", "vega",
               "updated_at")


def _project(d: dict) -> dict:
    out = {"instrumento": d.get("symbol")}
    for f in _OPC_FIELDS:
        if f in d:
            out[f] = d[f]
    return out


@cached(ttl=60)
def get_opciones(instrumento: str | None = None, tipo: str | None = None) -> list:
    """Chain de opciones desde SQL (mercado.options_snapshot). SIN filtro de "solo hoy":
    la tabla ya contiene SOLO la chain VIGENTE (el motor purga los strikes/vencimientos
    fuera de mapa), así que mostrarla siempre da el comportamiento correcto — live con el
    mercado abierto, ÚLTIMO CIERRE con el mercado cerrado (antes filtraba updated_at>=hoy
    y la vista quedaba vacía fuera de rueda; el `updated_at` de cada fila indica frescura)."""
    where: list[str] = []
    params: list = []
    if instrumento:
        where.append("symbol ILIKE %s")
        params.append(f"%{instrumento}%")
    if tipo:
        where.append("tipo = %s")
        params.append(tipo.upper())
    sql = "SELECT data FROM mercado.options_snapshot"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, tuple(params))
        return [_project(r["data"]) for r in cur.fetchall()]


@cached(ttl=60)
def get_opciones_meta() -> dict:
    """Metadata opciones (mercado.options_metadata): tasa risk-free + VR ADR/local.
    Mismo shape que el path Mongo. 2 filas (type='config'/'vr_ggal')."""
    cfg: dict = {}
    vr: dict = {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT type, data FROM mercado.options_metadata "
                    "WHERE type IN ('config', 'vr_ggal')")
        for r in cur.fetchall():
            if r["type"] == "config":
                cfg = r["data"] or {}
            elif r["type"] == "vr_ggal":
                vr = r["data"] or {}
    return {
        "tasa": float(cfg.get("tasa") or 0.0),
        "vr_local": float(vr.get("vr_local") or 0.0),
        "vr_adr": float(vr.get("vr_adr") or 0.0),
        "updated_at": vr.get("updated_at"),
    }


# Campos del tick que el chart intradía consume (sale del jsonb `data`).
_HIST_FIELDS = ("last_timestamp", "bid", "offer", "last", "spot", "strike",
                "tipo", "iv", "delta", "gamma", "vega", "theta")

# Hora (naive, misma convención que `options_data.ts`) a la que se ubica el punto de
# CIERRE diario que sale de `options_data_hist`: 17:00 = fin de rueda BYMA.
_HORA_CIERRE = time(17, 0)
# Cuántos días para atrás mira el costo histórico de una ESTRATEGIA. El de un contrato
# no tiene tope: es la vida del contrato (1 fila/día), igual que sus griegas.
_DIAS_HIST_ESTRATEGIA = 21


def _opero_ese_dia(d: dict) -> bool:
    """Un cierre diario vale si el contrato OPERÓ ese día. `ev` (efectivo del día) es la
    señal; si el rollup no lo trae (filas viejas), alcanza con last > 0."""
    ev = d.get("ev")
    if ev is not None:
        return float(ev) > 0
    return float(d.get("last") or 0) > 0


def filas_diarias_contrato(
    hist: list[tuple[str, str, dict]], dias_excluidos: set[date],
) -> list[dict]:
    """Convierte filas de `options_data_hist` (fecha 'YYYY-MM-DD', symbol, data) en filas
    con el MISMO shape que `get_historico_opciones` (un "tick" a las 17:00 del día), salteando
    los días que ya tienen ticks intradía y los días en que el contrato no operó.
    Pura: la testea tests/unit/test_opciones_hist_diario.py."""
    out: list[dict] = []
    for fecha_str, symbol, data in hist:
        d = data or {}
        fecha = date.fromisoformat(fecha_str)
        if fecha in dias_excluidos or not _opero_ese_dia(d):
            continue
        row = {"instrumento": symbol, "timestamp": datetime.combine(fecha, _HORA_CIERRE)}
        row.update({f: d.get(f) for f in _HIST_FIELDS})
        out.append(row)
    return out


def buckets_diarios_estrategia(
    hist: list[tuple[str, str, dict]], dias_excluidos: set[date],
) -> dict[datetime, list[dict]]:
    """Arma buckets {17:00 del día: [docs de la chain de ese día]} desde `options_data_hist`,
    con el shape que consume `opciones.estrategia_desde_buckets`. El rollup no guarda bid/offer:
    se ponen IGUALES a `last`, que es exactamente lo que ese pricing hace como fallback
    (buy→offer, sell→bid, si no last) y lo que hace contar al strike como líquido. Solo entran
    los contratos que operaron ese día. Pura: testeada junto con `filas_diarias_contrato`."""
    buckets: dict[datetime, list[dict]] = {}
    for fecha_str, symbol, data in hist:
        d = data or {}
        fecha = date.fromisoformat(fecha_str)
        if fecha in dias_excluidos or not _opero_ese_dia(d):
            continue
        last = d.get("last")
        buckets.setdefault(datetime.combine(fecha, _HORA_CIERRE), []).append({
            "symbol": symbol,
            "bid":    last,
            "offer":  last,
            "last":   last,
            "strike": d.get("strike"),
            "tipo":   d.get("tipo"),
            "spot":   d.get("spot"),
        })
    return buckets


def _leer_hist(where_sql: str, params: tuple) -> list[tuple[str, str, dict]]:
    """Filas crudas de `options_data_hist` (fecha, symbol, data), asc por fecha."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT fecha, symbol, data FROM mercado.options_data_hist {where_sql} "
            "ORDER BY fecha, symbol",
            params,
        )
        return [(r["fecha"], r["symbol"], r["data"]) for r in cur.fetchall()]


@cached(ttl=30)
def get_historico_opciones(
    instrumento: str | None = None,
    tipo: str | None = None,
) -> list:
    """Histórico de un contrato: ticks intradía de HOY (mercado.options_data, 1 punto por
    bucket de 15 min = el ÚLTIMO tick de cada franja) + un punto de CIERRE por cada día
    anterior en que operó (mercado.options_data_hist, el rollup diario), a las 17:00.

    Por qué las dos fuentes: `archive_options_data` purga `options_data` todas las noches y
    deja solo la rueda vigente, así que el intradía nunca tiene más de 1-2 días. El
    cierre diario es la misma fila que alimenta el chart de griegas. Un día con ticks
    intradía NO se duplica con su cierre. El cierre diario solo se suma cuando se pide
    un `instrumento` (con solo `tipo` sería toda la chain × toda la historia).

    `instrumento` acepta forma corta o completa (substring ILIKE). El corte de 21 días
    del intradía es nominal (ver arriba).

    Bucket por epoch//900 → `DISTINCT ON (symbol, bucket) ... ORDER BY ts DESC` toma el
    tick más reciente de cada franja (== $last del pipeline Mongo). Salida desc por ts.
    """
    corte = datetime.now() - timedelta(days=21)
    where = ["ts >= %s"]
    params: list = [corte]
    if instrumento:
        where.append("symbol ILIKE %s")
        params.append(f"%{instrumento}%")
    if tipo:
        where.append("(data->>'tipo') = %s")
        params.append(tipo.upper())

    # bucket = floor(epoch/900)*900 → instante de inicio de la franja de 15 min.
    # Proyección server-side de los campos del jsonb (data->'f' preserva el tipo):
    # viaja solo lo que el chart consume, no el doc entero por fila (hasta 20k filas).
    proj = ", ".join(f"data->'{f}' AS \"{f}\"" for f in _HIST_FIELDS)
    sql = (
        "SELECT DISTINCT ON (symbol, floor(extract(epoch FROM ts) / 900)) "
        f"  symbol, ts, {proj} "
        "FROM mercado.options_data "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY symbol, floor(extract(epoch FROM ts) / 900), ts DESC "
        "LIMIT 20000"
    )
    out: list[dict] = []
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, tuple(params))
        for r in cur.fetchall():
            row = {"instrumento": r["symbol"], "timestamp": r["ts"]}
            row.update({f: r[f] for f in _HIST_FIELDS})
            out.append(row)
    if instrumento:
        dias_intradia = {r["timestamp"].date() for r in out}
        hist = _leer_hist("WHERE symbol ILIKE %s", (f"%{instrumento}%",))
        out.extend(filas_diarias_contrato(hist, dias_intradia))
    # Mongo devuelve desc por timestamp (el cliente lo revierte). DISTINCT ON ordena por
    # (symbol, bucket) → re-ordenar en Python para igualar el contrato.
    out.sort(key=lambda x: x["timestamp"], reverse=True)
    return out


@cached(ttl=300)
def get_vr_ggal_serie() -> list:
    """Serie diaria GGAL local (ARS) + ADR (USD) desde mercado.options_vr (~40 ruedas).
    Mismo shape/orden (asc por fecha) que el path Mongo."""
    out: list[dict] = []
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT fecha, data->'LOCAL_Close' AS local, data->'ADR_Close' AS adr "
                    "FROM mercado.options_vr ORDER BY fecha")
        for r in cur.fetchall():
            out.append({
                "fecha": r["fecha"].isoformat() if r["fecha"] else None,
                "local": r["local"],
                "adr":   r["adr"],
            })
    return out


@cached(ttl=120)
def get_griegas_historico(instrumento: str) -> list:
    """Serie diaria de griegas de un contrato (mercado.options_data_hist).
    Una fila por fecha. `instrumento` acepta forma corta o completa (ILIKE). asc por fecha.
    Mismo shape que el path Mongo (`opciones.get_griegas_historico`)."""
    flds = ("delta", "gamma", "vega", "theta", "iv", "last", "spot", "tipo", "strike")
    proj = ", ".join(f"data->'{f}' AS \"{f}\"" for f in flds)
    out: list[dict] = []
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # ILIKE %..% (forma corta = substring) no puede usar el índice b-tree de symbol —
        # aceptado: la tabla es chica (1 fila/contrato/día) y la fn está @cached(120s).
        cur.execute(
            f"SELECT fecha, {proj} FROM mercado.options_data_hist "
            "WHERE symbol ILIKE %s ORDER BY fecha",
            (f"%{instrumento}%",),
        )
        for r in cur.fetchall():
            row = {"fecha": r["fecha"]}
            row.update({f: r[f] for f in flds})
            out.append(row)
    return out


@cached(ttl=60)
def _estrategia_historico_cached(
    legs_key: str,
    legs_json: str,
    bucket_min: int,
    desde_iso: str | None,
    hasta_iso: str | None,
) -> list[dict]:
    from api.services.opciones import estrategia_desde_buckets

    legs = json.loads(legs_json)

    where: list[str] = []
    params: list = []
    if desde_iso:
        where.append("ts >= %s")
        params.append(datetime.fromisoformat(desde_iso.replace("Z", "+00:00")).replace(tzinfo=None))
    if hasta_iso:
        where.append("ts < %s")
        params.append(datetime.fromisoformat(hasta_iso.replace("Z", "+00:00")).replace(tzinfo=None))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    secs = max(int(bucket_min), 1) * 60
    # bucket = floor(epoch/secs)*secs. DISTINCT ON por (symbol, bucket) ORDER BY ts DESC = el
    # ÚLTIMO tick de la franja → bid/offer/last/spot del cierre del bucket (strike/tipo son
    # constantes por símbolo, así que $first==$last). Igual semántica que el $group Mongo.
    sql = (
        f"SELECT DISTINCT ON (symbol, floor(extract(epoch FROM ts) / {secs})) "
        f"  symbol, "
        f"  to_timestamp(floor(extract(epoch FROM ts) / {secs}) * {secs}) AT TIME ZONE 'UTC' AS bucket, "
        f"  data "
        f"FROM mercado.options_data {where_sql} "
        f"ORDER BY symbol, floor(extract(epoch FROM ts) / {secs}), ts DESC"
    )

    buckets: dict[datetime, list[dict]] = {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, tuple(params))
        for r in cur.fetchall():
            d = r["data"] or {}
            buckets.setdefault(r["bucket"], []).append({
                "symbol": r["symbol"],
                "bid":    d.get("bid"),
                "offer":  d.get("offer"),
                "last":   d.get("last"),
                "strike": d.get("strike"),
                "tipo":   d.get("tipo"),
                "spot":   d.get("spot"),
            })

    # Días anteriores: un bucket de CIERRE por día desde el rollup diario (misma razón que
    # en get_historico_opciones: el intradía se purga de noche). La estrategia se
    # reconstruye con la chain de CADA día (ATM y offsets de ese día). Ventana: 21 días,
    # o desde `desde` si vino.
    if desde_iso:
        fecha_desde = params[0].date()
    else:
        # Sin `desde`: 21 días para atrás desde `hasta` (si vino) o desde hoy.
        fin = params[-1].date() if hasta_iso else date.today()
        fecha_desde = fin - timedelta(days=_DIAS_HIST_ESTRATEGIA)
    hist_where = ["fecha >= %s"]
    hist_params: list = [fecha_desde.isoformat()]
    if hasta_iso:
        hist_where.append("fecha < %s")
        hist_params.append(params[-1].date().isoformat())
    dias_intradia = {k.date() for k in buckets}
    hist = _leer_hist("WHERE " + " AND ".join(hist_where), tuple(hist_params))
    buckets.update(buckets_diarios_estrategia(hist, dias_intradia))
    return estrategia_desde_buckets(buckets, legs)


def estrategia_historico(
    legs: list[dict],
    bucket_min: int = 15,
    desde: str | None = None,
    hasta: str | None = None,
) -> list[dict]:
    """Serie intradía de costo de una estrategia (mercado.options_data). Mismo contrato/
    pricing que `opciones.estrategia_historico` — solo cambia la fuente (SQL)."""
    from api.services.opciones import _leg_key

    if bucket_min <= 0:
        bucket_min = 15
    legs_json = json.dumps(legs, sort_keys=True)
    return _estrategia_historico_cached(
        legs_key=_leg_key(legs),
        legs_json=legs_json,
        bucket_min=int(bucket_min),
        desde_iso=desde,
        hasta_iso=hasta,
    )

"""api/services/agro_sql.py — dominio AGRO / Derivados Agro leyendo Postgres.

Espejo SQL-native de `api/services/derivados_agro.py` + `camara_cereales.py` +
`mejoras_dispo.py` (vista /derivados → Agro). Las TABLAS:

- `mercado.agro_snapshot`          (Trading.AgroSnapshot,          motor, SNAPSHOT_SQL)
- `mercado.agro_opciones_snapshot` (Trading.AgroOpcionesSnapshot,  motor, SNAPSHOT_SQL)
- `mercado.agro_pizarra`           (Derivados.AgroPizarra,         carga MANUAL, write_native)
- `mercado.camara_cereales`        (Derivados.CamaraCereales,      carga MANUAL, write_native)

Todas son passthrough: `data jsonb` = el doc Mongo completo. Reconstruir `data`
devuelve el doc original — **excepto** que los datetimes vienen como ISO string
(se escribieron con `pg_mirror.doc_iso`). Como la lógica de cálculo (pase, TNAV,
frescura, simulador) vive en funciones PURAS de `derivados_agro.py` que esperan
`datetime`, acá re-parseamos `updated_at` a datetime y delegamos en esas funciones
→ shape de salida IDÉNTICO al path Mongo, sin duplicar fórmulas.

Dual-run flag `AGRO_SQL` (+ `?_engine=sql|mongo`). Selector en
`api/routers/derivados_agro.py::_agro_svc()`.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from psycopg.rows import dict_row

from api.cache import cached
from api.services import agro_cobertura as _cob
from api.services import camara_cereales as _cam
from api.services import derivados_agro as _agro
from api.services import mejoras_dispo as _mej
from core import curvas_sql
from core.dolar_oficial import mid_oficial_live
from core.postgres import get_pool


# ── reconstrucción de docs desde jsonb ───────────────────────────────────────
def _parse_dt(v: Any) -> Any:
    """ISO string → datetime aware (UTC si naive). Deja pasar lo que ya es datetime/None."""
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return v
    return v


def _hydrate(doc: dict, dt_fields: tuple[str, ...] = ("updated_at",)) -> dict:
    """Re-parsea los campos datetime que el espejo guardó como ISO string."""
    for f in dt_fields:
        if f in doc:
            doc[f] = _parse_dt(doc[f])
    return doc


def _rows(table: str, where: str = "", params: tuple = ()) -> list[dict]:
    """[data, ...] de una tabla agro (passthrough jsonb), datetimes re-hidratados."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT data FROM mercado.{table} {where}", params)
        return [_hydrate(r["data"]) for r in cur.fetchall()]


# ── PASE AGRO ────────────────────────────────────────────────────────────────
@cached(ttl=5)
def get_pase_agro() -> dict[str, Any]:
    """Tabla PASE AGRO desde SQL. Mismo shape que `derivados_agro.get_pase_agro`.

    Reusa los builders puros del módulo Mongo (`_build_bloque`, frescura) — acá
    solo se cambian las 3 lecturas (AgroPizarra/CamaraCereales/AgroSnapshot) por SQL.

    @cached(5s): el shell de /agro pollea y el copiloto la lee DOS veces por
    pregunta (fetch + extras) — con el cache la segunda es gratis y la frescura
    real no cambia (el motor escribe cada ~5s).
    """
    pizarras = {p["commodity"]: p for p in _rows("agro_pizarra")
                if p.get("commodity")}
    # Cámara con la pata derivada YA calculada (SOJA en ARS → USD, resto USD → ARS,
    # con el dólar BNA). _build_bloque usa `precio_usd`, que para SOJA es derivado.
    camara = {c["cereal"]: c for c in _cam.get_camara_cereales()["cereales"]}
    snapshots = _rows("agro_snapshot")

    oficial = mid_oficial_live("oficial")
    oficial_value = oficial.get("value")

    hoy = date.today()
    bloques = [
        _agro._build_bloque(
            commodity,
            pizarras.get(commodity, {}),
            camara.get(commodity, {}),
            snapshots,
            oficial_value,
            hoy,
        )
        for commodity in _agro.COMMODITY_ORDER
    ]

    now = datetime.now(UTC)
    snap_updates = [
        r["updated_at"]
        for b in bloques for r in b["rows"]
        if r.get("tipo") == "futuro" and r.get("updated_at")
    ]
    last_snap = max(snap_updates) if snap_updates else None
    snap_age = _agro._age_s(last_snap, now)
    oficial_age = _agro._age_s(oficial.get("ts"), now)

    return {
        "oficial": {
            "value":  oficial_value,
            "ts":     oficial.get("ts"),
            "source": oficial.get("source"),
            "age_s":  round(oficial_age) if oficial_age is not None else None,
            "stale":  oficial_age is None or oficial_age > _agro.STALE_OFICIAL_S,
        },
        "ts":               now,
        "data_fresh":       snap_age is not None and snap_age <= _agro.STALE_SNAPSHOT_S,
        "last_snapshot_at": last_snap,
        "snapshot_age_s":   round(snap_age) if snap_age is not None else None,
        "bloques":          bloques,
        # Tasas manuales ON / Pagaré (tab DATOS) — alimentan las columnas
        # Pagaré / ON del "Pase con Cobertura".
        "tasas_cobertura":  _cam.get_tasas_cobertura(),
        # Cards + ganancia ON del "Pase con Cobertura" (calculado sobre estos
        # mismos bloques → única fuente de la fórmula).
        "pase_cobertura":   _cob.get_pase_cobertura(bloques),
        # Misma tabla pero con el disponible de la Cámara de Bahía Blanca (el
        # Pase Lleno se recalcula con la pizarra Bahía; el resto es idéntico).
        "pase_cobertura_bahia": _cob.get_pase_cobertura(bloques, plaza="bahia"),
    }


# ── PANEL DE OPCIONES + SIMULADOR ────────────────────────────────────────────
def get_panel_opciones(commodity: str) -> dict[str, Any]:
    """Cadena de opciones agro desde SQL. Mismo shape que el path Mongo.

    Reconstruye la misma estructura por contrato-futuro que `derivados_agro`. La
    lógica de grouping/merge se replica acá (el path Mongo la tiene inline, no en
    un helper reusable), leyendo de las dos tablas SQL filtradas por commodity.
    """
    commodity = _agro._validate_commodity(commodity.upper())

    opciones = _rows("agro_opciones_snapshot", "WHERE commodity = %s", (commodity,))
    futuros = _rows("agro_snapshot", "WHERE commodity = %s", (commodity,))
    futuros_by_ticker = {f.get("ticker"): f for f in futuros if f.get("ticker")}

    by_future: dict[str, list[dict]] = {}
    for o in opciones:
        ticker = o.get("ticker")
        if not ticker:
            continue
        prefix = _agro._futuro_ticker_de_opcion(ticker)
        by_future.setdefault(prefix, []).append(o)

    def _sort_key(prefix: str) -> str:
        opts = by_future[prefix]
        fut = futuros_by_ticker.get(prefix)
        if fut and fut.get("vencimiento"):
            return fut["vencimiento"]
        return opts[0].get("vencimiento") or "99999999"

    vencimientos = []
    for prefix in sorted(by_future.keys(), key=_sort_key):
        opts = by_future[prefix]
        futuro = futuros_by_ticker.get(prefix)
        opt_vto = opts[0].get("vencimiento")
        dias_a_vto = opts[0].get("dias_a_vto")

        by_strike: dict[float, dict[str, dict]] = {}
        for o in opts:
            strike = o.get("strike")
            tipo = o.get("tipo")
            if strike is None or tipo not in ("C", "P"):
                continue
            row = by_strike.setdefault(float(strike), {})
            slot = "call" if tipo == "C" else "put"
            row[slot] = {
                "ticker":     o.get("ticker"),
                "bid":        o.get("bid_price"),
                "offer":      o.get("offer_price"),
                "last":       o.get("last_price"),
                "vol":        o.get("vol_efectivo"),
                "updated_at": o.get("updated_at"),
            }

        strikes = [
            {"strike": k, "call": v.get("call"), "put": v.get("put")}
            for k, v in sorted(by_strike.items())
        ]

        vencimientos.append({
            "vencimiento":   opt_vto,
            "futuro_ticker": prefix,
            "futuro_vto":    futuro.get("vencimiento") if futuro else None,
            "futuro_last":   futuro.get("last_price") if futuro else None,
            "dias_a_vto":    dias_a_vto,
            "strikes":       strikes,
        })

    now = datetime.now(UTC)
    opt_updates = [o.get("updated_at") for o in opciones if o.get("updated_at")]
    last_opt = max(opt_updates) if opt_updates else None
    opt_age = _agro._age_s(last_opt, now)

    return {
        "commodity":        commodity,
        "ts":               now,
        "data_fresh":       opt_age is not None and opt_age <= _agro.STALE_SNAPSHOT_S,
        "last_snapshot_at": last_opt,
        "vencimientos":     vencimientos,
    }


def simular_estrategia(
    commodity: str,
    vencimiento: str,
    tipo: _agro.TipoEstrategia,
    strike: float,
    prima_override: float | None = None,
) -> dict[str, Any]:
    """Simula put sintético / long put desde SQL. Mismo shape y validaciones que el
    path Mongo. Reusa las fórmulas puras (`_curva_estrategia_y_diferencias`, métricas
    de payoff); solo cambian las 2 lecturas (opción + futuro) por SQL."""
    commodity = _agro._validate_commodity(commodity.upper())
    if tipo not in ("put_sintetico", "long_put"):
        raise ValueError(f"tipo inválido: {tipo!r}")
    if strike <= 0:
        raise ValueError("strike debe ser > 0")

    tipo_opcion = "C" if tipo == "put_sintetico" else "P"

    opciones = _rows(
        "agro_opciones_snapshot",
        "WHERE commodity = %s AND data->>'vencimiento' = %s "
        "AND (data->>'strike')::float = %s AND data->>'tipo' = %s",
        (commodity, vencimiento, strike, tipo_opcion),
    )
    opcion = opciones[0] if opciones else None
    if not opcion:
        raise ValueError(f"opción {tipo_opcion} K={strike} vto {vencimiento} no listada")
    future_ticker = _agro._futuro_ticker_de_opcion(opcion.get("ticker") or "")
    if not future_ticker:
        raise ValueError(
            f"no pude extraer ticker del futuro de la opción {opcion.get('ticker')!r}"
        )

    futuros = _rows("agro_snapshot", "WHERE ticker = %s", (future_ticker,))
    futuro = futuros[0] if futuros else None
    if not futuro or futuro.get("last_price") in (None, 0):
        raise ValueError(f"sin precio de futuro {future_ticker} (subyacente de la opción)")
    futuro_F0 = float(futuro["last_price"])

    if prima_override is not None:
        if prima_override <= 0:
            raise ValueError("prima_override debe ser > 0")
        prima = float(prima_override)
    else:
        last = opcion.get("last_price")
        if last in (None, 0):
            raise ValueError(
                "sin last_price para la opción seleccionada — pasar prima_override"
            )
        prima = float(last)

    if tipo == "put_sintetico":
        piso = round(futuro_F0 - prima, 4)
        diferencia_max = round(strike - futuro_F0, 4)
        zona_expuesta = {
            "desde": round(min(futuro_F0, strike), 4),
            "hasta": round(max(futuro_F0, strike), 4),
        }
    else:  # long_put
        piso = round(strike - prima, 4)
        diferencia_max = 0.0
        zona_expuesta = None

    curva_e, curva_d = _agro._curva_estrategia_y_diferencias(
        tipo=tipo, futuro_F0=futuro_F0, strike_K=strike, prima=prima,
    )

    now = datetime.now(UTC)
    fut_age = _agro._age_s(futuro.get("updated_at"), now)
    opt_age = _agro._age_s(opcion.get("updated_at"), now)

    return {
        "tipo":               tipo,
        "commodity":          commodity,
        "vencimiento":        vencimiento,
        "strike":             strike,
        "prima":              prima,
        "prima_override":     prima_override is not None,
        "futuro_ticker":      futuro.get("ticker"),
        "futuro_last":        futuro_F0,
        "futuro_age_s":       round(fut_age) if fut_age is not None else None,
        "opcion_ticker":      opcion.get("ticker"),
        "opcion_age_s":       round(opt_age) if opt_age is not None else None,
        "stale":              (fut_age is None or fut_age > _agro.STALE_SNAPSHOT_S),
        "piso":               piso,
        "diferencia_max":     diferencia_max,
        "zona_expuesta":      zona_expuesta,
        "curva_estrategia":   curva_e,
        "curva_diferencias":  curva_d,
        "ts":                 now,
    }


# ── CÁMARA ARBITRAL DE CEREALES ──────────────────────────────────────────────
def get_camara_cereales() -> dict[str, Any]:
    """Los 5 cereales. Delega en el service canónico, que guarda solo la pata
    manual (SOJA en ARS, resto en USD) y deriva la otra con el dólar BNA."""
    return _cam.get_camara_cereales()


def get_camara_cereales_bahia() -> dict[str, Any]:
    """Los 5 cereales de la Cámara de Bahía Blanca. Delega en el service canónico,
    que guarda solo la pata USD (manual) y deriva la ARS con el dólar BNA."""
    return _cam.get_camara_cereales_bahia()


# ── MEJORAS PRECIO DISPONIBLE ────────────────────────────────────────────────
def _mejoras_dispo(camara_docs: dict[str, dict]) -> dict[str, Any]:
    """Núcleo de Mejoras Dispo: dado el precio disponible por commodity
    (`camara_docs`, ya con `precio_ars` derivado), cruza LECAPs + futuros DLR.
    Rosario y Bahía comparten TODO menos la fuente del precio disponible."""
    today = date.today()
    hoy_str = today.strftime("%Y%m%d")

    spot = mid_oficial_live("oficial").get("value")

    # Futuros DLR vigentes desde mercado.futuros_dlr_snapshot (SQL-native; decomiso Mongo:
    # FuturosDLRSnapshot dropeada). Doc completo en jsonb `data`.
    from psycopg.rows import dict_row
    fut_by_ym: dict[tuple[int, int], dict] = {}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM mercado.futuros_dlr_snapshot WHERE vencimiento > %s",
                    (hoy_str,))
        for r in cur.fetchall():
            f = r["data"]
            if not f:
                continue
            fvto = _mej._parse_yyyymmdd(f.get("vencimiento"))
            if fvto:
                fut_by_ym.setdefault((fvto.year, fvto.month), f)

    # LECAPs desde SQL mercado.curvas; flujo_vencimiento not-null se filtra en Python.
    lecaps = [c for c in curvas_sql.por_curva("tasa_fija")
              if c.get("flujo_vencimiento") is not None]

    lecap_tickers = [c["ticker"] for c in lecaps if c.get("ticker")]
    tea_map: dict[str, float] = {}
    if lecap_tickers:
        from core.market_snapshot import metric_map
        tea_map = metric_map(lecap_tickers, "tea")  # SQL-only (mercado.market_snapshot)

    bloques = []
    for commodity in _mej.COMMODITIES:
        cam = camara_docs.get(commodity) or {}
        precio_ars = cam.get("precio_ars")
        filas = _mej._build_filas(lecaps, tea_map, fut_by_ym, precio_ars, today)
        bloques.append({
            "commodity":  commodity,
            "precio_ars": precio_ars,
            "filas":      filas,
            "precio_updated_at": cam.get("updated_at"),
            "precio_updated_by": cam.get("updated_by"),
        })

    return {"ts": datetime.now(UTC), "spot": spot, "bloques": bloques}


def get_mejoras_dispo() -> dict[str, Any]:
    """Mejoras dispo (ROSARIO) desde SQL. El precio disponible sale de la Cámara de
    Rosario; los futuros DLR / LECAPs / TEA salen de las tablas SQL de mercado.
    Reusa el builder puro `_build_filas`.

    Mismo shape que `mejoras_dispo.get_mejoras_dispo` (no se cachea acá; el router
    aplica el selector — el path Mongo conserva su `@cached`)."""
    # Cámara con pata derivada calculada — MAIZ/TRIGO cargan USD → precio_ars derivado.
    camara_docs = {c["cereal"]: c for c in _cam.get_camara_cereales()["cereales"]}
    return _mejoras_dispo(camara_docs)


def get_mejoras_dispo_bahia() -> dict[str, Any]:
    """Mejoras dispo (BAHÍA) — idéntico a Rosario pero el precio disponible sale de
    la Cámara de Bahía Blanca (todos los cereales en USD → precio_ars derivado)."""
    camara_docs = {c["cereal"]: c for c in _cam.get_camara_cereales_bahia()["cereales"]}
    return _mejoras_dispo(camara_docs)

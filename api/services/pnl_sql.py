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

# Decomiso Mongo: el motor _pnl_por_cuenta_core acepta db_cf/db_v/db_t pero SOLO los
# dereferencia en su rama de fallback (cuando los kwargs bulk vienen None). En el path
# SQL `_deps_sql` provee TODOS los kwargs → esas ramas nunca corren → se pasa None.
from api.cache import cached
from api.services._mep import get_mep_for_date
from api.services._sql import _f, _q
from api.services.pnl import (
    _CATS_RELEVANTES,
    _PLACEHOLDERS_INSTRUMENTO,
    _pnl_por_cuenta_core,
    _ticker_corto_fallback,
)

_PLACEHOLDERS = {"", "NO APLICA"}


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


@cached(ttl=300)
def _mapas_assets() -> dict:
    """Mapas globales derivados del catálogo `portafolio.assets` (casi estático).

    Cacheados 300s: sin esto cada request de PnL re-escaneaba assets 2 veces.
    Los dicts cacheados se COMPARTEN entre requests → solo lectura (el motor
    solo hace .get(), verificado en pnl.py)."""
    unidad_to_match, match_to_display = _build_unidad_maps_sql()
    instrumentos_by_unidad: dict[str, str] = {}
    for d in _q("SELECT unidad, instrumento FROM portafolio.assets WHERE instrumento IS NOT NULL"):
        instr = (d.get("instrumento") or "").strip()
        if d.get("unidad") and instr and instr not in _PLACEHOLDERS_INSTRUMENTO:
            instrumentos_by_unidad[d["unidad"]] = instr
    return {
        "unidad_to_match": unidad_to_match, "match_to_display": match_to_display,
        "instrumentos_by_unidad": instrumentos_by_unidad,
    }


@cached(ttl=5)
def _pricing_live() -> dict:
    """Precios live por ticker (portfolio_snapshot). El motor escribe cada 1s
    durante la rueda → TTL corto (5s, mismo criterio que market_sql._quotes_all):
    colapsa ráfagas de requests sin dejar el PnL stale."""
    return {
        d["ticker"]: {"last_price": _f(d["last_price"]), "closing_price": _f(d["closing_price"])}
        for d in _q("SELECT ticker, last_price, closing_price FROM portfolio_snapshot")
    }


@cached(ttl=60)
def _pricing_cierre() -> dict:
    """Cierres persistidos por ticker (snapshots_cierre, escribe 1×/día el cron)."""
    return {
        d["ticker"]: {"last_price": _f(d["last_price"]), "fecha": _iso(d["fecha"])}
        for d in _q("SELECT ticker, last_price, fecha FROM snapshots_cierre")
    }


# Cuentas por query al cargar los boletos en bulk. El cost-basis necesita TODOS los
# boletos de una cuenta (sin cota de fecha, por diseño) → se batchea POR CUENTA: cada
# query es acotada y NINGUNA crece con el total del sistema (antes era una sola query
# con toda la historia de todas las cuentas → riesgo de cruzar el statement_timeout de
# 15s y dejar `valuaciones.pnl_totales_cache` congelada).
_CUENTAS_POR_LOTE = 100

_SEL_BOLETOS = (
    "SELECT id_cuenta, fecha, categoria, op, ticker, cantidad, precio, importe, "
    "moneda, comprobante, mep FROM negocio_movimientos "
    "WHERE categoria = ANY(%(cats)s) AND ticker IS NOT NULL AND anulado_en IS NULL"
)


def _boletos_by_cuenta(only_cuenta: str | None) -> dict[str, list]:
    """Boletos relevantes agrupados por id_cuenta. Idéntico resultado que una única
    query global: la partición por id_cuenta es exacta y dentro de cada lote se
    mantiene el mismo `ORDER BY fecha, comprobante` → cada cuenta recibe su lista en
    el mismo orden."""
    cats = list(_CATS_RELEVANTES)
    if only_cuenta is not None:
        lotes = [(f"{_SEL_BOLETOS} AND id_cuenta = %(idc)s ORDER BY fecha, comprobante",
                  {"cats": cats, "idc": only_cuenta})]
    else:
        ids = [
            r["id_cuenta"] for r in _q(
                "SELECT DISTINCT id_cuenta FROM negocio_movimientos "
                "WHERE categoria = ANY(%(cats)s) AND ticker IS NOT NULL "
                "AND anulado_en IS NULL",
                {"cats": cats},
            ) if r["id_cuenta"] is not None
        ]
        lotes = [
            (f"{_SEL_BOLETOS} AND id_cuenta = ANY(%(ids)s) ORDER BY fecha, comprobante",
             {"cats": cats, "ids": ids[i:i + _CUENTAS_POR_LOTE]})
            for i in range(0, len(ids), _CUENTAS_POR_LOTE)
        ]

    out: dict[str, list] = {}
    for sql, params in lotes:
        for b in _q(sql, params):
            cid = str(b.get("id_cuenta") or "").strip()
            if not cid:
                continue
            out.setdefault(cid, []).append({
                "fecha": _iso(b["fecha"]), "categoria": b["categoria"], "op": b["op"],
                "ticker": b["ticker"], "cantidad": _f(b["cantidad"]), "precio": _f(b["precio"]),
                "importe": _f(b["importe"]), "moneda": b["moneda"],
                "comprobante": b["comprobante"], "mep": _f(b["mep"]),
            })
    return out


def _deps_sql(only_cuenta: str | None, base: str = "tenencia") -> dict:
    """Arma las deps del motor desde SQL (= _load_pnl_bulk_deps pero en Postgres).

    `base` decide de dónde salen las CANTIDADES de la posición:
      "tenencia" (default) → `portafolio.tenencia`, la foto conciliada de ayer.
      "live_t1"            → `portafolio.tenencia_live` t1, la posición de HOY
                             con lo concertado hoy adentro.

    Es opt-in porque este loader alimenta a TODOS los consumidores del motor —
    la vista VALUACIONES, el asistente de IA y el cron que precalcula
    `valuaciones.pnl_totales_cache`. Solo `/api/carteras/pnl` pide "live_t1";
    el resto sigue viendo exactamente lo mismo que antes.

    Lo único que cambia es la fuente de la posición: precios, normalizador por
    cartera, cost-basis y cadena de fallback (live → cierre → aum) quedan iguales.
    """
    mapas = _mapas_assets()
    unidad_to_match = mapas["unidad_to_match"]
    match_to_display = mapas["match_to_display"]
    instrumentos_by_unidad = mapas["instrumentos_by_unidad"]
    portfolio_snap_by_ticker = _pricing_live()
    snapshots_cierre_by_ticker = _pricing_cierre()

    # Boletos por cuenta (scopeado si only_cuenta; batcheado por cuenta si es global).
    boletos_by_id_cuenta = _boletos_by_cuenta(only_cuenta)

    # Ajustes manuales por eventos corporativos (splits, canjes, pre-data) —
    # `operaciones.pnl_ajustes`, tabla chica (1 query). Se mergean al stream
    # cronológico como pseudo-boletos ANTES de los boletos de su fecha; el motor
    # los procesa con las ramas ajuste_split/ajuste_cantidad. Si la tabla no
    # existe todavía (schema sin aplicar), el PnL sigue sin ajustes — no cae.
    try:
        from api.services.pnl_ajustes_sql import ajustes_activos, merge_ajustes_en_boletos
        boletos_by_id_cuenta = merge_ajustes_en_boletos(
            boletos_by_id_cuenta, ajustes_activos(only_cuenta=only_cuenta))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("pnl_sql: merge de ajustes falló — sigo sin ajustes")

    # AuM último snapshot global (posición).
    #
    # `fecha_actual_aum_global` SIEMPRE es la fecha de la foto CONCILIADA
    # (`portafolio.tenencia`), aunque las filas vengan de `tenencia_live`. El motor
    # la usa para el corte de day-trades (`boleto.fecha > fecha_actual_aum` →
    # `pnl_realizado_dia`), y son dos conceptos distintos que hoy coinciden por
    # casualidad. Si acá se pusiera la fecha de HOY, ningún boleto sería posterior
    # y la columna de PnL del día se iría a CERO en silencio.
    fecha_actual_aum_global = None
    aum_rows_by_id_cuenta: dict[str, list] = {}
    snap = _q("SELECT max(fecha) AS f FROM portafolio.tenencia WHERE aum = 'si'")[0]["f"]
    if snap is not None:
        fecha_actual_aum_global = _iso(snap)
        cols = ("id_cuenta, unidad, cantidad, precio, valuacion, tipo_titulo, cartera")
        rows: list[dict] = []
        if base == "live_t1":
            # Posición del DÍA (t1 = con lo concertado hoy adentro). Mismo filtro
            # `aum='si'` que la foto, así el universo sumado es idéntico y la única
            # variable que cambia es la fecha base.
            wl = ("horizonte = 't1' AND aum = 'si' "
                  "AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live)")
            pl: dict = {}
            if only_cuenta is not None:
                wl += " AND id_cuenta = %(idc)s"
                pl["idc"] = only_cuenta
            try:
                rows = _q(f"SELECT {cols} FROM portafolio.tenencia_live WHERE {wl}", pl)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("pnl_sql: tenencia_live falló")
                rows = []
        if not rows:
            # Default, y fallback de `live_t1`: si el daemon no corrió (fin de
            # semana, caído, tabla sin aplicar) la vista sigue andando con la foto.
            wa = "fecha = %(f)s AND aum = 'si'"
            pa: dict = {"f": snap}
            if only_cuenta is not None:
                wa += " AND id_cuenta = %(idc)s"
                pa["idc"] = only_cuenta
            rows = _q(f"SELECT {cols} FROM portafolio.tenencia WHERE {wa}", pa)
        for d in rows:
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


def pnl_por_cuenta_sql(id_cuenta: str, base: str = "tenencia") -> dict:
    """PnL por (cuenta, ticker) reusando el motor, con datos de SQL.

    `base="live_t1"` toma la posición del día (`portafolio.tenencia_live`) en vez
    de la foto de ayer. Ver `_deps_sql`.
    """
    deps = _deps_sql(only_cuenta=str(id_cuenta), base=base)
    return _pnl_por_cuenta_core(
        id_cuenta=str(id_cuenta),
        mep_hoy=get_mep_for_date(date.today().isoformat()),
        mep_cache={},
        **deps,
    )


def pnl_todas_cuentas_sql(
    filtro_cuenta: str = "todas",
    scope: tuple[str, ...] | None = None,
) -> dict:
    """Espejo SQL de pnl.pnl_todas_cuentas — LECTURA LIVIANA del cache.

    Lee `valuaciones.pnl_totales_cache` (precalculado por el cron
    jobs.pnl_totales_precompute, dual-write Mongo+SQL). Mismo shape de salida que el
    path Mongo: aplana los rows con info de cuenta + suma totales. El filtro de tipo
    de cuenta se aplica EN PYTHON (accionistas/productores ∈ sets SQL) — NO contra
    Valuaciones.AuM (eliminada), igual que valuaciones_sql.valuacion_consolidada.

    Si la tabla no existe / está vacía (falta correr el cron) → rows: []."""
    try:
        cache = _q(
            "SELECT id_cuenta, cuenta, rows, totales FROM valuaciones.pnl_totales_cache")
    except Exception:
        return {"rows": [], "totales": _pnl_totales_vacio(), "filtro_cuenta": filtro_cuenta}

    docs = [{
        "id_cuenta": d["id_cuenta"],
        "cuenta":    d["cuenta"] or "",
        "rows":      d["rows"] or [],
        "totales":   d["totales"] or {},
    } for d in cache]

    # Scope de grupos — subset de cuentas visibles para el usuario.
    if scope is not None:
        permitidas = set(scope)
        docs = [d for d in docs if str(d.get("id_cuenta", "")) in permitidas]

    # Filtro de tipo de cuenta — membership directa (cuenta="[N] NOMBRE" + id_cuenta).
    if filtro_cuenta and filtro_cuenta != "todas":
        from api.services._cuentas_filter import (
            _cuentas_accionistas,
            _ids_cuenta_productores,
        )
        accs = set(_cuentas_accionistas())
        prods = set(_ids_cuenta_productores())

        def _ok(d: dict) -> bool:
            cuenta = d.get("cuenta") or ""
            idc = str(d.get("id_cuenta") or "")
            if filtro_cuenta == "accionistas":
                return cuenta in accs
            if filtro_cuenta == "sin_accionistas":
                return cuenta not in accs
            if filtro_cuenta == "cooperativas":
                return cuenta not in accs and "coop" in cuenta.lower()
            if filtro_cuenta == "productores":
                return idc in prods
            return True

        docs = [d for d in docs if _ok(d)]

    rows: list[dict] = []
    totales = _pnl_totales_vacio()
    for d in docs:
        id_cta = d.get("id_cuenta")
        cta_label = d.get("cuenta") or ""
        for row in d.get("rows", []):
            # TOTALES lista solo posiciones abiertas — qty_aum != 0 (los cerrados
            # intraday ya están sumados en el `totales` por cuenta).
            if float(row.get("qty_aum") or 0) == 0:
                continue
            r2 = dict(row)
            r2["cuenta"] = cta_label
            r2["id_cuenta"] = id_cta
            rows.append(r2)
        t = d.get("totales", {}) or {}
        for k in totales:
            totales[k] += float(t.get(k) or 0)

    def _total_view(row: dict) -> float:
        return (
            float(row.get("pnl_no_realizado") or 0)
            + float(row.get("pnl_pasivo") or 0)
            + float(row.get("pnl_realizado_dia") or 0)
        )
    rows.sort(key=lambda r: -_total_view(r))

    return {
        "rows": rows,
        "totales": {
            "n_cuentas":         len(docs),
            "n_filas":           len(rows),
            "costo_remanente":   round(totales["costo_remanente"], 2),
            "valor_actual":      round(totales["valor_actual"], 2),
            "pnl_no_realizado":  round(totales["pnl_no_realizado"], 2),
            "pnl_pasivo":        round(totales["pnl_pasivo"], 2),
            "pnl_realizado_dia": round(totales["pnl_realizado_dia"], 2),
            "pnl_total":         round(totales["pnl_total"], 2),
            "costo_remanente_usd":   round(totales["costo_remanente_usd"], 2),
            "valor_actual_usd":      round(totales["valor_actual_usd"], 2),
            "pnl_no_realizado_usd":  round(totales["pnl_no_realizado_usd"], 2),
            "pnl_pasivo_usd":        round(totales["pnl_pasivo_usd"], 2),
            "pnl_realizado_dia_usd": round(totales["pnl_realizado_dia_usd"], 2),
            "pnl_total_usd":         round(totales["pnl_total_usd"], 2),
        },
        "filtro_cuenta": filtro_cuenta,
    }


def _pnl_totales_vacio() -> dict:
    return {
        "costo_remanente":   0.0,
        "valor_actual":      0.0,
        "pnl_no_realizado":  0.0,
        "pnl_pasivo":        0.0,
        "pnl_realizado_dia": 0.0,
        "pnl_total":         0.0,
        "costo_remanente_usd":   0.0,
        "valor_actual_usd":      0.0,
        "pnl_no_realizado_usd":  0.0,
        "pnl_pasivo_usd":        0.0,
        "pnl_realizado_dia_usd": 0.0,
        "pnl_total_usd":         0.0,
    }


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
    mep_cache: dict[str, float | None] = {}

    out: list[dict] = []
    fallos = 0
    primer_error: str | None = None
    for c in cuentas:
        id_cta = c.get("id_cuenta")
        if not id_cta:
            continue
        try:
            r = _pnl_por_cuenta_core(
                id_cuenta=str(id_cta),
                mep_hoy=mep_hoy, mep_cache=mep_cache, **deps,
            )
        except Exception as e:
            # Antes se tragaba en silencio → un TypeError de firma quedaba oculto y
            # el job daba "0 cuentas / PARTIAL" sin decir por qué. Ahora lo logueamos
            # y, si NINGUNA cuenta salió, propagamos para que el run figure como error real.
            fallos += 1
            if primer_error is None:
                primer_error = f"{type(e).__name__}: {e}"
            continue
        out.append({
            "id_cuenta": id_cta,
            "cuenta":    c.get("cuenta") or "",
            "rows":      r.get("rows", []),
            "totales":   r.get("totales", {}) or {},
        })

    # Si TODAS fallaron, es un bug (firma incompatible, deps mal armadas, etc.), no
    # "no hay cuentas": propagamos para que el cron lo marque como ERROR con causa,
    # en vez de un PARTIAL "0 cuentas" que no dice nada y no toca el cache.
    if not out and fallos:
        raise RuntimeError(
            f"pnl_todas_cuentas_compute_sql: las {fallos} cuentas fallaron. "
            f"Primer error: {primer_error}")
    return out

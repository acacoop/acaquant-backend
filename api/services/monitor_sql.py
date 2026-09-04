"""monitor_sql.py — la tab **MONITOR** de `/trading`: el precio del tape arriba y
los **nominales operados en cada precio** abajo.

Read-only. No escribe una sola fila.

**Qué contesta que las otras vistas no contestan.** `/trading → PIVOTS` muestra
*dónde está* el precio; esto muestra **dónde se operó la plata**: el precio con
más volumen de la ventana (POC) y la banda donde se hizo el 70 % del negocio
(área de valor). Es el nivel que la mesa defiende, y hasta hoy no estaba en
ninguna pantalla.

## Las tres fuentes, y por qué son tres

No hay UNA tabla que sirva para todo, y elegir mal no falla: dibuja otra cosa
en silencio. Por eso la fuente **se deriva de (clase, ventana)** en un solo
lugar —`_fuente()`— y viaja en la respuesta (`fuente`), para que la pantalla
pueda decir de dónde salió lo que muestra.

| clase | ventana | tabla | precisión |
|---|---|---|---|
| `rv` | HOY | `mercado.cedears_time_sales` | **exacta** — tick a tick |
| `rv` | 5R / 20R | `mercado.cedears_bars_1m` | aproximada — ver abajo |
| `rf` | HOY / 3R / 5R | `mercado.timesales` | **exacta** — tick a tick |

- El tape de CEDEARs **se trunca todas las noches** (`jobs/cleanup_cedears_timesales.py`),
  así que multi-rueda de renta variable NO puede salir de ahí. Sale del ARCHIVO
  de barras de 1 minuto (`jobs/cedears_bars_1m.py`, ventana móvil de 60 ruedas),
  que hasta hoy **no leía nadie**: esta tab es su primer consumidor.
- ⚠️ **La barra de 1 minuto no guarda el precio de cada trade.** Para el perfil
  hay que elegir UN precio por barra y se usa el **típico** `(high+low+close)/3`
  —la convención estándar— con el volumen del minuto entero. Es una
  APROXIMACIÓN del tape: en HOY el mismo papel puede dar un POC levemente
  distinto según la ventana, y eso es correcto, no un bug. La respuesta lo dice
  en `aproximado`.
- `mercado.timesales` (renta fija) guarda ~7 días corridos (`prune_native` en
  `engines/valores.py`, al arrancar el motor) → en la práctica **5-6 ruedas**.
  Su clave NO es el ticker corto sino el **símbolo de mercado**
  (`MERV - XMEV - AL30D - 24hs`), que sale del master por `trading_pivots`.

## Lo que hace que no se ponga lento

Medido en prod el 2026-09-04 (`scripts/diag_monitor_tape.py`):
NVDA a 20 ruedas = **104 ms**; AL30 con 59.097 ticks = **317 ms**.

1. **Un instrumento por vez, por índice.** El `WHERE clave = X AND ts >= Y` entra
   por `(ticker, ts DESC)` / `(ticker_corto, ts DESC)` / `(ticker_corto, minuto DESC)`.
   El universo NO entra en la query.
2. **Todo se agrega SERVER-SIDE y en UNA sola pasada.** La CTE `t` se materializa
   una vez y de ahí salen la serie, el perfil y los totales: un solo scan del
   índice, un solo round-trip. Por el cable viajan ~400 filas, pidas 1 rueda o 20.
3. **La serie se resamplea según la ventana** (1' hoy · 5' a 5 ruedas · 15' a 20).
   20 ruedas a 1 minuto son 7.758 puntos para un chart de ~1.300 px: seis puntos
   por píxel, invisibles y caros.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from api.cache import cached
from api.services import curvas_vista as curvas_svc
from api.services import scanner_sql as scanner_svc
from api.services import trading_pivots as pivots_svc
from core import curvas_ejes as ce
from core import market_snapshot
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_ART = ZoneInfo("America/Argentina/Buenos_Aires")
_TZ_SQL = "America/Argentina/Buenos_Aires"

CLASES = ("rv", "rf")
BUCKETS_DEF = 26
BUCKETS_MIN, BUCKETS_MAX = 8, 60
AREA_DE_VALOR = 0.70

# Un bono sin pill acordada (badlar/tpm/caución, o sin ejes cargados) NO se
# oculta: cae acá. Una lista que se come instrumentos en silencio es peor que
# una con una categoría fea — nadie sale a buscar lo que no sabe que falta.
SIN_CURVA = "otros"
SIN_CURVA_DISPLAY = "OTROS"


@dataclass(frozen=True)
class _Fuente:
    """De dónde sale una ventana. `es_barra` cambia la agregación entera: con
    barras el precio del perfil es el TÍPICO y el volumen ya viene sumado."""
    tabla: str
    col_k: str          # columna clave: ticker_corto (CEDEARs) o ticker (símbolo, RF)
    col_ts: str
    tz_aware: bool      # timesales guarda naive ART; las otras dos, timestamptz
    es_barra: bool
    etiqueta: str


_TAPE_RV = _Fuente("mercado.cedears_time_sales", "ticker_corto", "ts",
                   tz_aware=True, es_barra=False, etiqueta="cedears_time_sales · tick a tick")
_BARS_RV = _Fuente("mercado.cedears_bars_1m", "ticker_corto", "minuto",
                   tz_aware=True, es_barra=True, etiqueta="cedears_bars_1m · barras de 1'")
_TAPE_RF = _Fuente("mercado.timesales", "ticker", "ts",
                   tz_aware=False, es_barra=False, etiqueta="timesales · tick a tick")

# (ruedas, paso en minutos de la serie). El orden es el de la UI.
VENTANAS: dict[str, dict[str, tuple[int, int]]] = {
    "rv": {"hoy": (1, 1), "5r": (5, 5), "20r": (20, 15)},
    "rf": {"hoy": (1, 1), "3r": (3, 5), "5r": (5, 5)},
}

_ETIQUETA_VENTANA = {"hoy": "HOY", "3r": "3 R", "5r": "5 R", "20r": "20 R"}

# Con qué ventana ABRE la tab. Vive acá y no en la pantalla por la misma razón
# que las ventanas mismas: el front no puede abrir en una que el service
# rechaza. Se abre con la MÁS LARGA de cada clase (pedido de la mesa
# 2026-09-04) — el volumen por precio dice más cuanto más historia agarra: un
# POC de un día es el precio de hoy, uno de 20 ruedas es el nivel que el papel
# viene defendiendo. Renta fija abre en 5R, que es toda la que tiene.
POR_DEFECTO: dict[str, str] = {"rv": "20r", "rf": "5r"}


def _fuente(clase: str, ruedas: int) -> _Fuente:
    """La regla, en UN solo lugar. Renta variable cambia de tabla al pasar de HOY
    a multi-rueda porque su tape se vacía todas las noches; renta fija no."""
    if clase == "rf":
        return _TAPE_RF
    return _TAPE_RV if ruedas <= 1 else _BARS_RV


def ventanas(clase: str) -> list[dict]:
    """Las ventanas que ofrece la clase, con la fuente de cada una. La pantalla
    las dibuja de acá: si mañana renta variable gana ventana, no se toca el front."""
    out = []
    for clave, (ruedas, paso) in VENTANAS[clase].items():
        f = _fuente(clase, ruedas)
        out.append({
            "ventana": clave,
            "etiqueta": _ETIQUETA_VENTANA.get(clave, clave.upper()),
            "ruedas": ruedas,
            "paso_min": paso,
            "fuente": f.etiqueta,
            "aproximado": f.es_barra,
            "por_defecto": clave == POR_DEFECTO[clase],
        })
    return out


# ── lógica PURA (sin base) ───────────────────────────────────────────────────

def rellenar_buckets(filas: list[dict], nb: int, lo: float, hi: float) -> list[dict]:
    """Perfil CONTIGUO de `nb` buckets entre `lo` y `hi`.

    La query solo devuelve los buckets que tuvieron volumen. Rellenar los huecos
    con cero es lo que hace que el histograma se pueda dibujar barra a barra sin
    que el front invente los precios que faltan (y sin que dos buckets vacíos
    consecutivos se vean como uno solo, que es como se lee mal un perfil)."""
    ancho = (hi - lo) / nb if nb and hi > lo else 0.0
    vistos = {int(f["b"]): f for f in filas if f.get("b")}
    out: list[dict] = []
    for i in range(1, nb + 1):
        f = vistos.get(i)
        out.append({
            "px_lo": lo + (i - 1) * ancho,
            "px_hi": lo + i * ancho,
            "vol": float(f["vol"]) if f and f["vol"] is not None else 0.0,
            "trades": int(f["trades"]) if f and f["trades"] is not None else 0,
        })
    return out


def area_de_valor(buckets: list[dict], pct: float = AREA_DE_VALOR) -> tuple[float | None, float | None]:
    """(VAL, VAH): la banda de precios donde se hizo `pct` del volumen.

    Se van tomando los buckets de mayor a menor volumen hasta acumular el
    porcentaje, y la banda son el mínimo y el máximo de ESOS buckets. Ojo: la
    banda puede contener buckets flojos que no fueron elegidos — es un rango, no
    un conjunto, y así es como se lee en cualquier plataforma."""
    total = sum(b["vol"] for b in buckets)
    if total <= 0:
        return None, None
    acum, elegidos = 0.0, []
    for b in sorted(buckets, key=lambda x: x["vol"], reverse=True):
        if b["vol"] <= 0:
            break
        acum += b["vol"]
        elegidos.append(b)
        if acum >= total * pct:
            break
    if not elegidos:
        return None, None
    return min(b["px_lo"] for b in elegidos), max(b["px_hi"] for b in elegidos)


def _poc(buckets: list[dict]) -> dict | None:
    """El bucket de más volumen (Point of Control). `None` si no se operó nada."""
    vivos = [b for b in buckets if b["vol"] > 0]
    if not vivos:
        return None
    b = max(vivos, key=lambda x: x["vol"])
    return {"px_lo": b["px_lo"], "px_hi": b["px_hi"],
            "px": (b["px_lo"] + b["px_hi"]) / 2, "vol": b["vol"]}


# ── SQL ──────────────────────────────────────────────────────────────────────

def _expr_ts(f: _Fuente) -> str:
    """La hora SIEMPRE en pared argentina. `mercado.timesales` ya guarda naive
    ART (ver sql/schema.sql); las otras dos son timestamptz y hay que bajarlas,
    o el chart mostraría la rueda corrida 3 horas."""
    return f"({f.col_ts} AT TIME ZONE '{_TZ_SQL}')" if f.tz_aware else f.col_ts


def _expr_bucket(paso: int) -> str:
    """Bucket temporal de `paso` minutos, anclado a la hora. Se hace con
    date_trunc + make_interval y NO con epoch: epoch obliga a pasar por
    timestamptz y ahí se cuela el corrimiento de zona que este service evita."""
    if paso <= 1:
        return "date_trunc('minute', ts)"
    return (f"date_trunc('hour', ts) + make_interval("
            f"mins => (EXTRACT(MINUTE FROM ts)::int / {paso}) * {paso})")


def _sql(f: _Fuente, paso: int, nb: int) -> str:
    """UNA query: serie, perfil y totales de una sola pasada por el índice.

    `paso` y `nb` se interpolan como enteros ya validados por el caller (no son
    input crudo del usuario); la clave y el inicio van parametrizados."""
    if f.es_barra:
        base = f"""
            SELECT {_expr_ts(f)}                        AS ts,
                   {f.col_ts}                           AS ord,
                   open::float8                         AS o,
                   high::float8                         AS h,
                   low::float8                          AS l,
                   close::float8                        AS c,
                   ((high + low + close) / 3)::float8   AS px,
                   COALESCE(volume, 0)::float8          AS vol,
                   COALESCE(trades, 0)::int             AS trades
            FROM {f.tabla}
            WHERE {f.col_k} = %(clave)s AND {f.col_ts} >= %(ini)s
              AND close IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
        """
    else:
        base = f"""
            SELECT {_expr_ts(f)}               AS ts,
                   id                          AS ord,
                   price::float8               AS o,
                   price::float8               AS h,
                   price::float8               AS l,
                   price::float8               AS c,
                   price::float8               AS px,
                   COALESCE(size, 0)::float8   AS vol,
                   1                           AS trades
            FROM {f.tabla}
            WHERE {f.col_k} = %(clave)s AND {f.col_ts} >= %(ini)s AND price IS NOT NULL
        """
    return f"""
        WITH t AS MATERIALIZED ({base}),
        r AS (
            SELECT min(px) AS lo, max(px) AS hi, sum(vol) AS vol, sum(trades) AS trades,
                   sum(px * vol) AS pv, count(*) AS n,
                   min(ts) AS ts_ini, max(ts) AS ts_fin,
                   count(DISTINCT ts::date) AS ruedas
            FROM t
        ),
        s AS (
            SELECT {_expr_bucket(paso)}                       AS b,
                   (array_agg(o ORDER BY ts, ord))[1]         AS o,
                   max(h)                                     AS h,
                   min(l)                                     AS l,
                   (array_agg(c ORDER BY ts DESC, ord DESC))[1] AS c,
                   sum(vol)                                   AS vol,
                   sum(trades)                                AS trades
            FROM t GROUP BY 1
        ),
        p AS (
            -- El tope se empuja: sin eso el precio MÁXIMO cae en el bucket nb+1
            -- (un bucket fantasma de un solo trade que en un papel ilíquido sale
            -- como POC), y con rango nulo width_bucket directamente revienta.
            SELECT width_bucket(t.px, r.lo,
                       CASE WHEN r.hi > r.lo THEN r.hi + (r.hi - r.lo) / 1000
                            ELSE r.lo + 1 END,
                       {nb})            AS b,
                   sum(t.vol)           AS vol,
                   sum(t.trades)        AS trades
            FROM t, r GROUP BY 1
        )
        SELECT
          (SELECT json_agg(json_build_object(
                    't',  to_char(b, 'YYYY-MM-DD"T"HH24:MI:00'),
                    'o', o, 'h', h, 'l', l, 'c', c,
                    'vol', vol, 'trades', trades) ORDER BY b) FROM s)  AS serie,
          (SELECT json_agg(json_build_object(
                    'b', b, 'vol', vol, 'trades', trades) ORDER BY b) FROM p) AS perfil,
          (SELECT row_to_json(r) FROM r)                               AS meta
    """


def _inicio(cur, f: _Fuente, clave: str, ruedas: int):
    """Desde cuándo pedir. Con 1 rueda es la medianoche ART de HOY; con más, la
    medianoche de la N-ésima rueda **que existe** — no `now() - N días`: las
    tablas guardan ruedas, no días corridos, y un feriado correría la ventana
    entera sin que nadie se entere."""
    if ruedas <= 1:
        base = datetime.now(_ART).replace(hour=0, minute=0, second=0, microsecond=0)
        return base if f.tz_aware else base.replace(tzinfo=None)
    cur.execute(
        f"SELECT DISTINCT {_expr_ts(f)}::date AS f FROM {f.tabla} "
        f"WHERE {f.col_k} = %s ORDER BY f DESC LIMIT %s",
        (clave, ruedas),
    )
    filas = cur.fetchall()
    if not filas:
        return None
    base = datetime.combine(filas[-1]["f"], datetime.min.time())
    return base.replace(tzinfo=_ART) if f.tz_aware else base


# ── universo (el rail izquierdo) ─────────────────────────────────────────────

@cached(ttl=30)
def universo(*, clase: str) -> list[dict]:
    """Catálogo de la clase para el rail, con su último precio.

    Renta variable lee el MISMO scanner que alimenta los radares de `/trading`
    (`get_cedears_scanner`) en vez de armar su propia consulta: si leyeran
    fuentes distintas, el mismo papel podría mostrar dos precios en la misma
    pantalla (REGLA #9). Renta fija sale del master + `market_snapshot`."""
    if clase == "rf":
        return _universo_rf()
    return _universo_rv()


def _universo_rv() -> list[dict]:
    nombres = {r.get("ticker_corto"): (r.get("nombre") or "")
               for r in scanner_svc.get_universo()}
    try:
        vivos = {str(r.get("ticker_corto", "")).upper(): r
                 for r in scanner_svc.get_cedears_scanner()}
    except Exception as e:                                   # pragma: no cover
        logger.debug("monitor: scanner no disponible (%s)", e)
        vivos = {}
    out: list[dict] = []
    for tk, nombre in nombres.items():
        if not tk:
            continue
        v = vivos.get(str(tk).upper(), {})
        out.append({
            "ticker": tk, "nombre": nombre, "grupo": "CEDEARs", "moneda": "ARS",
            "last": v.get("last"), "var_pct": v.get("intraday_pct"),
            "cash": v.get("total_money"),
        })
    # por plata operada: arriba lo que la mesa mira. Sin dato, al fondo.
    out.sort(key=lambda d: (d["cash"] is None, -(d["cash"] or 0), d["ticker"]))
    return out


def _universo_rf() -> list[dict]:
    catalogo = pivots_svc.bonos_universo()
    simbolos = pivots_svc.simbolos_de_bonos()
    largos = [simbolos[d["ticker_corto"]] for d in catalogo
              if d["ticker_corto"] in simbolos]
    live = market_snapshot.cols_map(largos, ["last_price", "closing_price"])
    # La clasificación por curva NO se calcula acá: sale de `curvas_vista`, el
    # mismo lugar del que la saca la tab CURVAS de renta fija. Un bono no puede
    # ser TASA FIJA en una pantalla y CER en la otra (REGLA #9).
    pills = curvas_svc.pills_del_master()
    out: list[dict] = []
    for d in catalogo:
        tk = d["ticker_corto"]
        m = live.get(simbolos.get(tk, ""), {})
        last, prev = m.get("last_price"), m.get("closing_price")
        # Un dual CER+TAMAR entra en DOS curvas y aparece con las dos elegidas:
        # es donde el trader lo busca, igual que en la tab CURVAS.
        del_bono = list(pills.get(tk) or ()) or [SIN_CURVA]
        out.append({
            "ticker": tk, "nombre": d.get("nombre") or "", "grupo": d.get("nombre") or "Bono",
            "moneda": None, "last": last, "cash": None,
            "var_pct": ((last / prev - 1) * 100) if last and prev else None,
            "curvas": del_bono,
        })
    out.sort(key=lambda d: (d["grupo"], d["ticker"]))
    return out


@cached(ttl=60)
def curvas(*, clase: str) -> list[dict]:
    """Las curvas del rail (TASA FIJA · CER · TAMAR · …) con cuántos bonos tiene
    cada una, para el filtro de renta fija. Se derivan del universo que la
    pantalla ya muestra: si se contaran aparte, una pill podría decir 40 y la
    lista mostrar 12. Renta variable no tiene curvas → lista vacía."""
    if clase != "rf":
        return []
    cuenta: dict[str, int] = {}
    for it in universo(clase="rf"):
        for c in it.get("curvas") or []:
            cuenta[c] = cuenta.get(c, 0) + 1
    # El orden es el de `core.curvas_ejes.PILLS` (el MISMO de la tab CURVAS), y
    # lo que no esté ahí va al final; OTROS siempre último.
    orden = {p: i for i, p in enumerate(ce.pills_disponibles())}
    def _clave(c: str) -> tuple[int, int, str]:
        if c == SIN_CURVA:
            return (2, 0, c)
        return (0, orden.get(c, 99), c)
    return [
        {"codigo": c,
         "display": SIN_CURVA_DISPLAY if c == SIN_CURVA else ce.display_de(c),
         "lado": "—" if c == SIN_CURVA else ce.lado_de(c),
         "n": cuenta[c]}
        for c in sorted(cuenta, key=_clave)
    ]


# ── el endpoint ──────────────────────────────────────────────────────────────

def get_monitor(*, clase: str, ticker: str, ventana: str | None = None,
                buckets: int = BUCKETS_DEF) -> dict:
    """Serie de precio + volumen por precio de UN instrumento en UNA ventana.

    Shape:
        {clase, ticker, ventana, fuente, aproximado, paso_min, buckets:[...],
         serie:[{t,o,h,l,c,vol,trades}], poc, val, vah,
         resumen:{last, first, high, low, vwap, vol, trades, ruedas, desde, hasta}}

    Sin datos (papel que no operó, ventana vacía) devuelve el MISMO shape con
    `serie` y `buckets` vacíos y `sin_datos: true` — nunca una excepción: la
    pantalla tiene que poder decir "no operó" sin quedarse en blanco."""
    clase = (clase or "rv").lower()
    if clase not in CLASES:
        raise ValueError(f"clase inválida: {clase!r} (esperado: {', '.join(CLASES)})")
    ventana = (ventana or POR_DEFECTO[clase]).lower()
    if ventana not in VENTANAS[clase]:
        raise ValueError(
            f"ventana inválida para {clase}: {ventana!r} "
            f"(esperado: {', '.join(VENTANAS[clase])})")
    tk = (ticker or "").strip().upper()
    if not tk:
        raise ValueError("falta el ticker")
    nb = max(BUCKETS_MIN, min(int(buckets or BUCKETS_DEF), BUCKETS_MAX))

    ruedas = VENTANAS[clase][ventana][0]
    if ruedas <= 1:
        return _monitor_vivo(clase=clase, ticker=tk, ventana=ventana, buckets=nb)
    return _monitor_archivo(clase=clase, ticker=tk, ventana=ventana, buckets=nb)


# Dos cachés a propósito: HOY va contra un tape que se escribe cada segundo y el
# poll de la pantalla lo pide seguido (2 s, igual que /pivots); el multi-rueda
# sale de tablas que ya no cambian hasta el próximo cierre y recomputarlo cada
# 2 s sería pagar 100 ms por nada.
@cached(ttl=2)
def _monitor_vivo(*, clase: str, ticker: str, ventana: str, buckets: int) -> dict:
    return _consultar(clase=clase, ticker=ticker, ventana=ventana, buckets=buckets)


@cached(ttl=120)
def _monitor_archivo(*, clase: str, ticker: str, ventana: str, buckets: int) -> dict:
    return _consultar(clase=clase, ticker=ticker, ventana=ventana, buckets=buckets)


def _clave(clase: str, ticker: str) -> str | None:
    """La clave con la que se busca en la tabla. En renta variable es el ticker
    corto; en renta fija es el SÍMBOLO DE MERCADO, que sale del master (el mismo
    que usa `/pivots`) — `AL30` se guarda como `MERV - XMEV - AL30D - 24hs`."""
    if clase == "rf":
        return pivots_svc.simbolos_de_bonos().get(ticker)
    return ticker


def _vacio(clase: str, ticker: str, ventana: str, f: _Fuente, paso: int, motivo: str) -> dict:
    return {
        "clase": clase, "ticker": ticker, "ventana": ventana, "fuente": f.etiqueta,
        "aproximado": f.es_barra, "paso_min": paso, "sin_datos": True, "motivo": motivo,
        "serie": [], "buckets": [], "poc": None, "val": None, "vah": None,
        "resumen": {},
    }


def _consultar(*, clase: str, ticker: str, ventana: str, buckets: int) -> dict:
    ruedas, paso = VENTANAS[clase][ventana]
    f = _fuente(clase, ruedas)
    clave = _clave(clase, ticker)
    if not clave:
        return _vacio(clase, ticker, ventana, f, paso, "el ticker no está en el master")

    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            ini = _inicio(cur, f, clave, ruedas)
            if ini is None:
                return _vacio(clase, ticker, ventana, f, paso, "sin ruedas guardadas")
            cur.execute(_sql(f, paso, buckets), {"clave": clave, "ini": ini})
            fila = cur.fetchone() or {}
    except Exception as e:
        # Las tres tablas se crean HACIA ADELANTE (el DDL vive en el primer run de
        # su job/motor). En una instalación donde el archivo de barras todavía no
        # existe, esto tiene que devolver "no hay datos" y no un 500 que se lleva
        # puesta la pantalla entera — el mismo criterio que `trading_pivots`.
        logger.warning("monitor: %s no disponible para %s (%s)", f.tabla, ticker, e)
        return _vacio(clase, ticker, ventana, f, paso, "la fuente no está disponible")

    meta = fila.get("meta") or {}
    serie = fila.get("serie") or []
    if not serie or meta.get("lo") is None:
        return _vacio(clase, ticker, ventana, f, paso, "no operó en la ventana")

    bks = rellenar_buckets(fila.get("perfil") or [], buckets,
                           float(meta["lo"]), float(meta["hi"]))
    val, vah = area_de_valor(bks)
    vol = float(meta.get("vol") or 0)
    pv = float(meta.get("pv") or 0)
    return {
        "clase": clase, "ticker": ticker, "ventana": ventana, "fuente": f.etiqueta,
        "aproximado": f.es_barra, "paso_min": paso, "sin_datos": False,
        "serie": serie,
        "buckets": bks,
        "poc": _poc(bks),
        "val": val, "vah": vah,
        "resumen": {
            "first": serie[0].get("o"),
            "last": serie[-1].get("c"),
            "high": float(meta["hi"]),
            "low": float(meta["lo"]),
            "vwap": (pv / vol) if vol else None,
            "vol": vol,
            "trades": int(meta.get("trades") or 0),
            "ruedas": int(meta.get("ruedas") or 0),
            "desde": serie[0].get("t"),
            "hasta": serie[-1].get("t"),
        },
    }

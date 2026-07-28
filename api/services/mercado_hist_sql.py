"""api/services/mercado_hist_sql.py — históricos de mercado leyendo Postgres.

Espejo SQL-native de los `get_historico_*` de `derivados.py` (futuros DLR,
forwards, breakevens) y `repo.py` (caución). Fuente: `mercado.mercado_hist`,
tabla genérica grano (coleccion, fecha, k=subclave) con el doc completo en
`data jsonb`. Devolver `data` reconstruye el doc original (incluido `fecha` como
string, igual que Mongo).

Paridad de datetime: el jsonb se escribió con datetime→`.isoformat()`
(`sync._jsonb` → `pg_mirror.doc_iso`), idéntico a lo que FastAPI emite en el path
Mongo (jsonable_encoder usa isoformat); estos endpoints NO tienen serializer
custom → no hace falta `_fix_tz`.

Filtros: `fecha` por la columna `date` (== comparación lexicográfica del string
Mongo 'YYYY-MM-DD'); subclave por la columna `k` (k=ticker en FuturosDLR, moneda
en Caucion, curva en ForwardsHistorico, '' en BreakevensHistorico).

Lee de Postgres (Supabase); las tablas espejo son el system of record.
"""
from __future__ import annotations

from datetime import date

from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool


def _hist(coleccion: str, k: str | None = None, desde: str | None = None,
          hasta: str | None = None, order_extra: str | None = None) -> list:
    """[doc, ...] de `mercado_hist` para una colección, reconstruidos desde jsonb."""
    where = ["coleccion = %s"]
    params: list = [coleccion]
    if k is not None:
        where.append("k = %s")
        params.append(k)
    if desde:
        where.append("fecha >= %s::date")
        params.append(desde)
    if hasta:
        where.append("fecha <= %s::date")
        params.append(hasta)
    order = "fecha" + (f", {order_extra}" if order_extra else "")
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT data FROM mercado.mercado_hist WHERE {' AND '.join(where)} "
            f"ORDER BY {order}",
            tuple(params),
        )
        return [r["data"] for r in cur.fetchall()]


def _sin_fecha(d: dict) -> dict:
    """El live (ForwardsLive/BreakevensLive) NO tiene `fecha`; la fila de mercado_hist sí.
    La saco para que el shape sea idéntico al doc live de Mongo."""
    return {k: v for k, v in d.items() if k != "fecha"}


@cached(ttl=30)
def get_forwards(curva: str | None = None) -> list:
    """LIVE forwards desde SQL = la fila MÁS RECIENTE (max fecha) por curva de
    ForwardsHistorico en `mercado.mercado_hist`. Equivale a Trading.ForwardsLive
    (1 doc por curva, último valor pisado). La frescura intradía la da el motor
    (mirror_hist bajo SNAPSHOT_SQL refresca la fila de hoy en cada tick)."""
    where = ["coleccion = 'ForwardsHistorico'"]
    params: list = []
    if curva:
        where.append("k = %s")
        params.append(curva)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT DISTINCT ON (k) data FROM mercado.mercado_hist "
            f"WHERE {' AND '.join(where)} ORDER BY k, fecha DESC",
            tuple(params),
        )
        return [_sin_fecha(r["data"]) for r in cur.fetchall()]


@cached(ttl=30)
def get_futuros_dlr() -> list:
    """Snapshot LIVE de futuros DLR desde SQL (mercado.futuros_dlr_snapshot, dual-write
    del motor bajo SNAPSHOT_SQL). Filtra `vencimiento` > hoy (YYYYMMDD) y ordena — igual
    que el path Mongo (FuturosDLRSnapshot). Devuelve el doc completo (data jsonb)."""
    hoy = date.today().strftime("%Y%m%d")
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT data FROM mercado.futuros_dlr_snapshot "
            "WHERE vencimiento > %s ORDER BY vencimiento", (hoy,))
        return [r["data"] for r in cur.fetchall()]


@cached(ttl=60)
def get_forwards_zscore(curva: str | None = None) -> list:
    """Coeficientes z-score (media/desvío) por curva desde SQL (mercado.forwards_zscore,
    dual-write del job bajo MERCADO_SQL_WRITE). Igual que el path Mongo (ForwardsZscore)."""
    where, params = "", ()
    if curva:
        where = "WHERE curva = %s"
        params = (curva,)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT data FROM mercado.forwards_zscore {where}", params)
        return [r["data"] for r in cur.fetchall()]


@cached(ttl=30)
def get_caucion(moneda: str | None = None) -> list:
    """Snapshot LIVE de caución desde SQL (mercado.caucion_snapshot, dual-write motor bajo
    SNAPSHOT_SQL). Con `moneda` (uppercase) filtra esa; sin filtro devuelve todas. Igual
    que el path Mongo (CaucionSnapshot)."""
    where, params = "", ()
    if moneda:
        where = "WHERE moneda = %s"
        params = (moneda.upper(),)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT data FROM mercado.caucion_snapshot {where}", params)
        return [r["data"] for r in cur.fetchall()]


@cached(ttl=30)
def breakevens_docs_raw() -> list:
    """Doc live de breakevens SIN curar (fila más reciente de BreakevensHistorico).
    Lo usa la matriz de Manager (necesita ver TODOS los pares, incluidos los
    excluidos). La vista pública usa `get_breakevens`, que filtra los excluidos."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT data FROM mercado.mercado_hist WHERE coleccion = 'BreakevensHistorico' "
            "ORDER BY fecha DESC LIMIT 1",
        )
        return [_sin_fecha(r["data"]) for r in cur.fetchall()]


def get_breakevens() -> list:
    """LIVE breakevens desde SQL = la fila MÁS RECIENTE de BreakevensHistorico
    (1 doc global). Equivale a Trading.BreakevensLive.

    Curaduría (2026-07): filtra los pares (lecap, cer) marcados como EXCLUIDOS en
    Manager (`mercado.breakevens_overrides`). El motor los sigue calculando; acá se
    ocultan al instante, sin reiniciar el motor. Fail-open: si el filtro falla, se
    devuelve todo."""
    docs = breakevens_docs_raw()
    try:
        from api.services.breakevens_admin import get_excluidos
        excl = get_excluidos()
        if excl:
            for d in docs:
                pares = d.get("pares")
                if isinstance(pares, list):
                    d["pares"] = [
                        p for p in pares
                        if (p.get("lecap"), p.get("cer")) not in excl
                    ]
    except Exception:
        pass
    return docs


@cached(ttl=300)
def get_historico_futuros_dlr(
    ticker: str | None = None, desde: str | None = None, hasta: str | None = None,
) -> list:
    # Mongo ordena (fecha, vencimiento); k=ticker, así que el 2do criterio sale del jsonb.
    return _hist("FuturosDLR", k=ticker, desde=desde, hasta=hasta,
                 order_extra="(data->>'vencimiento')")


@cached(ttl=300)
def get_historico_forwards(
    curva: str | None = None, desde: str | None = None, hasta: str | None = None,
) -> list:
    # Mongo no ordena este (orden natural); ordenamos por (fecha, k=curva) — determinista,
    # y el front no depende del orden de empate (la lectura Mongo tampoco lo garantizaba).
    return _hist("ForwardsHistorico", k=curva, desde=desde, hasta=hasta, order_extra="k")


@cached(ttl=300)
def get_historico_breakevens(desde: str | None = None, hasta: str | None = None) -> list:
    return _hist("BreakevensHistorico", desde=desde, hasta=hasta)


@cached(ttl=300)
def get_historico_caucion(
    moneda: str | None = None, desde: str | None = None, hasta: str | None = None,
) -> list:
    # Mongo ordena (fecha, moneda); k=moneda. El filtro Mongo uppercasea el input.
    k = moneda.upper() if moneda else None
    return _hist("Caucion", k=k, desde=desde, hasta=hasta, order_extra="k")

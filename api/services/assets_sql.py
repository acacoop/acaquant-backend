"""api/services/assets_sql.py — lectura del catálogo de títulos desde SQL
`portafolio.assets` (la fuente de verdad que edita Manager → Assets).

Reemplaza las lecturas de Mongo `Valuaciones.Assets` SIN obligar a reescribir el
código consumidor: `assets_rows()` devuelve dicts con claves UPPERCASE (mismo
shape que traía Mongo), así el caller sigue usando `a["TICKER"]`, `a["CARTERA"]`,
etc. Los vacíos vienen NULL de SQL → se normalizan a '' (string) para no romper
comparaciones; FEE_ADMIN (numérico) se deja como None.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterable

from core.postgres import get_pool

# Campo Mongo UPPERCASE → columna SQL en portafolio.assets.
_FIELD_COL = {
    "CARTERA": "cartera", "CLASE_ACTIVO": "clase_activo", "EMISOR": "emisor",
    "TICKER": "ticker", "INSTRUMENTO": "instrumento", "CALIFICACION": "calificacion",
    "CAFCI": "cafci", "VENCIMIENTO": "vencimiento", "CODIGO_CNV": "codigo_cnv",
    "FEE_ADMIN": "fee_admin",
}
_NUMERIC = frozenset({"FEE_ADMIN"})

# Cache del catálogo completo (perf 2026-06-29). `assets_rows` es la lectura más
# transversal (pnl, acreencias, etc.) y hacía un full-scan por call; el catálogo lo
# edita Manager → cambia raro. Se cachea la tabla entera 300s y se proyectan los fields
# pedidos en memoria (chica). Tras editar un asset llamar `invalidar()`.
_TTL = 300.0
_cache: dict = {"rows": None, "ts": 0.0}
_lock = threading.Lock()
_ALL_FIELDS = tuple(_FIELD_COL)


def _all_rows() -> list[dict]:
    """Catálogo completo cacheado (todas las columnas, normalizado). NO mutar."""
    now = time.monotonic()
    if _cache["rows"] is not None and (now - _cache["ts"]) < _TTL:
        return _cache["rows"]
    with _lock:
        if _cache["rows"] is None or (time.monotonic() - _cache["ts"]) >= _TTL:
            cols = ["unidad", *[_FIELD_COL[f] for f in _ALL_FIELDS]]
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT {', '.join(cols)} FROM portafolio.assets")
                rows = []
                for row in cur.fetchall():
                    d = {"unidad": row[0] or ""}
                    for i, f in enumerate(_ALL_FIELDS, 1):
                        d[f] = row[i] if f in _NUMERIC else (row[i] or "")
                    rows.append(d)
            _cache["rows"] = rows
            _cache["ts"] = time.monotonic()
        return _cache["rows"]


def invalidar() -> None:
    """Forzar recarga del catálogo en la próxima lectura (tras editar un asset)."""
    _cache["rows"] = None
    _cache["ts"] = 0.0


def assets_rows(fields: Iterable[str]) -> list[dict]:
    """`[{unidad, <FIELD>: valor, ...}]` con claves UPPERCASE (shape Mongo), proyectado
    del catálogo cacheado. `fields`: campos UPPERCASE (sin 'unidad', que va siempre).
    Strings NULL → '' ; FEE_ADMIN NULL → None."""
    fields = list(fields)
    return [{"unidad": r["unidad"], **{f: r[f] for f in fields}} for r in _all_rows()]


# ── Lecturas del PANEL Manager → Assets (shape UPPERCASE completo, incl. auditoría) ──
# (columna SQL, clave de salida). Las de auditoría salen tal cual (no UPPERCASE).
_PANEL = [
    ("unidad", "unidad"), ("cartera", "CARTERA"), ("emisor", "EMISOR"),
    ("instrumento", "INSTRUMENTO"), ("clase_activo", "CLASE_ACTIVO"),
    ("calificacion", "CALIFICACION"), ("ticker", "TICKER"), ("vencimiento", "VENCIMIENTO"),
    ("fee_admin", "FEE_ADMIN"), ("codigo_cnv", "CODIGO_CNV"), ("cafci", "CAFCI"),
    ("actualizado_por", "actualizado_por"), ("actualizado_at", "actualizado_at"),
]
_PANEL_COLS = [c for c, _ in _PANEL]
_PANEL_KEYS = [k for _, k in _PANEL]
# NULL / '' / 'NO APLICA' = "vacío" (mismo criterio que tenía Mongo _EMPTY_VALUES).
_EMPTY = "({col} IS NULL OR {col} = '' OR {col} = 'NO APLICA')"


def _row_panel(row) -> dict:
    return dict(zip(_PANEL_KEYS, row, strict=True))


def list_assets_panel(cartera: str | None = None, emisor: str | None = None,
                      campo_vacio: str | None = None, limit: int = 5000) -> list[dict]:
    """Lista del panel desde SQL. Filtros: cartera/emisor exactos, `campo_vacio`
    (campo UPPERCASE) = ese campo vacío/NULL/'NO APLICA'."""
    conds, params = [], []
    if cartera:
        conds.append("cartera = %s"); params.append(cartera)
    if emisor:
        conds.append("emisor = %s"); params.append(emisor)
    if campo_vacio and campo_vacio in _FIELD_COL:
        conds.append(_EMPTY.format(col=_FIELD_COL[campo_vacio]))
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    sql = (f"SELECT {', '.join(_PANEL_COLS)} FROM portafolio.assets {where} "
           f"ORDER BY unidad LIMIT {int(limit)}")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [_row_panel(r) for r in cur.fetchall()]


def gaps_assets_panel(limit: int = 5000) -> list[dict]:
    """Assets con CARTERA o EMISOR vacíos."""
    sql = (f"SELECT {', '.join(_PANEL_COLS)} FROM portafolio.assets "
           f"WHERE {_EMPTY.format(col='cartera')} OR {_EMPTY.format(col='emisor')} "
           f"ORDER BY unidad LIMIT {int(limit)}")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [_row_panel(r) for r in cur.fetchall()]


def values_assets_panel(fields: Iterable[str]) -> dict[str, list[str]]:
    """Valores únicos por campo UPPERCASE (para los datalist del form). Filtra
    vacíos y 'NO APLICA'. UNA query para todos los campos (array_agg DISTINCT
    por columna en una sola pasada de la tabla — antes: un SELECT por campo)."""
    pares = [(f, _FIELD_COL[f]) for f in fields]
    if not pares:
        return {}
    sel = ", ".join(f"array_agg(DISTINCT {col}) FILTER (WHERE {col} IS NOT NULL)"
                    for _, col in pares)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {sel} FROM portafolio.assets")
        row = cur.fetchone()
    return {f: sorted({v for v in (vals or [])
                       if isinstance(v, str) and v and v != "NO APLICA"})
            for (f, _), vals in zip(pares, row, strict=True)}


def asset_one_panel(unidad: str) -> dict | None:
    """Un asset por unidad (shape panel) — para devolver el doc tras un PATCH."""
    sql = f"SELECT {', '.join(_PANEL_COLS)} FROM portafolio.assets WHERE unidad = %s"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (unidad,))
        row = cur.fetchone()
        return _row_panel(row) if row else None

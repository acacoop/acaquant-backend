"""api/services/assets_sql.py — lectura del catálogo de títulos desde SQL
`portafolio.assets` (la fuente de verdad que edita Manager → Assets).

Reemplaza las lecturas de Mongo `Valuaciones.Assets` SIN obligar a reescribir el
código consumidor: `assets_rows()` devuelve dicts con claves UPPERCASE (mismo
shape que traía Mongo), así el caller sigue usando `a["TICKER"]`, `a["CARTERA"]`,
etc. Los vacíos vienen NULL de SQL → se normalizan a '' (string) para no romper
comparaciones; FEE_ADMIN (numérico) se deja como None.
"""
from __future__ import annotations

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


def assets_rows(fields: Iterable[str]) -> list[dict]:
    """Lee portafolio.assets y devuelve `[{unidad, <FIELD>: valor, ...}]` con claves
    UPPERCASE (shape Mongo). `fields`: campos UPPERCASE (sin 'unidad', que va siempre).
    Strings NULL → '' ; FEE_ADMIN NULL → None."""
    fields = list(fields)
    cols = ["unidad", *[_FIELD_COL[f] for f in fields]]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(cols)} FROM portafolio.assets")
        out = []
        for row in cur.fetchall():
            d = {"unidad": row[0] or ""}
            for i, f in enumerate(fields, 1):
                d[f] = row[i] if f in _NUMERIC else (row[i] or "")
            out.append(d)
        return out


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
    vacíos y 'NO APLICA'."""
    out: dict[str, list[str]] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        for f in fields:
            col = _FIELD_COL[f]
            cur.execute(f"SELECT DISTINCT {col} FROM portafolio.assets WHERE {col} IS NOT NULL")
            out[f] = sorted({r[0] for r in cur.fetchall()
                             if isinstance(r[0], str) and r[0] and r[0] != "NO APLICA"})
    return out


def asset_one_panel(unidad: str) -> dict | None:
    """Un asset por unidad (shape panel) — para devolver el doc tras un PATCH."""
    sql = f"SELECT {', '.join(_PANEL_COLS)} FROM portafolio.assets WHERE unidad = %s"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (unidad,))
        row = cur.fetchone()
        return _row_panel(row) if row else None

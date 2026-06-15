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

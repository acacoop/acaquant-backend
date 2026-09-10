"""api/services/fci_sql.py — la vista FCI: tabla de rendimientos + ficha de un fondo.

Doc madre: `docs/FCI.md`. Servicio PURO (sin FastAPI), lee Postgres.

El universo que sirve es `mercado.fci` activo **de gerentes seguidas**
(`mercado.fci_gerentes.seguida`): un fondo de una gerente con la que la mesa no
opera no sale por acá aunque exista la fila.

LA CONVENCIÓN DE ANCLAS (una sola, la misma que el watchlist de HOME —
`jobs/market_anchors.py`): cada rendimiento es `vcp_hoy / vcp_ancla − 1`, con
`vcp_hoy` = el último VCP del fondo (`fecha`) y `vcp_ancla` = el último VCP
**en o antes** de la fecha ancla:

    1D    el VCP anterior al último (rueda previa con dato)
    WTD   el último VCP ANTES del lunes de la semana de `fecha` (= el viernes)
    MTD   el último VCP ANTES del 1° del mes de `fecha`
    YTD   el último VCP ANTES del 1° de enero del año de `fecha`
    7D · 30D · 90D · 365D   el último VCP en o antes de `fecha − N días corridos`

Fracciones (0.0123 = 1,23 %). La TNA «según 30D» del informe es `r_30d × 365/30`
(`=+G13/30*365` en el Excel de la mesa). Todo sale de `mercado.fci_vcp`.

Costo: por fondo, 9 seeks sobre la PK (clase, fecha DESC LIMIT 1). Con ~300
fondos es un query de milisegundos; cacheado 120 s porque el dato es diario.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from api.cache import cached
from core.postgres import get_pool

# Orden de los estantes en la vista (los del informe primero; el resto alfabético;
# sin estante al final).
ORDEN_CATEGORIAS: tuple[str, ...] = (
    "T+0 MONEY MARKET", "T+0 LECAPS", "T+0", "T+1", "CER", "DÓLAR LINK",
    "MONEY MARKET USD", "RENTA FIJA USD", "RENTA FIJA USD ARG", "RENTA FIJA USD LATAM",
    "RENTA MIXTA", "RENTA MIXTA USD", "RENTA VARIABLE",
)


def _lateral(alias: str, cond: str) -> str:
    return (f"LEFT JOIN LATERAL (SELECT v.vcp, v.fecha, v.fuente FROM mercado.fci_vcp v "
            f"WHERE v.fci_id = u.fci_id AND {cond} "
            f"ORDER BY v.fecha DESC LIMIT 1) {alias} ON true ")


_SQL_TABLA = (
    "WITH u AS ("
    "  SELECT f.fci_id, f.nombre, f.gerente, f.categoria, f.moneda, f.tipo_renta, f.plazo,"
    "         f.simbolo_primary, f.unidad, f.cafci, f.origen, ult.fecha, ult.vcp, ult.fuente"
    "  FROM mercado.fci f"
    "  JOIN mercado.fci_gerentes g ON g.gerente = f.gerente AND g.seguida"
    "  LEFT JOIN LATERAL (SELECT v.fecha, v.vcp, v.fuente FROM mercado.fci_vcp v"
    "                     WHERE v.fci_id = f.fci_id ORDER BY v.fecha DESC LIMIT 1) ult ON true"
    "  WHERE f.activo AND {filtro}"
    ") "
    "SELECT u.fci_id, u.nombre, u.gerente, u.categoria, u.moneda, u.tipo_renta, u.plazo,"
    "       u.simbolo_primary, u.unidad, u.cafci, u.origen, u.fecha, u.vcp, u.fuente,"
    "       a1.vcp, a1.fecha, awtd.vcp, amtd.vcp, aytd.vcp,"
    "       a7.vcp, a30.vcp, a90.vcp, a365.vcp "
    "FROM u "
    + _lateral("a1",   "v.fecha < u.fecha")
    + _lateral("awtd", "v.fecha < date_trunc('week',  u.fecha)::date")
    + _lateral("amtd", "v.fecha < date_trunc('month', u.fecha)::date")
    + _lateral("aytd", "v.fecha < date_trunc('year',  u.fecha)::date")
    + _lateral("a7",   "v.fecha <= u.fecha - 7")
    + _lateral("a30",  "v.fecha <= u.fecha - 30")
    + _lateral("a90",  "v.fecha <= u.fecha - 90")
    + _lateral("a365", "v.fecha <= u.fecha - 365")
    + "ORDER BY u.categoria NULLS LAST, u.gerente, u.nombre"
)


def rendimiento(hoy: float | None, ancla: float | None) -> float | None:
    """`hoy / ancla − 1`, o None si falta alguno o el ancla es 0. PURA."""
    if hoy is None or ancla is None or ancla == 0:
        return None
    return float(hoy) / float(ancla) - 1.0


def tna(r: float | None, dias: int) -> float | None:
    """Rendimiento directo de `dias` → TNA simple (la fórmula del informe). PURA."""
    if r is None or dias <= 0:
        return None
    return r * 365.0 / dias


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def _fila(r: tuple) -> dict:
    (fci_id, nombre, gerente, categoria, moneda, tipo_renta, plazo, simbolo, unidad, cafci,
     origen, fecha, vcp, fuente, v1, f1, vwtd, vmtd, vytd, v7, v30, v90, v365) = r
    vcp_f = _f(vcp)
    r7, r30 = rendimiento(vcp_f, _f(v7)), rendimiento(vcp_f, _f(v30))
    return {
        "fci_id": fci_id, "nombre": nombre, "gerente": gerente, "categoria": categoria,
        "moneda": moneda, "tipo_renta": tipo_renta, "plazo": plazo,
        "simbolo_primary": simbolo, "unidad": unidad, "cafci": cafci, "origen": origen,
        "en_tenencia": unidad is not None,
        "fecha": _iso(fecha), "vcp": vcp_f, "fuente": fuente, "fecha_1d": _iso(f1),
        "r_1d": rendimiento(vcp_f, _f(v1)),
        "r_wtd": rendimiento(vcp_f, _f(vwtd)),
        "r_mtd": rendimiento(vcp_f, _f(vmtd)),
        "r_ytd": rendimiento(vcp_f, _f(vytd)),
        "r_7d": r7, "r_30d": r30,
        "r_90d": rendimiento(vcp_f, _f(v90)),
        "r_365d": rendimiento(vcp_f, _f(v365)),
        "tna_7d": tna(r7, 7), "tna_30d": tna(r30, 30),
    }


def _orden_categoria(cat: str | None) -> tuple:
    if not cat:
        return (2, "")
    return (0, ORDEN_CATEGORIAS.index(cat)) if cat in ORDEN_CATEGORIAS else (1, cat)


@cached(ttl=120)
def tabla() -> dict:
    """Los fondos del universo con sus rendimientos + los catálogos de filtros.

    `{"fondos": [...], "categorias": [{nombre, n}], "gerentes": [{nombre, n}],
      "monedas": [{nombre, n}], "fecha_max", "n"}`
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_SQL_TABLA.format(filtro="true"))
        filas = [_fila(r) for r in cur.fetchall()]
    filas.sort(key=lambda x: (_orden_categoria(x["categoria"]), x["gerente"] or "", x["nombre"]))
    cats: dict[str | None, int] = {}
    gers: dict[str | None, int] = {}
    mons: dict[str | None, int] = {}
    for x in filas:
        cats[x["categoria"]] = cats.get(x["categoria"], 0) + 1
        gers[x["gerente"]] = gers.get(x["gerente"], 0) + 1
        mons[x["moneda"]] = mons.get(x["moneda"], 0) + 1
    fechas = [x["fecha"] for x in filas if x["fecha"]]
    return {
        "fondos": filas,
        "categorias": [{"nombre": c, "n": n} for c, n in
                       sorted(cats.items(), key=lambda kv: _orden_categoria(kv[0]))],
        "gerentes": [{"nombre": g, "n": n} for g, n in sorted(gers.items(), key=lambda kv: (-kv[1], kv[0] or ""))],
        "monedas": [{"nombre": m, "n": n} for m, n in sorted(mons.items(), key=lambda kv: kv[0] or "")],
        "fecha_max": max(fechas) if fechas else None,
        "n": len(filas),
    }


@cached(ttl=120)
def ficha(fci_id: int, dias: int = 400) -> dict | None:
    """La ficha de UN fondo: su fila + la serie de VCP (últimos `dias` días, con
    la fuente de cada punto) + el asset de Manager si está linkeado. None si no
    existe o su gerente no está seguida."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_SQL_TABLA.format(filtro="f.fci_id = %s"), (fci_id,))
        r = cur.fetchone()
        if not r:
            return None
        fila = _fila(r)
        serie: list[dict] = []
        if fila["fecha"]:
            desde = date.fromisoformat(fila["fecha"]) - timedelta(days=dias)
            cur.execute("SELECT fecha, vcp, fuente FROM mercado.fci_vcp "
                        "WHERE fci_id = %s AND fecha >= %s ORDER BY fecha", (fci_id, desde))
            serie = [{"fecha": f.isoformat(), "vcp": float(v), "fuente": fu} for f, v, fu in cur.fetchall()]
        cur.execute("SELECT fuente, count(*), min(fecha), max(fecha) FROM mercado.fci_vcp "
                    "WHERE fci_id = %s GROUP BY fuente ORDER BY fuente", (fci_id,))
        fuentes = [{"fuente": fu, "n": n, "desde": _iso(d0), "hasta": _iso(d1)} for fu, n, d0, d1 in cur.fetchall()]
        asset = None
        if fila["unidad"]:
            cur.execute("SELECT ticker, emisor, clase_activo, fee_admin, codigo_cnv, instrumento "
                        "FROM portafolio.assets WHERE unidad = %s", (fila["unidad"],))
            a = cur.fetchone()
            if a:
                asset = {"ticker": a[0], "emisor": a[1], "clase_activo": a[2],
                         "fee_admin": _f(a[3]), "codigo_cnv": a[4], "instrumento": a[5]}
    return {**fila, "serie": serie, "fuentes": fuentes, "asset": asset}

"""Service Tenencia Valorizada (cartera HD, cuentas propias 100/255/256) — SQL-NATIVE.

Lee y escribe DIRECTO sobre `portafolio.tenencia` (SQL, fuente de verdad). NO usa
Mongo: el viejo rollup `Valuaciones.TenenciaHD` quedó OBSOLETO — era un intermediario
sobre exactamente la misma data SQL. Tres operaciones:
  - tenencia_dias        → tabla izquierda: 1 fila por día, AuM HD de cada cuenta + tc.
  - tenencia_posiciones  → tabla derecha: posiciones HD por título de un día.
  - actualizar_precio_posicion → corrige el precio de una unidad/día → UPDATE en SQL,
    recalcula la valuación (cartera HD = paridad → cantidad × precio / 100).

El `tc` (MEP por día, para dolarizar) sale de la serie SQL `valuaciones.dolar`
(`core.dolar_sql`), con la misma semántica de arrastre que `get_mep_for_date`.
"""
from __future__ import annotations

import logging
from typing import Any

from api.cache import cached, invalidate
from core.postgres import get_pool

logger = logging.getLogger(__name__)

CUENTAS = ["100", "255", "256"]
# Sets de unidades por cartera del catálogo SQL portafolio.assets (IS NOT NULL para no
# romper el NOT IN del filtro ARS — un NULL en el subquery haría que NOT IN no devuelva nada).
_HD_UNITS = "SELECT unidad FROM portafolio.assets WHERE cartera = 'HD' AND unidad IS NOT NULL"
# MONEDAS (cash: ARS/USD/USDC) = cartera 'MONEDAS' → se EXCLUYEN de TODAS las vistas
# (no entran en ninguna cartera, por pedido de la mesa).
_MONEDAS_UNITS = "SELECT unidad FROM portafolio.assets WHERE cartera = 'MONEDAS' AND unidad IS NOT NULL"


def _cartera_subq(cartera: str) -> str:
    """Filtro de cartera para la tenencia valorizada (cuentas propias):
      - 'HD'  → Cartera USD: bonos hard-dollar (cartera = 'HD').
      - 'ARS' → Cartera ARS: TODO lo que NO es HD NI MONEDAS (pesos: ARS/DL/FCI/RV/…).
    Las MONEDAS (cash) se excluyen de ambas. HD ya excluye MONEDAS por definición
    (cartera='HD'); ARS = NOT HD necesita excluir MONEDAS explícito."""
    if (cartera or "").upper() == "ARS":
        return f"(unidad NOT IN ({_HD_UNITS}) AND unidad NOT IN ({_MONEDAS_UNITS}))"
    return f"unidad IN ({_HD_UNITS})"


def _tc(fecha: str) -> float | None:
    """MEP del día (ARS/USD) para dolarizar — SQL `valuaciones.dolar` (arrastra el
    último día con dato si la fecha no tiene tick)."""
    from api.services._mep import get_mep_for_date
    tc = get_mep_for_date(fecha)
    return round(tc, 2) if tc else None


def _tc_map(fechas: list[str]) -> dict[str, float | None]:
    """Igual que `_tc` pero para MUCHAS fechas en UNA query (misma semántica de
    arrastre). La serie diaria de AuM pide el TC de cada día hábil: 1 SELECT por
    fecha eran cientos de round-trips secuenciales por cada cache-miss."""
    from core import dolar_sql
    return {f: (round(tc, 2) if tc else None)
            for f, tc in dolar_sql.mep_por_fecha(fechas).items()}


@cached(ttl=300)
def tenencia_dias(cartera: str = "HD") -> dict[str, Any]:
    """Serie diaria: 1 fila por fecha con el AuM de cada cuenta + total + tc.
    Live desde SQL portafolio.tenencia (aum='si', cuentas 100/255/256), filtrado por
    `cartera`: 'HD' (Cartera USD) o 'ARS' (todo lo no-HD).

    NETEA el alquiler (por título+cuenta) date-aware: a partir de la fecha `desde`
    de cada marca, descuenta la valuación de los nominales en alquiler."""
    marcas = get_alquiler_marcas()
    por_fecha: dict[str, dict[str, float]] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT fecha, id_cuenta, unidad, SUM(valuacion), SUM(cantidad) "
            f"FROM portafolio.tenencia "
            f"WHERE aum = 'si' AND id_cuenta = ANY(%s) AND {_cartera_subq(cartera)} "
            f"GROUP BY fecha, id_cuenta, unidad ORDER BY fecha", (CUENTAS,))
        for fecha, idc, unidad, val, cant in cur.fetchall():
            f_iso = fecha.isoformat()
            c = str(idc)
            factor = _net_factor(float(cant or 0.0), marcas.get((c, str(unidad))), f_iso)
            byc = por_fecha.setdefault(f_iso, {})
            byc[c] = byc.get(c, 0.0) + float(val or 0.0) * factor
    fechas = sorted(por_fecha)
    tcs = _tc_map(fechas)
    dias = []
    for f in fechas:
        byc = por_fecha[f]
        fila = {"fecha": f, "tc": tcs.get(f), "total": round(sum(byc.values()), 2)}
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        dias.append(fila)
    return {"cuentas": CUENTAS, "cartera": (cartera or "HD").upper(), "dias": dias,
            "ultima_fecha": dias[-1]["fecha"] if dias else None}


def tenencia_posiciones(*, fecha: str, cartera: str = "HD") -> dict[str, Any]:
    """Posiciones (por título, desglose por cuenta) de un día — live desde SQL,
    filtrado por `cartera` ('HD' = Cartera USD / 'ARS' = todo lo no-HD).

    NETEA el alquiler (por título+cuenta, date-aware): descuenta los nominales en
    alquiler y su valuación. Si un título queda 100% en alquiler, no aparece.

    Adjunta el desglose en GARANTÍA (estado GAR de Aunesa) por cuenta: `gar`
    (valor) y `gar_cant` (nominales) → la vista filtra Todos / Sin GAR / Solo GAR."""
    marcas = get_alquiler_marcas()
    por_unidad: dict[str, dict[str, float]] = {}
    cant_unidad: dict[str, dict[str, float]] = {}
    gar_val_unidad: dict[str, dict[str, float]] = {}
    gar_cant_unidad: dict[str, dict[str, float]] = {}
    alq_val_unidad: dict[str, dict[str, float]] = {}
    alq_cant_unidad: dict[str, dict[str, float]] = {}
    precio_unidad: dict[str, float] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT unidad, id_cuenta, SUM(valuacion), SUM(cantidad), MAX(precio), "
            f"SUM(COALESCE(gar_cantidad, 0)) "
            f"FROM portafolio.tenencia "
            f"WHERE fecha = %s AND aum = 'si' AND id_cuenta = ANY(%s) AND {_cartera_subq(cartera)} "
            f"GROUP BY unidad, id_cuenta", (fecha, CUENTAS))
        for u, idc, val, cant, prec, gar in cur.fetchall():
            c = str(idc)
            cant_f = float(cant or 0.0)
            gross_val = float(val or 0.0)
            factor = _net_factor(cant_f, marcas.get((c, u)), fecha)  # descuenta alquiler
            net_val = gross_val * factor
            net_cant = cant_f * factor
            # Parte EN ALQUILER ese día (la que se descontó, date-aware) = 1 - factor.
            alq_frac = 1.0 - factor
            # Fracción del título en garantía (sobre el nominal crudo, clamp 0..1).
            gar_frac = min(1.0, max(0.0, float(gar or 0.0) / cant_f)) if cant_f else 0.0
            por_unidad.setdefault(u, {})[c] = net_val
            cant_unidad.setdefault(u, {})[c] = net_cant
            gar_val_unidad.setdefault(u, {})[c] = net_val * gar_frac
            gar_cant_unidad.setdefault(u, {})[c] = net_cant * gar_frac
            alq_val_unidad.setdefault(u, {})[c] = gross_val * alq_frac
            alq_cant_unidad.setdefault(u, {})[c] = cant_f * alq_frac
            if prec is not None:
                precio_unidad[u] = float(prec)

    posiciones = []
    total_gar = 0.0
    total_alq = 0.0
    for u in sorted(por_unidad, key=lambda x: -sum(por_unidad[x].values())):
        byc = por_unidad[u]
        cantc = cant_unidad.get(u, {})
        garv = gar_val_unidad.get(u, {})
        garc = gar_cant_unidad.get(u, {})
        alqv = alq_val_unidad.get(u, {})
        alqc = alq_cant_unidad.get(u, {})
        total_cant = round(sum(cantc.values()), 4)
        alq_total = round(sum(alqv.values()), 2)
        alq_total_cant = round(sum(alqc.values()), 4)
        # Fuera de la vista solo si NO queda posición NI hay parte en alquiler
        # (un título 100% en alquiler igual aparece para el filtro/marca ALQUILER).
        if total_cant == 0 and alq_total_cant == 0:
            continue
        gar_total = round(sum(garv.values()), 2)
        total_gar += gar_total
        total_alq += alq_total
        fila = {
            "unidad": u,
            "total": round(sum(byc.values()), 2),
            "precio": round(precio_unidad[u], 4) if u in precio_unidad else None,
            "cant": {c: round(cantc.get(c, 0.0), 4) for c in CUENTAS},
            "total_cant": total_cant,
            "gar": {c: round(garv.get(c, 0.0), 2) for c in CUENTAS},
            "gar_cant": {c: round(garc.get(c, 0.0), 4) for c in CUENTAS},
            "gar_total": gar_total,
            "gar_total_cant": round(sum(garc.values()), 4),
            "en_alquiler": alq_total_cant > 0,
            "alq_total": alq_total,
            "alq_total_cant": alq_total_cant,
            "alq": {c: round(alqv.get(c, 0.0), 2) for c in CUENTAS},
            "alq_cant": {c: round(alqc.get(c, 0.0), 4) for c in CUENTAS},
        }
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        posiciones.append(fila)
    total = round(sum(p["total"] for p in posiciones), 2)
    return {"fecha": fecha, "cuentas": CUENTAS, "cartera": (cartera or "HD").upper(),
            "tc": _tc(fecha), "total": total, "total_gar": round(total_gar, 2),
            "total_alq": round(total_alq, 2), "posiciones": posiciones}


# ── ALQUILER (marca durable por título + cuenta, self-service) ───────────────
# Por (id_cuenta, unidad): SI/NO, nominales en alquiler y fecha DESDE. Durable
# (no por día). Se edita en la vista "Títulos en alquiler". NETEA la posición en
# las vistas de tenencia (posiciones + AuM diario) a partir de `desde`. Tabla
# self-create para tolerar el drift de schema.
_ALQUILER_TABLE = "portafolio.alquiler"


def _ensure_alquiler_table(cur) -> None:
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_ALQUILER_TABLE} ("
        "id_cuenta text NOT NULL, unidad text NOT NULL, "
        "en_alquiler boolean DEFAULT false, cantidad numeric, desde date, hasta date, "
        "updated_by text, updated_at timestamptz, "
        "PRIMARY KEY (id_cuenta, unidad))")
    # `hasta` se agregó después (período del alquiler) → ALTER para tablas viejas.
    cur.execute(f"ALTER TABLE {_ALQUILER_TABLE} ADD COLUMN IF NOT EXISTS hasta date")


def get_alquiler_marcas() -> dict[tuple[str, str], dict[str, Any]]:
    """{(id_cuenta, unidad): {cantidad, desde}} de las marcas ACTIVAS (en_alquiler
    y cantidad > 0). `desde` es date|None. Vacío si la tabla no existe / PG caído."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            _ensure_alquiler_table(cur)
            cur.execute(
                f"SELECT id_cuenta, unidad, cantidad, desde, hasta FROM {_ALQUILER_TABLE} "
                f"WHERE en_alquiler = true AND cantidad > 0")
            for idc, u, cant, desde, hasta in cur.fetchall():
                if idc and u and cant:
                    out[(str(idc), str(u))] = {"cantidad": float(cant), "desde": desde,
                                               "hasta": hasta}
            conn.commit()
    except Exception:
        return {}
    return out


def _net_factor(cant: float, marca: dict[str, Any] | None, fecha: str) -> float:
    """Fracción de la posición que QUEDA tras descontar el alquiler (0..1).
    Aplica solo si hay marca activa y `fecha` está DENTRO del período [desde, hasta]
    (date-aware). `desde` vacío = desde siempre; `hasta` vacío = sigue en alquiler.
    Fechas en ISO YYYY-MM-DD → comparación lexicográfica == comparación de fechas."""
    if not marca or cant <= 0:
        return 1.0
    desde = marca.get("desde")
    if desde is not None and str(desde) > fecha:
        return 1.0   # el alquiler todavía no arrancó ese día
    hasta = marca.get("hasta")
    if hasta is not None and str(hasta) < fecha:
        return 1.0   # el alquiler ya terminó ese día
    lent = min(float(marca["cantidad"]), cant)
    return max(0.0, (cant - lent) / cant)


# Arranque del proceso legal: se listan los títulos tenidos DESDE esta fecha, aunque
# hoy ya no estén en posición (hay que poder marcarlos igual).
_ALQUILER_DESDE_DEFAULT = "2026-06-01"


def titulos_en_alquiler(*, desde: str | None = None) -> dict[str, Any]:
    """TODOS los (título, cuenta) de 100/255/256 que aparecieron en tenencia entre
    `desde` (default 01/06/2026, arranque del proceso legal) y HOY + su marca de
    alquiler (SI/NO, cantidad, desde, hasta). Un título tenido el 01/06 pero NO hoy
    IGUAL aparece (para poder marcarlo). Los valores (cantidad/precio/valuación) son
    los del ÚLTIMO día en que se lo tuvo dentro del rango. Alimenta la vista de
    edición 'Títulos en alquiler'."""
    desde = (desde or _ALQUILER_DESDE_DEFAULT)[:10]
    marcas_raw: dict[tuple[str, str], dict[str, Any]] = {}
    posiciones: list[dict[str, Any]] = []
    ultima = None
    # Marcas: BEST-EFFORT — si la tabla de alquiler no se puede crear/leer (permiso,
    # PG caído), seguimos SIN marcas. Las posiciones NO dependen de esa tabla y son
    # lo importante (antes un fallo acá dejaba la vista entera vacía).
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            _ensure_alquiler_table(cur)
            cur.execute(
                f"SELECT id_cuenta, unidad, en_alquiler, cantidad, desde, hasta FROM {_ALQUILER_TABLE}")
            for idc, u, en, cant, d, h in cur.fetchall():
                marcas_raw[(str(idc), str(u))] = {
                    "en_alquiler": bool(en),
                    "cantidad": float(cant) if cant is not None else None,
                    "desde": d.isoformat() if d else None,
                    "hasta": h.isoformat() if h else None,
                }
            conn.commit()
    except Exception as e:
        logger.warning("titulos_en_alquiler: marcas no disponibles (%s)", e)
        marcas_raw = {}

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia "
                    "WHERE aum = 'si' AND id_cuenta = ANY(%s)", (CUENTAS,))
        row = cur.fetchone()
        ultima = row[0] if row and row[0] else None

        # Unión de (cuenta, unidad) del rango [desde, hoy] con los valores del ÚLTIMO
        # día tenido: se agrega por (fecha, cuenta, unidad) y en Python nos quedamos
        # con la fila de mayor fecha por (cuenta, unidad) (ORDER BY fecha DESC).
        cur.execute(
            "SELECT id_cuenta, unidad, cartera, fecha, SUM(cantidad), MAX(precio), SUM(valuacion) "
            "FROM portafolio.tenencia "
            "WHERE fecha >= %s AND aum = 'si' AND id_cuenta = ANY(%s) "
            "GROUP BY id_cuenta, unidad, cartera, fecha HAVING SUM(cantidad) <> 0 "
            "ORDER BY id_cuenta, unidad, fecha DESC", (desde, CUENTAS))
        visto: set[tuple[str, str]] = set()
        for idc, u, cart, f, cant, prec, val in cur.fetchall():
            key = (str(idc), str(u))
            if key in visto:
                continue   # ya tomamos el día más reciente de este (cuenta, unidad)
            visto.add(key)
            m = marcas_raw.get(key) or {}
            cant_f = round(float(cant or 0.0), 4)
            val_f = round(float(val or 0.0), 2)
            alq_cant = m.get("cantidad")
            alq_valor = (round(val_f * min(alq_cant, cant_f) / cant_f, 2)
                         if (alq_cant and cant_f) else None)
            posiciones.append({
                "id_cuenta":     str(idc),
                "unidad":        u,
                "cartera":       cart,
                "cantidad":      cant_f,
                "precio":        round(float(prec), 4) if prec is not None else None,
                "valuacion":     val_f,
                "ultimo_dia":    f.isoformat() if f else None,  # último día tenido en el rango
                "en_alquiler":   m.get("en_alquiler", False),
                "alq_cant":      alq_cant,
                "alq_valor":     alq_valor,
                "desde":         m.get("desde"),
                "hasta":         m.get("hasta"),
            })
        posiciones.sort(key=lambda p: (p["unidad"], p["id_cuenta"]))
    return {"ultima_fecha": ultima.isoformat() if ultima else None,
            "desde": desde, "cuentas": CUENTAS, "posiciones": posiciones}


def set_alquiler_marca(*, id_cuenta: str, unidad: str, en_alquiler: bool,
                       cantidad: float | None, desde: str | None,
                       hasta: str | None = None, email: str) -> dict[str, Any]:
    """Upsert de la marca de alquiler de un (título, cuenta). en_alquiler=False o
    cantidad<=0 → apaga la marca. `hasta` vacío = sigue en alquiler. Devuelve el
    estado guardado."""
    from datetime import UTC, datetime
    from datetime import date as _date
    idc = (id_cuenta or "").strip()
    u = (unidad or "").strip()
    if not idc or not u:
        return {"ok": False, "error": "id_cuenta y unidad son obligatorios"}
    cant = None
    if cantidad is not None:
        try:
            cant = float(cantidad)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"cantidad inválida: {cantidad!r}"}

    def _parse(v: str | None, campo: str):
        if not v:
            return None, None
        try:
            return _date.fromisoformat(v[:10]), None
        except ValueError:
            return None, {"ok": False, "error": f"{campo} inválida: {v!r}"}

    d, err = _parse(desde, "fecha desde")
    if err:
        return err
    h, err = _parse(hasta, "fecha hasta")
    if err:
        return err
    if d is not None and h is not None and h < d:
        return {"ok": False, "error": "la fecha hasta no puede ser anterior a la desde"}
    activo = bool(en_alquiler) and (cant or 0) > 0

    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure_alquiler_table(cur)
        cur.execute(
            f"INSERT INTO {_ALQUILER_TABLE} "
            f"(id_cuenta, unidad, en_alquiler, cantidad, desde, hasta, updated_by, updated_at) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id_cuenta, unidad) DO UPDATE SET "
            f"en_alquiler = EXCLUDED.en_alquiler, cantidad = EXCLUDED.cantidad, "
            f"desde = EXCLUDED.desde, hasta = EXCLUDED.hasta, "
            f"updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at",
            (idc, u, activo, cant, d, h, email, datetime.now(UTC)))
        conn.commit()
    invalidate("tenencia_dias")   # el netting cambió → refrescar la serie de AuM
    return {"ok": True, "id_cuenta": idc, "unidad": u, "en_alquiler": activo,
            "cantidad": cant, "desde": d.isoformat() if d else None,
            "hasta": h.isoformat() if h else None}


# ── PORTFOLIO ALQUILER (lista curada de títulos — tab propia) ────────────────
# Pedido de la mesa 2026-07-14: en vez de operar el netting por marcas, una
# tabla tipo Tenencia Valorizada pero SOLO con los títulos que el back office
# ELIGE a mano (fila "+" con buscador sobre todos los instrumentos). La
# selección es DURABLE y compartida (tabla SQL self-create, como alquiler);
# los valores salen de portafolio.tenencia EN BRUTO (sin netear marcas).
_PORTFOLIO_ALQ_TABLE = "portafolio.alquiler_portfolio"


def _ensure_portfolio_alq_table(cur) -> None:
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_PORTFOLIO_ALQ_TABLE} ("
        "unidad text PRIMARY KEY, updated_by text, updated_at timestamptz)")


def portfolio_alquiler_unidades() -> list[str]:
    """Títulos elegidos para la tab PORTFOLIO ALQUILER (orden alfabético).
    Vacío si la tabla no existe / PG caído — la vista muestra el estado vacío."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            _ensure_portfolio_alq_table(cur)
            cur.execute(f"SELECT unidad FROM {_PORTFOLIO_ALQ_TABLE} ORDER BY unidad")
            out = [r[0] for r in cur.fetchall()]
            conn.commit()
            return out
    except Exception as e:
        logger.warning("portfolio_alquiler: unidades no disponibles (%s)", e)
        return []


def portfolio_alquiler_dias() -> dict[str, Any]:
    """Serie diaria de la tab PORTFOLIO ALQUILER: 1 fila por fecha con la
    valuación de los títulos ELEGIDOS por cuenta propia + total + tc. Valuación
    EN BRUTO (sin netear marcas de alquiler): acá se mira el portfolio elegido."""
    unidades = portfolio_alquiler_unidades()
    if not unidades:
        return {"cuentas": CUENTAS, "unidades": [], "dias": [], "ultima_fecha": None}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fecha, id_cuenta, SUM(valuacion) FROM portafolio.tenencia "
            "WHERE aum = 'si' AND id_cuenta = ANY(%s) AND unidad = ANY(%s) "
            "GROUP BY fecha, id_cuenta ORDER BY fecha", (CUENTAS, unidades))
        filas = cur.fetchall()
    por_fecha: dict[str, dict[str, float]] = {}
    for fecha, idc, val in filas:
        por_fecha.setdefault(fecha.isoformat(), {})[str(idc)] = float(val or 0.0)
    fechas = sorted(por_fecha)
    tcs = _tc_map(fechas)
    dias = []
    for f in fechas:
        byc = por_fecha[f]
        fila = {"fecha": f, "tc": tcs.get(f), "total": round(sum(byc.values()), 2)}
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        dias.append(fila)
    return {"cuentas": CUENTAS, "unidades": unidades, "dias": dias,
            "ultima_fecha": dias[-1]["fecha"] if dias else None}


def portfolio_alquiler_posiciones(*, fecha: str) -> dict[str, Any]:
    """Posiciones del día SOLO de los títulos elegidos (desglose por cuenta,
    mismas columnas que Tenencia Valorizada: PX · 100 · 255 · 256 · Total).
    Un título elegido SIN posición ese día igual aparece (fila en cero) — la
    vista lo muestra con '—' y deja quitarlo."""
    unidades = portfolio_alquiler_unidades()
    por_unidad: dict[str, dict[str, float]] = {}
    cant_unidad: dict[str, dict[str, float]] = {}
    precio_unidad: dict[str, float] = {}
    if unidades:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT unidad, id_cuenta, SUM(valuacion), SUM(cantidad), MAX(precio) "
                "FROM portafolio.tenencia "
                "WHERE fecha = %s AND aum = 'si' AND id_cuenta = ANY(%s) AND unidad = ANY(%s) "
                "GROUP BY unidad, id_cuenta", (fecha, CUENTAS, unidades))
            for u, idc, val, cant, prec in cur.fetchall():
                c = str(idc)
                por_unidad.setdefault(u, {})[c] = float(val or 0.0)
                cant_unidad.setdefault(u, {})[c] = float(cant or 0.0)
                if prec is not None:
                    precio_unidad[u] = float(prec)
    posiciones = []
    for u in unidades:
        byc = por_unidad.get(u, {})
        cantc = cant_unidad.get(u, {})
        fila = {
            "unidad": u,
            "precio": round(precio_unidad[u], 4) if u in precio_unidad else None,
            "total": round(sum(byc.values()), 2),
            "total_cant": round(sum(cantc.values()), 4),
            "cant": {c: round(cantc.get(c, 0.0), 4) for c in CUENTAS},
        }
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        posiciones.append(fila)
    posiciones.sort(key=lambda p: -p["total"])
    total = round(sum(p["total"] for p in posiciones), 2)
    return {"fecha": fecha, "cuentas": CUENTAS, "tc": _tc(fecha),
            "total": total, "posiciones": posiciones}


@cached(ttl=300)
def portfolio_alquiler_instrumentos() -> list[str]:
    """Catálogo para el buscador del '+': todos los instrumentos conocidos —
    el catálogo (portafolio.assets) ∪ lo que alguna vez apareció en la tenencia
    de las cuentas propias (por si un título no tiene fila en assets)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT unidad FROM portafolio.assets WHERE unidad IS NOT NULL "
            "UNION "
            "SELECT DISTINCT unidad FROM portafolio.tenencia "
            "WHERE id_cuenta = ANY(%s) AND unidad IS NOT NULL "
            "ORDER BY 1", (CUENTAS,))
        return [r[0] for r in cur.fetchall()]


def set_portfolio_alquiler(*, unidad: str, en_portfolio: bool, email: str) -> dict[str, Any]:
    """Agrega (True) o quita (False) un título de la tab PORTFOLIO ALQUILER.
    Idempotente: agregar dos veces no duplica; quitar lo ausente no falla."""
    from datetime import UTC, datetime
    u = (unidad or "").strip()
    if not u:
        return {"ok": False, "error": "unidad vacía"}
    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure_portfolio_alq_table(cur)
        if en_portfolio:
            cur.execute(
                f"INSERT INTO {_PORTFOLIO_ALQ_TABLE} (unidad, updated_by, updated_at) "
                f"VALUES (%s, %s, %s) ON CONFLICT (unidad) DO NOTHING",
                (u, email, datetime.now(UTC)))
        else:
            cur.execute(f"DELETE FROM {_PORTFOLIO_ALQ_TABLE} WHERE unidad = %s", (u,))
        conn.commit()
    return {"ok": True, "unidad": u, "en_portfolio": bool(en_portfolio)}


def actualizar_precio_posicion(*, fecha: str, unidad: str, precio: float,
                               dividir_100: bool = True, cartera: str = "HD") -> dict[str, Any]:
    """Corrige a mano el PRECIO de una unidad en un día → recalcula la valuación de
    las 3 cuentas, DIRECTO en SQL `portafolio.tenencia`. Devuelve la nueva valuación
    por cuenta + el total del día.

    `dividir_100` (default True): cartera HD cotiza en PARIDAD → valuación =
    cantidad × precio / 100. Si el título cotiza en valor PLENO (no paridad), pasar
    False → valuación = cantidad × precio."""
    try:
        precio = float(precio)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"precio inválido: {precio!r}"}

    divisor = 100.0 if dividir_100 else 1.0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE portafolio.tenencia "
            "SET precio = %s, valuacion = ROUND((cantidad * %s / %s)::numeric, 2) "
            "WHERE fecha = %s AND unidad = %s AND id_cuenta = ANY(%s) AND aum = 'si'",
            (round(precio, 4), precio, divisor, fecha, unidad, CUENTAS))
        n = cur.rowcount
        if not n:
            conn.rollback()
            return {"ok": False, "error": f"unidad {unidad!r} no está en {fecha} (o sin cantidad)"}
        conn.commit()
        cur.execute(
            "SELECT id_cuenta, SUM(valuacion) FROM portafolio.tenencia "
            "WHERE fecha = %s AND unidad = %s AND id_cuenta = ANY(%s) GROUP BY id_cuenta",
            (fecha, unidad, CUENTAS))
        nuevas = {str(idc): round(float(v or 0.0), 2) for idc, v in cur.fetchall()}
        cur.execute(
            f"SELECT SUM(valuacion) FROM portafolio.tenencia "
            f"WHERE fecha = %s AND aum = 'si' AND id_cuenta = ANY(%s) AND {_cartera_subq(cartera)}",
            (fecha, CUENTAS))
        total = round(float(cur.fetchone()[0] or 0.0), 2)

    invalidate("tenencia_dias")   # el total/aum del día cambió → refrescar tabla izquierda
    return {"ok": True, "fecha": fecha, "unidad": unidad, "precio": round(precio, 4),
            "valuaciones": {c: nuevas.get(c, 0.0) for c in CUENTAS}, "total_dia": total}

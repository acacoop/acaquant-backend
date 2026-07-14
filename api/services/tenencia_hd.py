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
    """Serie diaria: 1 fila por fecha con el AuM BRUTO de cada cuenta + total + tc.
    Live desde SQL portafolio.tenencia (aum='si', cuentas 100/255/256), filtrado por
    `cartera`: 'HD' (Cartera USD) o 'ARS' (todo lo no-HD).

    Desde 2026-07-14 la serie es BRUTA: el alquiler ya NO se descuenta acá (el
    netting por marcas se retiró de esta vista). El descuento vive en el filtro
    SIN ALQUILER de posiciones, alimentado por el PORTFOLIO ALQUILER."""
    por_fecha: dict[str, dict[str, float]] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT fecha, id_cuenta, SUM(valuacion) "
            f"FROM portafolio.tenencia "
            f"WHERE aum = 'si' AND id_cuenta = ANY(%s) AND {_cartera_subq(cartera)} "
            f"GROUP BY fecha, id_cuenta ORDER BY fecha", (CUENTAS,))
        for fecha, idc, val in cur.fetchall():
            por_fecha.setdefault(fecha.isoformat(), {})[str(idc)] = float(val or 0.0)
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

    Los valores son BRUTOS (desde 2026-07-14 el netting por marcas se retiró).
    Adjunta por título la parte EN ALQUILER según los nominales cargados en el
    PORTFOLIO ALQUILER (carry-forward por día): la vista los usa para el filtro
    SIN ALQUILER (base − alquiler — puede dar NEGATIVO si se alquiló más de lo
    que hay en cartera ese día).

    Adjunta el desglose en GARANTÍA (estado GAR de Aunesa) por cuenta: `gar`
    (valor) y `gar_cant` (nominales) → la vista filtra Todos / Sin GAR / Solo GAR."""
    pa_unidades = set(portfolio_alquiler_unidades())
    overrides = _portfolio_alq_overrides() if pa_unidades else {}
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
            # Parte EN ALQUILER = nominales cargados en el Portfolio Alquiler ese
            # día, valuados al precio unitario implícito de la tenencia.
            alq_cant = (_alq_efectivo(overrides.get((u, c)), fecha)
                        if u in pa_unidades else 0.0)
            unit = (gross_val / cant_f) if cant_f else None
            alq_val = alq_cant * unit if unit is not None else 0.0
            # Fracción del título en garantía (sobre el nominal crudo, clamp 0..1).
            gar_frac = min(1.0, max(0.0, float(gar or 0.0) / cant_f)) if cant_f else 0.0
            por_unidad.setdefault(u, {})[c] = gross_val
            cant_unidad.setdefault(u, {})[c] = cant_f
            gar_val_unidad.setdefault(u, {})[c] = gross_val * gar_frac
            gar_cant_unidad.setdefault(u, {})[c] = cant_f * gar_frac
            alq_val_unidad.setdefault(u, {})[c] = alq_val
            alq_cant_unidad.setdefault(u, {})[c] = alq_cant
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
# (no por día). Se edita en la sub-tab "Marcas por cuenta". Desde 2026-07-14 es
# SOLO un registro (ya NO netea Tenencia Valorizada — eso lo hace el PORTFOLIO
# ALQUILER vía el filtro SIN ALQUILER). Tabla self-create.
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
# ELIGE a mano (fila "+" con buscador sobre todos los instrumentos) y con los
# NOMINALES EN ALQUILER editables POR CUENTA Y POR DÍA. Semántica carry-forward:
# el nominal cargado un día RIGE de ese día en adelante hasta la próxima edición
# (como un alquiler real — no se re-tipea cada día). La serie arranca en el
# inicio del proceso legal (01/06/2026). Es la fuente del filtro SIN ALQUILER
# de Tenencia Valorizada. Tablas self-create + en schema.sql.
_PORTFOLIO_ALQ_TABLE = "portafolio.alquiler_portfolio"
_PORTFOLIO_ALQ_NOM_TABLE = "portafolio.alquiler_portfolio_nominales"
_PORTFOLIO_ALQ_DESDE = "2026-06-01"   # arranque del proceso legal


def _ensure_portfolio_alq_table(cur) -> None:
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_PORTFOLIO_ALQ_TABLE} ("
        "unidad text PRIMARY KEY, updated_by text, updated_at timestamptz)")


def _ensure_portfolio_alq_nom_table(cur) -> None:
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_PORTFOLIO_ALQ_NOM_TABLE} ("
        "unidad text NOT NULL, id_cuenta text NOT NULL, fecha date NOT NULL, "
        "cantidad numeric NOT NULL, updated_by text, updated_at timestamptz, "
        "PRIMARY KEY (unidad, id_cuenta, fecha))")


def _portfolio_alq_overrides() -> dict[tuple[str, str], list[tuple[str, float]]]:
    """{(unidad, id_cuenta): [(fecha_iso, cantidad), ...] ASC} — las ediciones de
    nominales. Carry-forward: cada una rige desde su fecha hasta la siguiente."""
    out: dict[tuple[str, str], list[tuple[str, float]]] = {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            _ensure_portfolio_alq_nom_table(cur)
            cur.execute(
                f"SELECT unidad, id_cuenta, fecha, cantidad "
                f"FROM {_PORTFOLIO_ALQ_NOM_TABLE} ORDER BY fecha")
            for u, c, f, cant in cur.fetchall():
                out.setdefault((str(u), str(c)), []).append(
                    (f.isoformat(), float(cant or 0.0)))
            conn.commit()
    except Exception as e:
        logger.warning("portfolio_alquiler: nominales no disponibles (%s)", e)
        return {}
    return out


def _alq_efectivo(ediciones: list[tuple[str, float]] | None, fecha: str) -> float:
    """Nominales en alquiler EFECTIVOS el día `fecha`: la última edición con
    fecha <= `fecha` manda (carry-forward). Sin ediciones previas → 0."""
    if not ediciones:
        return 0.0
    cant = 0.0
    for f, c in ediciones:
        if f <= fecha:
            cant = c
        else:
            break
    return cant


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
    """Serie diaria de la tab PORTFOLIO ALQUILER (desde 01/06/2026): 1 fila por
    fecha con la valuación de los NOMINALES EN ALQUILER cargados, por cuenta.
    Precio unitario del día = valuación/cantidad de la tenencia de esa unidad
    (a nivel día, sumando las 3 cuentas); un título sin tenencia ese día no
    valúa (nominales sin precio)."""
    unidades = portfolio_alquiler_unidades()
    if not unidades:
        return {"cuentas": CUENTAS, "unidades": [], "dias": [], "ultima_fecha": None}
    ediciones = _portfolio_alq_overrides()
    precio_unit: dict[tuple[str, str], float] = {}
    fechas_set: set[str] = set()
    with get_pool().connection() as conn, conn.cursor() as cur:
        # Calendario (todas las fechas con tenencia de las cuentas propias desde
        # el arranque) + precio unitario por (fecha, unidad) de los elegidos.
        cur.execute(
            "SELECT DISTINCT fecha FROM portafolio.tenencia "
            "WHERE fecha >= %s AND aum = 'si' AND id_cuenta = ANY(%s)",
            (_PORTFOLIO_ALQ_DESDE, CUENTAS))
        fechas_set = {r[0].isoformat() for r in cur.fetchall()}
        cur.execute(
            "SELECT fecha, unidad, SUM(valuacion), SUM(cantidad) FROM portafolio.tenencia "
            "WHERE fecha >= %s AND aum = 'si' AND id_cuenta = ANY(%s) AND unidad = ANY(%s) "
            "GROUP BY fecha, unidad", (_PORTFOLIO_ALQ_DESDE, CUENTAS, unidades))
        for f, u, val, cant in cur.fetchall():
            if cant:
                precio_unit[(f.isoformat(), u)] = float(val or 0.0) / float(cant)
    fechas = sorted(fechas_set)
    tcs = _tc_map(fechas)
    dias = []
    for f in fechas:
        byc: dict[str, float] = {}
        for u in unidades:
            unit = precio_unit.get((f, u))
            if unit is None:
                continue
            for c in CUENTAS:
                cant_alq = _alq_efectivo(ediciones.get((u, c)), f)
                if cant_alq:
                    byc[c] = byc.get(c, 0.0) + cant_alq * unit
        fila = {"fecha": f, "tc": tcs.get(f), "total": round(sum(byc.values()), 2)}
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        dias.append(fila)
    return {"cuentas": CUENTAS, "unidades": unidades, "dias": dias,
            "ultima_fecha": dias[-1]["fecha"] if dias else None}


def portfolio_alquiler_posiciones(*, fecha: str) -> dict[str, Any]:
    """Posiciones del día de la tab PORTFOLIO ALQUILER: por título elegido, los
    NOMINALES EN ALQUILER por cuenta (efectivos ese día, carry-forward) y su
    valuación al precio de la tenencia. `ten_cant` = nominales EN CARTERA ese
    día (referencia para cargar). Un título sin tenencia ese día aparece igual
    (sin precio) para poder cargarle nominales o quitarlo."""
    unidades = portfolio_alquiler_unidades()
    ediciones = _portfolio_alq_overrides()
    ten_val: dict[str, dict[str, float]] = {}
    ten_cant: dict[str, dict[str, float]] = {}
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
                ten_val.setdefault(u, {})[c] = float(val or 0.0)
                ten_cant.setdefault(u, {})[c] = float(cant or 0.0)
                if prec is not None:
                    precio_unidad[u] = float(prec)
    posiciones = []
    for u in unidades:
        tv, tcnt = ten_val.get(u, {}), ten_cant.get(u, {})
        tot_val_ten = sum(tv.values())
        tot_cant_ten = sum(tcnt.values())
        unit = (tot_val_ten / tot_cant_ten) if tot_cant_ten else None
        cant_alq = {c: _alq_efectivo(ediciones.get((u, c)), fecha) for c in CUENTAS}
        val_alq = {c: (cant_alq[c] * unit if unit is not None else 0.0) for c in CUENTAS}
        fila = {
            "unidad": u,
            "precio": round(precio_unidad[u], 4) if u in precio_unidad else None,
            "cant": {c: round(cant_alq[c], 4) for c in CUENTAS},          # editables
            "ten_cant": {c: round(tcnt.get(c, 0.0), 4) for c in CUENTAS},  # referencia
            "total": round(sum(val_alq.values()), 2),
            "total_cant": round(sum(cant_alq.values()), 4),
            "sin_precio": unit is None,
        }
        fila.update({c: round(val_alq[c], 2) for c in CUENTAS})
        posiciones.append(fila)
    posiciones.sort(key=lambda p: (-p["total"], p["unidad"]))
    total = round(sum(p["total"] for p in posiciones), 2)
    return {"fecha": fecha, "cuentas": CUENTAS, "tc": _tc(fecha),
            "total": total, "posiciones": posiciones}


def set_portfolio_alquiler_nominal(*, unidad: str, id_cuenta: str, fecha: str,
                                   cantidad: float | None, email: str) -> dict[str, Any]:
    """Edita los nominales en alquiler de (título, cuenta) A PARTIR de `fecha`
    (carry-forward: rige hasta la próxima edición). cantidad None = borra la
    edición de ese día exacto (vuelve a regir la anterior); 0 = apaga de ese
    día en adelante."""
    from datetime import UTC, datetime
    from datetime import date as _date
    u, c = (unidad or "").strip(), (id_cuenta or "").strip()
    if not u or not c or c not in CUENTAS:
        return {"ok": False, "error": "unidad y cuenta (100/255/256) son obligatorias"}
    try:
        f = _date.fromisoformat((fecha or "")[:10])
    except ValueError:
        return {"ok": False, "error": f"fecha inválida: {fecha!r}"}
    cant = None
    if cantidad is not None:
        try:
            cant = float(cantidad)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"cantidad inválida: {cantidad!r}"}
        if cant < 0:
            return {"ok": False, "error": "la cantidad no puede ser negativa"}
    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure_portfolio_alq_nom_table(cur)
        if cant is None:
            cur.execute(
                f"DELETE FROM {_PORTFOLIO_ALQ_NOM_TABLE} "
                f"WHERE unidad = %s AND id_cuenta = %s AND fecha = %s", (u, c, f))
        else:
            cur.execute(
                f"INSERT INTO {_PORTFOLIO_ALQ_NOM_TABLE} "
                f"(unidad, id_cuenta, fecha, cantidad, updated_by, updated_at) "
                f"VALUES (%s,%s,%s,%s,%s,%s) "
                f"ON CONFLICT (unidad, id_cuenta, fecha) DO UPDATE SET "
                f"cantidad = EXCLUDED.cantidad, updated_by = EXCLUDED.updated_by, "
                f"updated_at = EXCLUDED.updated_at",
                (u, c, f, cant, email, datetime.now(UTC)))
        conn.commit()
    return {"ok": True, "unidad": u, "id_cuenta": c, "fecha": f.isoformat(),
            "cantidad": cant}


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

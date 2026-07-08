"""Service Tenencia Valorizada (cartera HD, cuentas propias 100/255/256) — SQL-NATIVE.

Lee y escribe DIRECTO sobre `portafolio.tenencia` (SQL, fuente de verdad). NO usa
Mongo: el viejo rollup `Valuaciones.TenenciaHD` quedó OBSOLETO — era un intermediario
sobre exactamente la misma data SQL. Tres operaciones:
  - tenencia_dias        → tabla izquierda: 1 fila por día, AuM HD de cada cuenta + tc.
  - tenencia_posiciones  → tabla derecha: posiciones HD por título de un día.
  - actualizar_precio_posicion → corrige el precio de una unidad/día → UPDATE en SQL,
    recalcula la valuación (cartera HD = paridad → cantidad × precio / 100).

El `tc` (MEP por día, para dolarizar) sale de `get_mep_for_date` — es la ÚNICA lectura
que todavía toca Mongo (Valuaciones.Dolar); la serie MEP se migra por separado.
"""
from __future__ import annotations

from typing import Any

from api.cache import cached, invalidate
from core.postgres import get_pool

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
    """MEP del día (ARS/USD) para dolarizar. Única lectura que aún toca Mongo
    (Valuaciones.Dolar vía get_mep_for_date); la serie MEP se migra aparte."""
    from api.services._mep import get_mep_for_date
    tc = get_mep_for_date(fecha)
    return round(tc, 2) if tc else None


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
    dias = []
    for f in sorted(por_fecha):
        byc = por_fecha[f]
        fila = {"fecha": f, "tc": _tc(f), "total": round(sum(byc.values()), 2)}
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        dias.append(fila)
    return {"cuentas": CUENTAS, "cartera": (cartera or "HD").upper(), "dias": dias,
            "ultima_fecha": dias[-1]["fecha"] if dias else None}


def tenencia_posiciones(*, fecha: str, cartera: str = "HD") -> dict[str, Any]:
    """Posiciones (por título, desglose por cuenta) de un día — live desde SQL,
    filtrado por `cartera` ('HD' = Cartera USD / 'ARS' = todo lo no-HD).

    NETEA el alquiler (por título+cuenta, date-aware): descuenta los nominales en
    alquiler y su valuación. Si un título queda 100% en alquiler, no aparece."""
    marcas = get_alquiler_marcas()
    por_unidad: dict[str, dict[str, float]] = {}
    cant_unidad: dict[str, dict[str, float]] = {}
    precio_unidad: dict[str, float] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT unidad, id_cuenta, SUM(valuacion), SUM(cantidad), MAX(precio) "
            f"FROM portafolio.tenencia "
            f"WHERE fecha = %s AND aum = 'si' AND id_cuenta = ANY(%s) AND {_cartera_subq(cartera)} "
            f"GROUP BY unidad, id_cuenta", (fecha, CUENTAS))
        for u, idc, val, cant, prec in cur.fetchall():
            c = str(idc)
            cant_f = float(cant or 0.0)
            factor = _net_factor(cant_f, marcas.get((c, u)), fecha)  # descuenta alquiler
            por_unidad.setdefault(u, {})[c] = float(val or 0.0) * factor
            cant_unidad.setdefault(u, {})[c] = cant_f * factor
            if prec is not None:
                precio_unidad[u] = float(prec)

    posiciones = []
    for u in sorted(por_unidad, key=lambda x: -sum(por_unidad[x].values())):
        byc = por_unidad[u]
        cantc = cant_unidad.get(u, {})
        total_cant = round(sum(cantc.values()), 4)
        if total_cant == 0:
            continue   # título 100% en alquiler → fuera de la vista principal
        fila = {
            "unidad": u,
            "total": round(sum(byc.values()), 2),
            "precio": round(precio_unidad[u], 4) if u in precio_unidad else None,
            "cant": {c: round(cantc.get(c, 0.0), 4) for c in CUENTAS},
            "total_cant": total_cant,
        }
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        posiciones.append(fila)
    total = round(sum(p["total"] for p in posiciones), 2)
    return {"fecha": fecha, "cuentas": CUENTAS, "cartera": (cartera or "HD").upper(),
            "tc": _tc(fecha), "total": total, "posiciones": posiciones}


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
        "en_alquiler boolean DEFAULT false, cantidad numeric, desde date, "
        "updated_by text, updated_at timestamptz, "
        "PRIMARY KEY (id_cuenta, unidad))")


def get_alquiler_marcas() -> dict[tuple[str, str], dict[str, Any]]:
    """{(id_cuenta, unidad): {cantidad, desde}} de las marcas ACTIVAS (en_alquiler
    y cantidad > 0). `desde` es date|None. Vacío si la tabla no existe / PG caído."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            _ensure_alquiler_table(cur)
            cur.execute(
                f"SELECT id_cuenta, unidad, cantidad, desde FROM {_ALQUILER_TABLE} "
                f"WHERE en_alquiler = true AND cantidad > 0")
            for idc, u, cant, desde in cur.fetchall():
                if idc and u and cant:
                    out[(str(idc), str(u))] = {"cantidad": float(cant), "desde": desde}
            conn.commit()
    except Exception:
        return {}
    return out


def _net_factor(cant: float, marca: dict[str, Any] | None, fecha: str) -> float:
    """Fracción de la posición que QUEDA tras descontar el alquiler (0..1).
    Aplica solo si hay marca activa y `fecha` >= `desde` (date-aware). `fecha` y
    `desde` en ISO YYYY-MM-DD → la comparación lexicográfica equivale a la de fechas."""
    if not marca or cant <= 0:
        return 1.0
    desde = marca.get("desde")
    if desde is not None and str(desde) > fecha:
        return 1.0   # el alquiler todavía no arrancó ese día
    lent = min(float(marca["cantidad"]), cant)
    return max(0.0, (cant - lent) / cant)


def titulos_en_alquiler() -> dict[str, Any]:
    """TODOS los (título, cuenta) en posición al último día + su marca de alquiler
    (SI/NO, cantidad, desde). Alimenta la vista de edición 'Títulos en alquiler'."""
    marcas_raw: dict[tuple[str, str], dict[str, Any]] = {}
    posiciones: list[dict[str, Any]] = []
    ultima = None
    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure_alquiler_table(cur)
        cur.execute(f"SELECT id_cuenta, unidad, en_alquiler, cantidad, desde FROM {_ALQUILER_TABLE}")
        for idc, u, en, cant, desde in cur.fetchall():
            marcas_raw[(str(idc), str(u))] = {
                "en_alquiler": bool(en),
                "cantidad": float(cant) if cant is not None else None,
                "desde": desde.isoformat() if desde else None,
            }
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia "
                    "WHERE aum = 'si' AND id_cuenta = ANY(%s)", (CUENTAS,))
        row = cur.fetchone()
        ultima = row[0] if row and row[0] else None
        if ultima:
            cur.execute(
                "SELECT id_cuenta, unidad, cartera, SUM(cantidad), MAX(precio), SUM(valuacion) "
                "FROM portafolio.tenencia "
                "WHERE fecha = %s AND aum = 'si' AND id_cuenta = ANY(%s) "
                "GROUP BY id_cuenta, unidad, cartera HAVING SUM(cantidad) <> 0 "
                "ORDER BY unidad, id_cuenta", (ultima, CUENTAS))
            for idc, u, cart, cant, prec, val in cur.fetchall():
                m = marcas_raw.get((str(idc), str(u))) or {}
                cant_f = round(float(cant or 0.0), 4)
                val_f = round(float(val or 0.0), 2)
                # Valor en alquiler = valuación proporcional a los nominales prestados.
                alq_cant = m.get("cantidad")
                alq_valor = (round(val_f * min(alq_cant, cant_f) / cant_f, 2)
                             if (alq_cant and cant_f) else None)
                posiciones.append({
                    "id_cuenta":   str(idc),
                    "unidad":      u,
                    "cartera":     cart,
                    "cantidad":    cant_f,
                    "precio":      round(float(prec), 4) if prec is not None else None,
                    "valuacion":   val_f,
                    "en_alquiler": m.get("en_alquiler", False),
                    "alq_cant":    alq_cant,
                    "alq_valor":   alq_valor,
                    "desde":       m.get("desde"),
                })
    return {"ultima_fecha": ultima.isoformat() if ultima else None,
            "cuentas": CUENTAS, "posiciones": posiciones}


def set_alquiler_marca(*, id_cuenta: str, unidad: str, en_alquiler: bool,
                       cantidad: float | None, desde: str | None, email: str) -> dict[str, Any]:
    """Upsert de la marca de alquiler de un (título, cuenta). en_alquiler=False o
    cantidad<=0 → apaga la marca. Devuelve el estado guardado."""
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
    d = None
    if desde:
        try:
            d = _date.fromisoformat(desde[:10])
        except ValueError:
            return {"ok": False, "error": f"fecha inválida: {desde!r}"}
    activo = bool(en_alquiler) and (cant or 0) > 0

    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure_alquiler_table(cur)
        cur.execute(
            f"INSERT INTO {_ALQUILER_TABLE} "
            f"(id_cuenta, unidad, en_alquiler, cantidad, desde, updated_by, updated_at) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id_cuenta, unidad) DO UPDATE SET "
            f"en_alquiler = EXCLUDED.en_alquiler, cantidad = EXCLUDED.cantidad, "
            f"desde = EXCLUDED.desde, updated_by = EXCLUDED.updated_by, "
            f"updated_at = EXCLUDED.updated_at",
            (idc, u, activo, cant, d, email, datetime.now(UTC)))
        conn.commit()
    invalidate("tenencia_dias")   # el netting cambió → refrescar la serie de AuM
    return {"ok": True, "id_cuenta": idc, "unidad": u, "en_alquiler": activo,
            "cantidad": cant, "desde": d.isoformat() if d else None}


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

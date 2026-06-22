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
# Set de unidades HD del catálogo SQL portafolio.assets (IS NOT NULL para no romper
# el NOT IN del filtro ARS — un NULL en el subquery haría que NOT IN no devuelva nada).
_HD_UNITS = "SELECT unidad FROM portafolio.assets WHERE cartera = 'HD' AND unidad IS NOT NULL"


def _cartera_subq(cartera: str) -> str:
    """Filtro de cartera para la tenencia valorizada (cuentas propias):
      - 'HD'  → Cartera USD: bonos hard-dollar (cartera = 'HD').
      - 'ARS' → Cartera ARS: TODO lo que NO es HD (pesos: ARS/DL/FCI/RV/cash/…).
    'todas las que no sean de HD' = NOT IN el set HD (incluye unidades sin asset)."""
    if (cartera or "").upper() == "ARS":
        return f"unidad NOT IN ({_HD_UNITS})"
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
    `cartera`: 'HD' (Cartera USD) o 'ARS' (todo lo no-HD)."""
    por_fecha: dict[str, dict[str, float]] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT fecha, id_cuenta, SUM(valuacion) FROM portafolio.tenencia "
            f"WHERE aum = 'si' AND id_cuenta = ANY(%s) AND {_cartera_subq(cartera)} "
            f"GROUP BY fecha, id_cuenta ORDER BY fecha", (CUENTAS,))
        for fecha, idc, val in cur.fetchall():
            por_fecha.setdefault(fecha.isoformat(), {})[str(idc)] = float(val or 0.0)
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
    filtrado por `cartera` ('HD' = Cartera USD / 'ARS' = todo lo no-HD)."""
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
            por_unidad.setdefault(u, {})[c] = float(val or 0.0)
            cant_unidad.setdefault(u, {})[c] = float(cant or 0.0)
            if prec is not None:
                precio_unidad[u] = float(prec)
    posiciones = []
    for u in sorted(por_unidad, key=lambda x: -sum(por_unidad[x].values())):
        byc = por_unidad[u]
        cantc = cant_unidad.get(u, {})
        fila = {
            "unidad": u,
            "total": round(sum(byc.values()), 2),
            "precio": round(precio_unidad[u], 4) if u in precio_unidad else None,
            "cant": {c: round(cantc.get(c, 0.0), 4) for c in CUENTAS},
            "total_cant": round(sum(cantc.values()), 4),
        }
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        posiciones.append(fila)
    total = round(sum(p["total"] for p in posiciones), 2)
    return {"fecha": fecha, "cuentas": CUENTAS, "cartera": (cartera or "HD").upper(),
            "tc": _tc(fecha), "total": total, "posiciones": posiciones}


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

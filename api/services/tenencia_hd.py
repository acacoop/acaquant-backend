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
    filtrado por `cartera` ('HD' = Cartera USD / 'ARS' = todo lo no-HD).

    Suma la marca de ALQUILER (durable, por título): `alq_cant` = nominales en
    alquiler y `alq_valor` = valor de mercado de esos nominales (proporcional a
    la valuación del día → respeta la regla de paridad ya aplicada)."""
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

    alq = get_alquiler_map()  # {unidad: nominales en alquiler} (durable)
    posiciones = []
    total_alquiler = 0.0
    for u in sorted(por_unidad, key=lambda x: -sum(por_unidad[x].values())):
        byc = por_unidad[u]
        cantc = cant_unidad.get(u, {})
        total_val = round(sum(byc.values()), 2)
        total_cant = round(sum(cantc.values()), 4)
        fila = {
            "unidad": u,
            "total": total_val,
            "precio": round(precio_unidad[u], 4) if u in precio_unidad else None,
            "cant": {c: round(cantc.get(c, 0.0), 4) for c in CUENTAS},
            "total_cant": total_cant,
            "alq_cant": None,
            "alq_valor": None,
        }
        cant_alq = alq.get(u)
        if cant_alq and total_cant:
            # Valor proporcional; capeo al nominal existente por si la posición bajó.
            eff = min(cant_alq, total_cant)
            val_alq = round(total_val * (eff / total_cant), 2)
            fila["alq_cant"] = round(cant_alq, 4)
            fila["alq_valor"] = val_alq
            total_alquiler += val_alq
        fila.update({c: round(byc.get(c, 0.0), 2) for c in CUENTAS})
        posiciones.append(fila)
    total = round(sum(p["total"] for p in posiciones), 2)
    return {"fecha": fecha, "cuentas": CUENTAS, "cartera": (cartera or "HD").upper(),
            "tc": _tc(fecha), "total": total,
            "total_alquiler": round(total_alquiler, 2), "posiciones": posiciones}


# ── ALQUILER (marca durable por título, self-service) ────────────────────────
# Nominales en alquiler por unidad. NO es por día: se setea una vez y dura hasta
# que el usuario lo cambie. Tabla self-create para tolerar el drift de schema.
_ALQUILER_TABLE = "portafolio.alquiler"


def _ensure_alquiler_table(cur) -> None:
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_ALQUILER_TABLE} ("
        "unidad text PRIMARY KEY, cantidad numeric, "
        "updated_by text, updated_at timestamptz)")


def get_alquiler_map() -> dict[str, float]:
    """{unidad: nominales en alquiler}. Vacío si la tabla no existe / PG caído."""
    out: dict[str, float] = {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            _ensure_alquiler_table(cur)
            cur.execute(f"SELECT unidad, cantidad FROM {_ALQUILER_TABLE} WHERE cantidad > 0")
            for u, c in cur.fetchall():
                if u and c:
                    out[str(u)] = float(c)
            conn.commit()
    except Exception:
        return {}
    return out


def set_alquiler(*, unidad: str, cantidad: float | None, email: str) -> dict[str, Any]:
    """Setea los nominales en alquiler de una unidad (durable). cantidad None o <=0
    → borra la marca. Devuelve {ok, unidad, cantidad}."""
    from datetime import UTC, datetime
    u = (unidad or "").strip()
    if not u:
        return {"ok": False, "error": "unidad vacía"}
    try:
        cant = float(cantidad) if cantidad is not None else 0.0
    except (TypeError, ValueError):
        return {"ok": False, "error": f"cantidad inválida: {cantidad!r}"}

    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure_alquiler_table(cur)
        if cant <= 0:
            cur.execute(f"DELETE FROM {_ALQUILER_TABLE} WHERE unidad = %s", (u,))
            conn.commit()
            return {"ok": True, "unidad": u, "cantidad": 0.0}
        cur.execute(
            f"INSERT INTO {_ALQUILER_TABLE} (unidad, cantidad, updated_by, updated_at) "
            f"VALUES (%s, %s, %s, %s) ON CONFLICT (unidad) DO UPDATE SET "
            f"cantidad = EXCLUDED.cantidad, updated_by = EXCLUDED.updated_by, "
            f"updated_at = EXCLUDED.updated_at",
            (u, round(cant, 4), email, datetime.now(UTC)))
        conn.commit()
    return {"ok": True, "unidad": u, "cantidad": round(cant, 4)}


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

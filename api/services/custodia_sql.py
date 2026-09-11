"""api/services/custodia_sql.py — lectura de la tenencia de CVSA. PURO (sin FastAPI).

Sirve la tab CUSTODIA → TENENCIAS de `/back-office`. Doc: `docs/BYMA_CUSTODIA.md`.
Los datos los escribe `jobs/custodia_cvsa.py` cada hora.

Todo lo que la vista muestra sale de ACÁ, contadores incluidos: el front no
deriva ni suma nada, y un número que no venga en el payload es un faltante del
contrato, no un cálculo para hacer del otro lado.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from core.postgres import get_pool

# Todo lo que no es AVAILABLE es tenencia que NO se puede entregar ni garantizar.
# Es la información que Aunesa no da, así que la vista la cuenta aparte.
DISPONIBLE = "AVAILABLE"


def ultima_fecha() -> date | None:
    """La fecha más reciente con datos. None si la tabla está vacía (aún no corrió)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.custodia_cvsa")
        return (cur.fetchone() or [None])[0]


def tenencias(*, fecha: date | None = None, id_cuenta: str | None = None,
              estado: str | None = None, solo_trabado: bool = False,
              limite: int = 5000) -> dict[str, Any]:
    """La foto de CVSA de un día, con sus contadores.

    Sin `fecha` usa la última que haya: la vista tiene que abrir mostrando algo
    aunque el job de hoy todavía no haya corrido.
    """
    f = fecha or ultima_fecha()
    if f is None:
        return {"fecha": None, "filas": [], "total_filas": 0, "cuentas": 0,
                "sin_asset": 0, "trabado": 0, "actualizado_at": None,
                "aviso": "todavía no se bajó ninguna foto de CVSA"}

    where = ["c.fecha = %(fecha)s"]
    params: dict[str, Any] = {"fecha": f, "limite": limite}
    if id_cuenta:
        where.append("c.id_cuenta = %(id_cuenta)s")
        params["id_cuenta"] = id_cuenta.strip()
    if estado:
        where.append("c.sub_balance_type = %(estado)s")
        params["estado"] = estado.strip().upper()
    if solo_trabado:
        where.append("c.sub_balance_type <> %(disponible)s")
        params["disponible"] = DISPONIBLE
    filtro = " AND ".join(where)

    sql = f"""
        SELECT c.id_cuenta, cu.denominacion, c.cvsa_id, c.unidad, a.ticker,
               c.sub_balance_type, c.cantidad, c.account_number, c.actualizado_at
          FROM portafolio.custodia_cvsa c
          LEFT JOIN clientes.cuentas cu ON cu.id_cuenta = c.id_cuenta
          LEFT JOIN portafolio.assets  a ON a.unidad    = c.unidad
         WHERE {filtro}
         ORDER BY c.id_cuenta, c.unidad NULLS LAST, c.cvsa_id, c.sub_balance_type
         LIMIT %(limite)s
    """

    # Los totales salen de la MISMA query que dibuja la lista (mismo filtro), no
    # de contar las filas devueltas: con LIMIT, contar lo devuelto daría un
    # número más chico que el real y nadie lo notaría.
    sql_tot = f"""
        SELECT count(*),
               count(DISTINCT c.id_cuenta),
               count(*) FILTER (WHERE c.unidad IS NULL),
               count(*) FILTER (WHERE c.sub_balance_type <> '{DISPONIBLE}'),
               max(c.actualizado_at)
          FROM portafolio.custodia_cvsa c
         WHERE {filtro}
    """

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        filas = [
            {"id_cuenta": r[0], "cuenta": r[1], "cvsa_id": r[2], "unidad": r[3],
             "ticker": r[4], "estado": r[5],
             "cantidad": float(r[6]) if r[6] is not None else None,
             "account_number": r[7],
             "actualizado_at": r[8].isoformat() if r[8] else None}
            for r in cur.fetchall()
        ]
        cur.execute(sql_tot, params)
        total, cuentas, sin_asset, trabado, actualizado = cur.fetchone()

    return {
        "fecha": f.isoformat(),
        "filas": filas,
        "total_filas": int(total or 0),
        "cuentas": int(cuentas or 0),
        # Códigos de la Caja que no tienen instrumento nuestro. Es un hueco
        # CONOCIDO (falta el código en assets), no un error: la tenencia existe
        # igual y por eso se guarda y se muestra.
        "sin_asset": int(sin_asset or 0),
        "trabado": int(trabado or 0),
        "truncado": len(filas) >= limite,
        "actualizado_at": actualizado.isoformat() if actualizado else None,
    }


def estados(fecha: date | None = None) -> list[dict[str, Any]]:
    """Los `subBalanceType` presentes ese día, con cuántas filas tiene cada uno.

    Alimenta los chips de la vista: el universo de estados sale de los DATOS, no
    de una lista hardcodeada que se desactualiza sola.
    """
    f = fecha or ultima_fecha()
    if f is None:
        return []
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sub_balance_type, count(*) FROM portafolio.custodia_cvsa "
            "WHERE fecha = %s GROUP BY 1 ORDER BY 2 DESC", (f,))
        return [{"estado": r[0], "n": int(r[1])} for r in cur.fetchall()]

"""api/services/custodia_sql.py — lectura de la tenencia de CVSA. PURO (sin FastAPI).

Sirve la tab CUSTODIA de `/back-office`. Los datos los escribe la PC de oficina
(`scripts/byma_feed.py` → `POST /api/ingest/custodia/holdings`). Doc:
`docs/BYMA_CUSTODIA.md`.

UNA SOLA QUERY, la foto entera del día, y los contadores se cuentan sobre esas
mismas filas. Antes eran tres queries (lista + totales + estados) y cada cambio
de filtro en la pantalla disparaba las tres de nuevo: con ~2.800 filas eso es un
segundo de espera para tildar un chip.

La foto de un día es un CONJUNTO CERRADO y chico. Traerla una vez y filtrarla en
memoria es más rápido y, sobre todo, **hace imposible que un contador contradiga
a su lista**: salen del mismo array. Si algún día una foto no entrara en un
payload razonable, la decisión se revisa — `LIMITE_FILAS` deja el techo a la
vista en vez de que el día que pase nadie sepa por qué faltan filas.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from core.postgres import get_pool

# Todo lo que no es AVAILABLE es tenencia que NO se puede entregar ni garantizar.
# Es la información que Aunesa no da, así que la vista la cuenta aparte.
DISPONIBLE = "AVAILABLE"

# Techo duro del payload. Medido: una foto real son ~2.800 filas. Si alguna vez
# se toca, la respuesta lo dice (`truncado`) en vez de mentir por lo bajo.
LIMITE_FILAS = 20_000


def ultima_fecha() -> date | None:
    """La fecha más reciente con datos. None si todavía no entró ninguna foto."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.custodia_cvsa")
        return (cur.fetchone() or [None])[0]


def tenencias(*, fecha: date | None = None) -> dict[str, Any]:
    """La foto de CVSA de un día, entera, con sus contadores.

    Sin `fecha` usa la última que haya: la vista tiene que abrir mostrando algo
    aunque el feed de hoy todavía no haya corrido.
    """
    f = fecha or ultima_fecha()
    if f is None:
        return {"fecha": None, "filas": [], "total_filas": 0, "cuentas": 0,
                "sin_asset": 0, "trabado": 0, "truncado": False,
                "actualizado_at": None, "estados": [],
                "aviso": "todavía no entró ninguna foto de la Caja de Valores"}

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.id_cuenta, cu.denominacion, c.cvsa_id, c.unidad, a.ticker, "
            "       c.sub_balance_type, c.cantidad, c.actualizado_at "
            "  FROM portafolio.custodia_cvsa c "
            "  LEFT JOIN clientes.cuentas cu ON cu.id_cuenta = c.id_cuenta "
            "  LEFT JOIN portafolio.assets  a ON a.unidad    = c.unidad "
            " WHERE c.fecha = %s "
            " ORDER BY c.id_cuenta, c.unidad NULLS LAST, c.cvsa_id, c.sub_balance_type "
            " LIMIT %s", (f, LIMITE_FILAS))
        crudas = cur.fetchall()

    filas = [
        {"id_cuenta": r[0], "cuenta": r[1], "cvsa_id": r[2], "unidad": r[3],
         "ticker": r[4], "estado": r[5],
         "cantidad": float(r[6]) if r[6] is not None else None}
        for r in crudas
    ]

    # Una sola pasada para todo lo que la cabecera muestra.
    cuentas: set[str] = set()
    por_estado: dict[str, int] = {}
    sin_asset = trabado = 0
    ultimo = None
    for r in crudas:
        cuentas.add(r[0])
        por_estado[r[5]] = por_estado.get(r[5], 0) + 1
        if r[3] is None:
            sin_asset += 1
        if r[5] != DISPONIBLE:
            trabado += 1
        if ultimo is None or (r[7] and r[7] > ultimo):
            ultimo = r[7]

    return {
        "fecha": f.isoformat(),
        "filas": filas,
        "total_filas": len(filas),
        "cuentas": len(cuentas),
        # Filas cuyo código de la Caja no tiene instrumento en `assets`. Es un
        # hueco CONOCIDO (falta el código de CAJA), no un error: la tenencia
        # existe igual y por eso se guarda y se muestra.
        "sin_asset": sin_asset,
        "trabado": trabado,
        "truncado": len(filas) >= LIMITE_FILAS,
        "actualizado_at": ultimo.isoformat() if ultimo else None,
        # El universo de estados sale de los DATOS, no de una lista hardcodeada
        # que se desactualiza sola.
        "estados": sorted(({"estado": k, "n": v} for k, v in por_estado.items()),
                          key=lambda x: -x["n"]),
    }

"""Sub-router Manager → /api/manager/comercial — Tablero Comercial.

v1 (solo manager): lente POR OPERADOR. Hereda el gate `_MANAGER` del paquete
(`require_module("manager")`). Cuando se abra a operadores con scope, se
extrae a su propio módulo RBAC `comercial`. Ver docs/TABLERO_COMERCIAL.md [5].
"""
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.services.comercial import resumen_por_operador
from api.services.macro import get_ultimo_mep, get_ultimo_uva
from api.services.segmentacion import _TIPOS_PH, _TIPOS_PJ, clasificar_nivel_3

router = APIRouter()


@router.get("/comercial/operadores")
def comercial_operadores(
    dias_activa: int = Query(30, ge=1, le=365, description="≤ este nº de días sin operar = ACTIVA"),
    dias_dormida: int = Query(90, ge=1, le=730, description="> este nº de días = DORMIDA"),
) -> dict[str, Any]:
    """Resumen comercial por operador: # cuentas, activas/dormidas, AuM, etc."""
    return resumen_por_operador(dias_activa=dias_activa, dias_dormida=dias_dormida)


@router.get("/comercial/debug-segmento")
def debug_segmento(id_cuenta: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Debug AUDITABLE del cálculo de `nivel_3` para una cuenta puntual.

    Devuelve cupo crudo + MEP/UVA usado + cálculo paso a paso + nivel_3 actual
    vs lo que daría recalculado ahora. Para que la mesa pueda confirmar que
    el segmento es el correcto antes de actuar comercialmente.
    """
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT c.id_cuenta, u.denominacion, c.tipo_cliente, c.nivel_1, c.nivel_3, "
            "c.cupo_transaccional_ars, c.cupo_usado_ars, c.cupo_cargado_en, c.cupo_fuente "
            "FROM comitentes c LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
            "WHERE c.id_cuenta = %s", (id_cuenta,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"id_cuenta {id_cuenta!r} no encontrada en comitentes")
    doc = {
        "id_cuenta": row["id_cuenta"], "denominacion": row["denominacion"],
        "tipo_cliente": row["tipo_cliente"], "nivel_1": row["nivel_1"], "nivel_3": row["nivel_3"],
        "cupo": {"transaccional_ars": row["cupo_transaccional_ars"],
                 "usado_ars": row["cupo_usado_ars"], "cargado_en": row["cupo_cargado_en"],
                 "fuente": row["cupo_fuente"]},
    }

    tipo = doc.get("tipo_cliente")
    cupo = doc.get("cupo") or {}
    cupo_ars = cupo.get("transaccional_ars")
    usado_ars = cupo.get("usado_ars")

    if tipo in _TIPOS_PH:
        clasif = "PH"
    elif tipo in _TIPOS_PJ:
        clasif = "PJ"
    else:
        clasif = None

    mep_data = get_ultimo_mep()
    mep = float(mep_data.get("mep") or 0) or None
    uva = get_ultimo_uva()

    cupo_convertido: float | None = None
    unidad: str | None = None
    factor_usado: float | None = None
    factor_fuente: str | None = None
    if clasif == "PH" and cupo_ars and mep:
        cupo_convertido = round(float(cupo_ars) / mep, 2)
        unidad = "USD"
        factor_usado = mep
        factor_fuente = f"MEP live ({mep_data.get('source') or '?'})"
    elif clasif == "PJ" and cupo_ars and uva:
        cupo_convertido = round(float(cupo_ars) / uva, 2)
        unidad = "UVA"
        factor_usado = uva
        factor_fuente = "Trading.UVA (carga manual)"

    nivel_3_recalc = clasificar_nivel_3(
        tipo, float(cupo_ars) if cupo_ars is not None else None, mep=mep, uva=uva,
    )
    nivel_3_actual = doc.get("nivel_3")

    return {
        "id_cuenta": doc["id_cuenta"],
        "denominacion": doc.get("denominacion"),
        "tipo_cliente": tipo,
        "clasificacion": clasif,        # "PH" | "PJ" | null
        "nivel_1": doc.get("nivel_1"),
        "cupo": {
            "transaccional_ars": cupo_ars,
            "usado_ars": usado_ars,
            "cargado_en": cupo.get("cargado_en"),
            "fuente": cupo.get("fuente"),
        },
        "tc": {
            "factor": factor_usado,     # MEP o UVA
            "unidad": unidad,           # "USD" o "UVA"
            "fuente": factor_fuente,
            "mep_timestamp": mep_data.get("timestamp"),
        },
        "cupo_convertido": cupo_convertido,   # en USD (PH) o UVAs (PJ)
        "nivel_3_actual": nivel_3_actual,
        "nivel_3_recalculado": nivel_3_recalc,
        "sincronizado": nivel_3_actual == nivel_3_recalc,
        "umbrales": {
            "PH_USD": {
                "PH RETAIL":          "< 50.000 USD",
                "PH MEDIO RETAIL":    "50.000 ≤ x ≤ 100.000 USD",
                "PH ALTO PATRIMONIO": "> 100.000 USD",
            },
            "PJ_UVA": {
                "PJ PEQUEÑA": "≤ 350.000 UVA",
                "PJ MEDIANA": "350.000 < x ≤ 700.000 UVA",
                "PJ GRANDE":  "> 700.000 UVA",
            },
        },
    }

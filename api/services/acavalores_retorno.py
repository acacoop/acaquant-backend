"""ACA VALORES RETORNO TOTAL — agregaciones de `operaciones.acavalores_retorno`.

Módulo PURO (regla de capas de api/services): solo SQL vía `_q`, sin FastAPI. La
tabla la carga EXCLUSIVAMENTE scripts/import_acavalores_retorno.py (informe Excel,
no diario). Acá vive la lectura que alimenta la tab "ACA VALORES RETORNO TOTAL" de
Mesa de Dinero: sumatoria de Valor Nominal por operación, por agente (con su share
%) y por papel, filtrable por período ('YYYY-MM').
"""
from __future__ import annotations

from api.services._sql import _f, _q

_TABLA = "operaciones.acavalores_retorno"


def _periodos() -> list[str]:
    """Períodos cargados, del más nuevo al más viejo."""
    filas = _q(
        f"SELECT DISTINCT periodo FROM {_TABLA} "
        "WHERE periodo IS NOT NULL ORDER BY periodo DESC"
    )
    return [f["periodo"] for f in filas]


def _por(campo: str, periodo: str | None) -> list[dict]:
    """Σ Valor Nominal + cantidad de operaciones agrupado por `campo`."""
    where = "WHERE periodo = %(p)s" if periodo else ""
    filas = _q(
        f"SELECT COALESCE(NULLIF(TRIM({campo}), ''), '(sin dato)') AS clave, "
        f"       SUM(valor_nominal) AS vn, COUNT(*) AS n "
        f"FROM {_TABLA} {where} "
        f"GROUP BY clave ORDER BY SUM(valor_nominal) DESC NULLS LAST",
        {"p": periodo} if periodo else None,
    )
    return [{"clave": f["clave"], "vn": _f(f["vn"]) or 0.0, "n": int(f["n"])} for f in filas]


def _con_share(filas: list[dict]) -> list[dict]:
    total = sum(f["vn"] for f in filas) or 0.0
    for f in filas:
        f["share"] = (f["vn"] / total) if total else 0.0
    return filas


def panel(periodo: str | None = None) -> dict:
    """Payload completo de la tab. Si `periodo` es None usa el más reciente."""
    periodos = _periodos()
    sel = periodo if (periodo and periodo in periodos) else (periodos[0] if periodos else None)

    por_operacion = _por("operacion", sel)
    por_agente = _con_share(_por("agente_descripcion", sel))
    por_papel = _por("papel_descripcion", sel)
    total_vn = sum(f["vn"] for f in por_operacion)

    return {
        "periodos": periodos,
        "periodo": sel,
        "total_vn": total_vn,
        "por_operacion": por_operacion,
        "por_agente": por_agente,
        "por_papel": por_papel,
    }

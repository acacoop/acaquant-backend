"""ACA VALORES RETORNO TOTAL — lectura de `operaciones.acavalores_retorno`.

Módulo PURO (regla de capas de api/services): solo SQL vía `_q`, sin FastAPI. La
tabla la carga EXCLUSIVAMENTE scripts/import_acavalores_retorno.py (informe Excel,
no diario). Acá vive la lectura que alimenta la tab "ACA VALORES RETORNO TOTAL" de
Mesa de Dinero. Devuelve las FILAS crudas del período (operación / agente / papel /
cash); la métrica es el CASH (columna "Moneda de Concertación Bruto"). Las sumatorias
y el cross-filter interactivo (tocar un agente filtra los demás paneles) se calculan
en el frontend — el volumen por período es chico.
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


def panel(periodo: str | None = None) -> dict:
    """Filas del período (default = más reciente) para agregar en el cliente."""
    periodos = _periodos()
    sel = periodo if (periodo and periodo in periodos) else (periodos[0] if periodos else None)

    filas: list[dict] = []
    if sel:
        rows = _q(
            "SELECT fecha_concertacion AS fecha, operacion, "
            "       agente_descripcion AS agente, "
            "       papel_descripcion AS papel, bruto AS cash "
            f"FROM {_TABLA} WHERE periodo = %(p)s",
            {"p": sel},
        )
        filas = [
            {
                "fecha": (r["fecha"].isoformat() if r["fecha"] else None),
                "operacion": (r["operacion"] or "(sin dato)"),
                "agente": (r["agente"] or "(sin dato)"),
                "papel": (r["papel"] or "(sin dato)"),
                "cash": _f(r["cash"]) or 0.0,
            }
            for r in rows
        ]

    return {"periodos": periodos, "periodo": sel, "filas": filas}


"""Filtro de exclusión de futuros para queries sobre CashFlow.NegocioMovimientos.

Los futuros ROFEX DLR no pagan arancel propio del proyecto — el cliente paga
aparte por el lado del mercado. Aparecen en `NegocioMovimientos` con
`unidad == "USDL"` (mismo marker que ya usa AuM en `jobs/_aum_filters.py`).

Si NO se filtran, inflan:
- el volumen operado por operador (KPI comercial),
- los gráficos de NEGOCIO de `/operaciones/negocio`,
- el matching de `jobs/aranceles.py` contra `/operaciones/informes`
  (los futuros nunca van a tener arancel ahí → quedan como "sin match" falso).

Sumar unidades nuevas acá las propaga a todos los consumidores en un único
lugar. Mantener en sync con `jobs/_aum_filters.py::EXCLUDE_UNIDAD_EXACT`.
"""
from __future__ import annotations

# Tupla canónica. Hoy: solo USDL (futuros DLR). Si se suma OTC u otro tipo
# que el negocio no quiere ver, agregarlo acá.
EXCLUIR_UNIDADES_FUTUROS: tuple[str, ...] = ("USDL",)


def match_no_futuros() -> dict:
    """Sub-doc $match para excluir futuros — pensado para spread en pipelines.

    Uso:
        {"$match": {"categoria": {"$in": cats}, **match_no_futuros()}}
    """
    return {"unidad": {"$nin": list(EXCLUIR_UNIDADES_FUTUROS)}}

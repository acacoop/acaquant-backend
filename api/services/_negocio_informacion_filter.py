"""Filtro de exclusión por `informacion` para CashFlow.NegocioMovimientos.

Lista canónica de substrings que NO deben entrar a la base — son movimientos
administrativos / regulatorios que ensucian el control comercial sin aportar
información operativa.

Aplicado en 2 lugares:
- INGESTA (`jobs.negocio_movimientos` via `api/services/aunesa_negocio.py`):
  se descartan ANTES del upsert → nunca entran a la base.
- CLEANUP (`scripts/cleanup_negocio_informacion.py`): borra de la base los
  que ya están persistidos.

Match: `informacion` debe contener (substring, case-sensitive) cualquiera
de los strings de `EXCLUIR_INFORMACION_CONTAINS`. Aunesa los manda con
capitalización consistente, no hace falta normalizar.

Para sumar reglas nuevas, editar la tupla. La fuente de verdad es este
archivo — el script de cleanup, la ingesta y cualquier endpoint que muestre
"ruido" leen de acá.
"""
from __future__ import annotations

# Tupla canónica de substrings. Confirmada por el usuario:
# - Bonificación
# - recuperos devengados
# - Gestión de cobranza
# - Márgenes MtR
# - Diferencias - Comit
EXCLUIR_INFORMACION_CONTAINS: tuple[str, ...] = (
    "Bonificación",
    "recuperos devengados",
    "Gestión de cobranza",
    "Márgenes MtR",
    "Diferencias - Comit",
)


def es_excluido(informacion: str | None) -> bool:
    """True si `informacion` contiene cualquiera de los substrings canónicos."""
    if not informacion:
        return False
    return any(s in informacion for s in EXCLUIR_INFORMACION_CONTAINS)



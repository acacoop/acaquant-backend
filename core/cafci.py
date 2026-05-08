"""Extracción del código CAFCI desde un string `unidad`.

Las unidades de FCI en `Valuaciones.AuM` y `Valuaciones.Assets` vienen
con el formato `[<id>] CAFCI<n>-<m> - <descripción>`. El código CAFCI
es el identificador real del fondo y es lo que matchea con el `ticker`
parseado de los boletos en `CashFlow.NegocioMovimientos`.

Fuente única — usado por:
  - `jobs/aum.py::_sincronizar_assets` (auto-fill al sincronizar Assets).
  - `scripts/backfill_assets_cafci.py` (backfill de docs viejos).
"""
from __future__ import annotations

import re

_RE_CAFCI = re.compile(r"\bCAFCI\d+-\d+\b")


def extract_cafci(unidad: str | None) -> str | None:
    """Devuelve el código CAFCI (ej `CAFCI3580-1199`) o None si no aparece."""
    if not unidad:
        return None
    m = _RE_CAFCI.search(unidad)
    return m.group(0) if m else None

"""Parseo de un string `unidad` de FCI.

Las unidades de FCI vienen con el formato `[<id>] CAFCI<n>-<m> - <descripción>`.
El código CAFCI es el identificador real del fondo (matchea con el `ticker`
parseado de los boletos de negocio_movimientos); la descripción es el NOMBRE del
fondo (lo que se muestra como `ticker` en el detalle de /aum → FCI).

Fuente única del patrón FCI (código + nombre). Usado por el writer diario
(`jobs/portafolio_backfill.py` — auto-alta de assets), el backfill
`scripts/backfill_fci_ticker.py` y el control `controles_datos.fci_incompletos`.
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


def es_fci_unidad(unidad: str | None) -> bool:
    """True si la `unidad` tiene formato de FCI (contiene un código CAFCI)."""
    return extract_cafci(unidad) is not None


def nombre_fci(unidad: str | None) -> str | None:
    """Nombre del fondo derivado de la `unidad`: TODO lo que va después del código
    CAFCI, conservando los guiones internos del nombre (la clase incluida).

        `[1047] CAFCI518-1047 - Argenfunds Ahorro Pesos - Clase B`
            → `Argenfunds Ahorro Pesos - Clase B`

    Es el valor que va como `ticker` del asset FCI (lo que rotula la fila en el
    detalle de /aum → FCI). Determinístico: sale siempre de la unidad, no hay que
    ir a buscarlo a ningún lado. None si la unidad no tiene el formato FCI (no hay
    código CAFCI, o no queda texto después) → el caller lo trata como
    no-parseable y lo deja para revisión manual (observabilidad)."""
    if not unidad:
        return None
    m = _RE_CAFCI.search(unidad)
    if not m:
        return None
    # Texto tras el código: sacar SOLO el separador " - " inicial (lstrip de
    # espacios/guiones al principio); los " - " internos del nombre se preservan.
    resto = unidad[m.end():].lstrip(" -\t").strip()
    return resto or None

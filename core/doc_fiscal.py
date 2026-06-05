"""Documento fiscal (DNI/CUIT/CUIL) — parseo y claves de cruce. Lógica PURA.

Vive en core/ (no importa nada del proyecto) para que la puedan usar por igual
`jobs/sync_comitentes` (al persistir) y `api/services/control_automatico` (al
cruzar) sin romper la regla de capas.

Verificado con scripts/diag_titular_cuit: nuestras cuentas guardan DNI (físicas,
7-8 díg) o CUIT/CUIL/CDI (jurídicas, 11) en el `titular` de Aunesa; el Excel del
Control Automático trae CUIT (11). Un CUIT contiene el DNI de 8 en el medio
(TT-DDDDDDDD-V) → el cruce se hace por claves equivalentes.
"""
from __future__ import annotations

import re

_NO_DIGITO = re.compile(r"\D")
_BRACKET = re.compile(r"^\[\s*([A-Za-z./]+)\s+([0-9.\-]+)\s*\]")

NIVEL_1_PRODUCTORES = "PRODUCTORES"


def solo_digitos(s: object) -> str:
    return _NO_DIGITO.sub("", str(s or ""))


def parse_titular(titular: object) -> tuple[str | None, str | None]:
    """'[DNI 93698623] NOMBRE' → ('DNI', '93698623'). (None, None) si no parsea."""
    m = _BRACKET.match(str(titular or "").strip())
    if not m:
        return None, None
    return m.group(1).upper(), solo_digitos(m.group(2))


def claves_match(nro: object) -> set[str]:
    """Claves de cruce de un número de documento (CUIT 11 o DNI 7/8).

    Devuelve todas las representaciones por las que puede matchear:
      - CUIT/CUIL/CDI (11): el número entero + el DNI embebido (8 del medio).
      - DNI/LC (7-8): el número normalizado a 8 (zero-pad).
    Así un CUIT del Excel cruza con un DNI nuestro y viceversa (función simétrica:
    se aplica igual para indexar nuestras cuentas y para buscar el CUIT del Excel).
    """
    d = solo_digitos(nro)
    if not d:
        return set()
    claves = {d}
    if len(d) == 11:
        claves.add(d[2:10])      # DNI embebido en el CUIT
    elif len(d) in (7, 8):
        claves.add(d.zfill(8))   # DNI a 8 dígitos
    return claves

"""core/mav_tasa.py — parseo de la TASA de los boletos MAV.

POR QUÉ EXISTE (medido 2026-08-03)
----------------------------------
`operaciones.negocio_movimientos.precio` está poblada al 100% en las categorías
compra / venta / suscripcion_fci / rescate_fci / caucion_*, y al 0% en `otro`.
El 100% de los boletos de MERCADO MAV cae en `otro` → para MAV esa columna es
SIEMPRE NULL. No es un hueco de datos: un pagaré o un cheque no se negocia a
precio unitario, **se negocia A TASA**.

La tasa viaja embebida en el texto de `informacion`:

    'Compra  [#UAC140770002] 100.000,00@6%    (ARS 24hs)'  → 6
    'Compra  [*ACI120300125] 27.000.000,00@39,5% (ARS 24hs)' → 39,5
    'Subasta [#UCO310770001] 30.000,00@7%     (ARS Inm)'   → 7
    'Compra  [#UMV131230011] 500.000,00@-0,5% (ARS Inm)'   → -0,5  ← negativas

Formato: `<Operación> [<código>] <nominal>@<TASA>% (<moneda> <plazo>)`, números
en formato argentino (punto de miles, coma decimal).

Módulo PURO: sólo `re`. No importa nada del proyecto (regla de capas de `core/`)
para que lo puedan usar tanto `jobs/` como `scripts/` sin duplicar el parseo.
"""
from __future__ import annotations

import re

# 'Compra [#UAC140770002] 100.000,00@6% (ARS 24hs)'
#          └─ código ──┘  └ nominal ┘ └tasa┘
# El signo es OBLIGATORIO capturarlo: hay boletos a tasa NEGATIVA
# ('...500.000,00@-0,5% (ARS Inm)', 132 casos medidos en prod 2026-08-03).
# Quedarse con el valor absoluto guardaría 0,5 donde va -0,5 — un error peor
# que dejar el dato vacío, porque no se nota.
_RE_TASA = re.compile(r"@\s*([+-]?[\d.,]+)\s*%")
_RE_COD = re.compile(r"\[([^\]]+)\]")
_RE_NOMINAL = re.compile(r"\]\s*([\d.,]+)\s*@")


def num_ar(s: str | None) -> float | None:
    """Número en formato argentino → float. '39,5'→39.5, '27.000.000,00'→27000000.0

    Sin coma la cadena es AMBIGUA ('39.5' puede ser 39,5 o 395): se asume punto
    decimal sólo si hay UN punto con 1-2 dígitos detrás; si no, es separador de
    miles. Los datos vistos usan siempre coma decimal, pero un error de 10x
    metido en silencio sería peor que devolver None.
    """
    t = (s or "").strip()
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") == 1 and len(t.split(".")[1]) <= 2:
        pass  # ya es decimal con punto
    else:
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def parse_informacion(info: str | None) -> dict:
    """Extrae de `informacion`: tasa (el precio de MAV), código de instrumento y nominal.

    Devuelve siempre las 4 claves; `tasa_pct` es None si el texto no tiene el
    formato conocido (el caller decide si eso es un error o no). `tasa_raw`
    conserva el token crudo para poder auditar el parseo.

    La tasa se devuelve EN PORCENTAJE tal como figura en el texto: '@6%' → 6.0
    (no 0.06).
    """
    if not info:
        return {"tasa_pct": None, "tasa_raw": "", "cod_instrumento": "", "nominal_info": None}
    m_tasa = _RE_TASA.search(info)
    m_cod = _RE_COD.search(info)
    m_nom = _RE_NOMINAL.search(info)
    raw = m_tasa.group(1) if m_tasa else ""
    return {
        "tasa_pct": num_ar(raw),
        "tasa_raw": raw,
        "cod_instrumento": m_cod.group(1) if m_cod else "",
        "nominal_info": num_ar(m_nom.group(1)) if m_nom else None,
    }

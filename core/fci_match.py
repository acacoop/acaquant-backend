"""core/fci_match.py — cómo se reconoce un fondo por su NOMBRE (puro, sin DB).

Doc madre: `docs/FCI.md`. Un fondo llega con tres grafías del mismo nombre:

    Primary   securityDescription = "FCI Adcap Cobertura - Clase A"
    Primary   símbolo             = "ADBAICA AR"  ó  " IEB Retorno Total - Clase D"
    Aunesa    unidad/ticker       = "SBS PESOS PLUS FCI Clase B", "SCHRODER LIQUIDEZ F.C.I. C"

`normalizar()` las lleva a una clave comparable ("adcap cobertura clase a") y
`gerente_de()` decide de qué gerente es un fondo por cómo EMPIEZA su nombre,
contra los alias que carga la mesa. Es el único lugar donde se decide eso.

Por qué no se usa el nombre como IDENTIDAD (REGLA #9): el match por nombre
propone; la fila de `portafolio.assets.instrumento` que resulta la confirma la
mesa en Manager y a partir de ahí el link es por símbolo, no por texto.
"""
from __future__ import annotations

import re
import unicodedata

# Ruido que aparece en una grafía y no en otra. Orden: los largos primero.
_RUIDO = (
    "fondos comunes de inversion", "fondo comun de inversion", "f.c.i.", "f.c.i", "fci",
    "ley n 27.743", "ley n° 27.743", "ley 27.743", "ley n 27743", "ley 27743",
)
_PLAZOS_FIX: dict[str, int] = {"1": 0, "2": 1, "3": 2, "4": 3}   # settlType FIX → T+n


def normalizar(nombre: str | None) -> str:
    """Clave de comparación: minúsculas, sin acentos, sin 'FCI'/'F.C.I.', sin
    puntuación, espacios colapsados. Vacío → ''."""
    if not nombre:
        return ""
    s = unicodedata.normalize("NFKD", str(nombre)).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"^\s*\[\d+\]\s*", "", s)                 # "[1047] " de Aunesa
    s = re.sub(r"\bcafci\d+-\d+\b", " ", s)              # el código CAFCI de la unidad
    for r in _RUIDO:
        s = s.replace(r, " ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def nombre_desde_primary(inst: dict) -> str:
    """El nombre legible de un instrument CIO de Primary: la descripción sin el
    prefijo 'FCI ', o el símbolo si no hay descripción."""
    d = str(inst.get("securityDescription") or "").strip()
    d = re.sub(r"^fci\s+", "", d, flags=re.I).strip()
    if d:
        return d
    sym = inst.get("symbol") or (inst.get("instrumentId") or {}).get("symbol") or ""
    return str(sym).strip()


def plazo_desde_settl(settl: str | int | None) -> int | None:
    return _PLAZOS_FIX.get(str(settl)) if settl is not None else None


def gerente_de(nombre: str, alias_por_gerente: dict[str, list[str]]) -> str | None:
    """¿De qué gerente es este fondo? El nombre normalizado tiene que EMPEZAR con
    un alias normalizado, como palabra entera. Gana el alias más largo (así
    'Toronto Trust' le gana a 'Toronto' y 'Max Capital' a 'Max'). None si nadie."""
    n = normalizar(nombre)
    if not n:
        return None
    mejor: tuple[int, str] | None = None
    for gerente, aliases in alias_por_gerente.items():
        for a in [gerente, *aliases]:
            an = normalizar(a)
            if an and (n == an or n.startswith(an + " ")):
                if mejor is None or len(an) > mejor[0]:
                    mejor = (len(an), gerente)
    return mejor[1] if mejor else None


def sugerir_categoria(tipo_renta: str | None, plazo: int | None, moneda: str | None) -> str | None:
    """El estante que se PROPONE para una fila sin categoría (la mesa manda).
    Reglas simples y visibles; lo que no encaja queda sin estante, no se inventa."""
    tr = (tipo_renta or "").lower()
    usd = (moneda or "").upper() == "USD"
    if "dinero" in tr:
        return "MONEY MARKET USD" if usd else "T+0 MONEY MARKET"
    if "renta fija" in tr:
        if usd:
            return "RENTA FIJA USD"
        if plazo == 0:
            return "T+0"
        if plazo == 1:
            return "T+1"
        return None
    if "mixta" in tr:
        return "RENTA MIXTA USD" if usd else "RENTA MIXTA"
    if "variable" in tr:
        return "RENTA VARIABLE"
    return None

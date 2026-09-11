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

from core.clase_activo import de_fci

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
    # Empate de longitud (el mismo alias cargado en dos gerentes) → gana la de
    # nombre alfabéticamente menor: reproducible corrida a corrida, y el job
    # avisa del alias compartido (`alias_compartidos`) para que la mesa lo resuelva.
    mejor: tuple[int, str] | None = None
    for gerente in sorted(alias_por_gerente):
        for a in [gerente, *alias_por_gerente[gerente]]:
            an = normalizar(a)
            if an and (n == an or n.startswith(an + " ")):
                if mejor is None or len(an) > mejor[0]:
                    mejor = (len(an), gerente)
    return mejor[1] if mejor else None


def alias_compartidos(alias_por_gerente: dict[str, list[str]]) -> dict[str, list[str]]:
    """Alias (normalizados) que aparecen en MÁS de una gerente → `{alias: [gerentes]}`.
    Un fondo que empiece así se asigna de forma reproducible pero arbitraria: es
    la mesa la que tiene que decidir a quién pertenece."""
    donde: dict[str, set[str]] = {}
    for g, aliases in alias_por_gerente.items():
        for a in [g, *aliases]:
            an = normalizar(a)
            if an:
                donde.setdefault(an, set()).add(g)
    return {a: sorted(gs) for a, gs in donde.items() if len(gs) > 1}


def sugerir_categoria(tipo_renta: str | None, plazo: int | None, moneda: str | None) -> str | None:
    """La CLASE DE ACTIVO que se propone para un fondo sin asset linkeado, en el
    vocabulario de Manager → ASSETS (`core/clase_activo.de_fci`: MM ARS · MM USD ·
    ARS T1 · HD T1 · RENTA VARIABLE). `plazo` no decide nada hoy: se conserva en la
    firma por si la mesa quiere distinguir T+0 de T+1 en su vocabulario. Lo que no
    encaja queda sin clase, no se inventa."""
    del plazo
    return de_fci(tipo_renta or "", moneda or "") or None

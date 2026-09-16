"""core/pareo.py — EMPAREJAR POR FICHA. La identidad no es el nombre.

Doc oficial: `docs/ACAQUANT.md` · `docs/AGENT.md` §0.y.

EL PATRÓN, Y POR QUÉ ES GENERAL
================================

Cada tanto hay que decir «este registro y aquel son la misma cosa» sin tener una
clave que los una. Pasó ya cuatro veces en este sistema, y las cuatro se
resolvieron por separado:

    las PATAS de un bono      BPOA7 (pesos) ↔ BPA7D (dólares)
    el REBAUTIZO de Aunesa    [6461] CAFCI1910 ↔ [28902] CAFCI1910
    el EMISOR de 1816         «BCO.COMAFI» ↔ «Banco Comafi»
    los SÍMBOLOS del master   curvas.instrumento ↔ especies ↔ assets.instrumento

La tentación siempre es la misma: **mirar el string**. Un sufijo, un prefijo, un
`startswith`. Y siempre falla igual, porque **el nombre es una convención de
quien lo emitió, no un dato**. El caso que lo dejó claro:

    BPOA7  →  BP[O]A7 + D  →  BPA7D      ← se cae una letra del MEDIO
    NDT25  →  NDT[2]5 + D  →  NDT5D
    SFD34  →  SFD[3]4 + D  →  SFD4D
    AL30   →  AL30   + D  →  AL30D       ← este anda, y por casualidad

El ticker está topeado en **5 caracteres**. `AL30` tiene 4, le entra la D y la
regla de sufijo funciona; cualquier base de 5 la rompe, y la va a romper siempre.
*El nombre literalmente no tiene lugar para ser la identidad.*

Lo que sí identifica es la **FICHA**: los atributos que da la fuente autoritativa
—para un bono, `underlying` + `maturity` de Primary—. Eso es un JOIN EXACTO: o
coincide o no. No hay que mantenerlo cuando cambie la convención, porque no mira
la convención.

LAS CUATRO GUARDAS, QUE SON LA MITAD DEL MÓDULO
================================================

Emparejar mal es **peor** que no emparejar: no da ningún error y el sistema
sigue, con el dato de otro. Las cuatro salieron de errores reales:

  1. **Solo la fuente AUTORITATIVA.** Primary publica los mismos papeles dos
     veces y la forma corta trae un `underlying` genérico («Bopreales - Bonos
     BCRA»): con ella los 6 BOPREALes comparten ficha y cada uno hereda las patas
     de los otros cinco. Quién es autoritativo lo decide el que llama.

  2. **Ficha COMPLETA o no hay ficha.** Media ficha no identifica a nadie, y un
     campo vacío matchea contra todos los demás campos vacíos.

  3. **Un grupo demasiado grande no se usa.** Si una ficha agrupa más de lo que
     el dominio admite, es genérica —o la fuente cambió— y hay que callarse.

  4. **«No pude mirar» nunca es «no existe».** Sin fuente se devuelve vacío y se
     DICE; jamás un veredicto negativo. Es la regla del AO29 (`AGENT.md` §0.v).

Todo acá es **PURO**: recibe las filas ya leídas. Quién las trae y de dónde es
del dominio, no de este módulo.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

# Cuántos registros puede agrupar una ficha antes de dejar de creerle, si el que
# llama no dice otra cosa. Es deliberadamente CHICO: el modo de falla que estamos
# evitando es agrupar de más, y ante la duda no se empareja.
MAX_POR_GRUPO = 12


def _clave(fila: dict, por: Sequence[str]) -> tuple[str, ...] | None:
    """La ficha de una fila, o `None` si está incompleta (guarda 2)."""
    vals = []
    for campo in por:
        v = fila.get(campo)
        v = ("" if v is None else str(v)).strip()
        if not v:
            return None
        vals.append(v.upper())
    return tuple(vals)


def agrupar(filas: Iterable[dict], *, por: Sequence[str],
            confiable: Callable[[dict], bool] | None = None,
            ) -> dict[tuple[str, ...], list[dict]]:
    """`ficha → las filas que la comparten`. PURA.

    `por` son los campos que forman la ficha (ej. `("underlying", "maturity")`).
    `confiable` decide qué filas son de la fuente autoritativa (guarda 1); sin él
    entran todas, que es lo correcto cuando la fuente ya viene filtrada.
    """
    out: dict[tuple[str, ...], list[dict]] = {}
    for f in filas or []:
        if confiable is not None and not confiable(f):
            continue
        k = _clave(f, por)
        if k is None:
            continue
        out.setdefault(k, []).append(f)
    return out


def hermanas(sujeto: Any, filas: Iterable[dict], *, por: Sequence[str],
             identidad: Callable[[dict], Any],
             confiable: Callable[[dict], bool] | None = None,
             quedarse: Callable[[dict], bool] | None = None,
             orden: Callable[[dict], Any] | None = None,
             max_por_grupo: int = MAX_POR_GRUPO,
             etiqueta: str = "") -> list[dict]:
    """Los registros que comparten FICHA con `sujeto`, sin mirarle el nombre. PURA.

    · `identidad(fila)` → cómo se reconoce al sujeto adentro de las filas.
    · `quedarse(fila)`  → qué hermanas interesan (ej. las de otra moneda). El
                          propio sujeto SIEMPRE se excluye: nadie es hermano de
                          sí mismo, y devolverlo haría que el que llama crea que
                          encontró algo.
    · `orden(fila)`     → con qué criterio se devuelven. **Sin esto el resultado
                          queda a merced del orden de llegada**, que fue
                          exactamente el bug de `BPA7C` ganándole a `BPA7D`:
                          emparejar bien y elegir mal no se ve distinto de
                          emparejar mal.

    Vacío = no se encontró **o** no se pudo confiar. Nunca es una afirmación
    negativa (guarda 4): quien necesite distinguir «no hay» de «no pude», que
    mire si `filas` venía vacío.
    """
    if sujeto is None or sujeto == "":
        return []
    idx = agrupar(filas, por=por, confiable=confiable)
    clave = next((k for k, v in idx.items()
                  if any(identidad(x) == sujeto for x in v)), None)
    if clave is None:
        return []
    grupo = idx[clave]
    if len(grupo) > max_por_grupo:
        # Guarda 3. Se LOGUEA y no se emparejea: una ficha genérica devolvería
        # resultados creíbles y equivocados, que es el peor resultado posible.
        logger.warning("pareo%s: la ficha %s agrupa %d registros (> %d) — no se "
                       "empareja; probablemente sea genérica",
                       f" {etiqueta}" if etiqueta else "", clave, len(grupo),
                       max_por_grupo)
        return []
    hs = [x for x in grupo if identidad(x) != sujeto
          and (quedarse is None or quedarse(x))]
    return sorted(hs, key=orden) if orden is not None else hs

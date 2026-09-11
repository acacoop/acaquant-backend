"""`agente/cartera.py` — LA CARTERA DE UN BONO, PROPUESTA Y CON SU FUENTE.

Doc: `docs/AGENT.md` §0.ej → habilidad `ficha_incompleta`, regla `sin_cartera`.

Mismo espíritu que `agente/clase.py` (el patrón «proponer con fuente»): la
escritura sigue siendo la de siempre (`agente.arreglos.CompletarFicha`) y una
persona confirma — o, si la regla ya existe en el catálogo, `CompletarFicha.solo`
la aplica sin que nadie apriete.

**Por qué el bono no tenía regla.** `jobs/assets_autofill` sabe derivar
FINANCIAMIENTO, FCI y DERIVADOS (OTC/agro) porque esos tres tienen una firma en
la propia `unidad`. Un BONO no la tiene: su cartera —`ARS`, `HD`, `DL`— sale de
sus EJES (moneda de denominación, ajuste), y esos ejes no viven en la `unidad`
sino en otra tabla (`mercado.curvas`) o en el censo de 1816.

    REGLA    — las reglas del JOB (financiamiento, FCI, OTC/agro), REUSADAS
    CURVA    — los EJES del bono en el master (`agente.fuentes.master`)
    1816     — los EJES del bono en el catálogo local de 1816
               (`agente.fuentes.universo_1816`, vía `core.curvas_ejes.desde_1816`)

⚠️ **LISTA CERRADA, mismo invariante que `clase.py` y `emisor.py`.** Una regla
no inventa grafías: si el valor que la regla derivaría todavía no existe en
`cartera`, la fila NO se escribe sola — viaja con `propuesto=""` y una nota.
La comparación es tolerante a grafía (`core.clase_activo.en_lista_cerrada`):
si ya existe una grafía que normaliza igual, se escribe ESA, no la de la regla.
"""
from __future__ import annotations

import logging

from agente.detectores.mercado import _es_pata_1816, _tk
from core.cartera import de_ejes
from core.clase_activo import en_lista_cerrada
from core.curvas_ejes import desde_1816

logger = logging.getLogger(__name__)

REGLA, CURVA, MIL816 = "regla", "curva", "1816"


def _reglas_job():
    """Las reglas de `jobs/assets_autofill` (`financiamiento`, `fci`,
    `derivados_otc`) y `_regla_ticker`, o `(None, None, None, None)` si el job
    no importa. **PURA salvo este import lazy** — mismo patrón que
    `emisor.por_regla` (REGLA #9: no se reimplementa ninguna regla, se llaman
    las del job)."""
    try:
        from jobs.assets_autofill import (
            _regla_derivados_otc,
            _regla_fci,
            _regla_financiamiento,
            _regla_ticker,
        )
    except Exception as e:                       # el job no importa: se sigue
        logger.warning("cartera: no pude leer las reglas del job (%s)", e)
        return None, None, None, None
    return _regla_financiamiento, _regla_fci, _regla_derivados_otc, _regla_ticker


def _indice_master(master: list[dict] | None) -> dict[str, dict]:
    """`{ticker_corto.upper(): doc}` del master (`agente.fuentes.master()`).
    **El PRIMERO gana**, mismo criterio que `clase._indice_master`."""
    out: dict[str, dict] = {}
    for d in master or []:
        clave = (d.get("ticker_corto") or "").strip().upper()
        if clave and clave not in out:
            out[clave] = d
    return out


def _indice_1816(universo_1816: dict | None) -> dict[str, dict]:
    """`{ticker.upper(): instrumento}` del censo de 1816, saltando las PATAS
    de un dual (`_es_pata_1816`): no son instrumentos, el cuadro lo tiene el
    ticker base."""
    inst = (universo_1816 or {}).get("instrumentos") or {}
    out: dict[str, dict] = {}
    for ticker, data in inst.items():
        if _es_pata_1816(ticker):
            continue
        clave = _tk(ticker)
        if clave and clave not in out:
            out[clave] = data
    return out


def _tickers_cedears(cedears: list[dict] | None) -> set[str]:
    """`{ticker_corto.upper()}` del master de CEDEARs (`fuentes.cedears_master`).
    No propone nada: es **evidencia para la nota**. Saber que un título es un
    CEDEAR no dice con qué cartera lo sigue la mesa —eso es criterio, no
    derivación—, pero convierte «no sé qué es esto» en «sé qué es y no sé cómo
    lo llaman ustedes», que es una pregunta que alguien puede contestar."""
    return {(c.get("ticker_corto") or "").strip().upper()
            for c in (cedears or []) if (c.get("ticker_corto") or "").strip()}


def proponer(filas: list[dict], master: list[dict] | None,
             universo_1816: dict | None, usadas: list[str],
             cedears: list[dict] | None = None) -> list[dict]:
    """Cada fila con `propuesto`, `fuente` y `nota`. **PURA** salvo el import
    lazy de las reglas del job. `master` es `agente.fuentes.master()`.

    `universo_1816` es **`agente.fuentes.universo_1816_local()`** —el catálogo
    persistido— y no el censo vivo: el censo son ~29 llamadas con 2,5 s de
    throttle entre cada una y esto corre cuando alguien abre una pantalla, con
    30 s de proxy del otro lado (§0.fh). Los dos tienen la misma forma, así que
    esta función no sabe cuál le pasaron: mira `_curva` y nada más.

    ⚠️⚠️ **UNA FILA SIN PROPUESTA SIEMPRE DICE POR QUÉ** (§0.fi), igual que en
    `agente/clase.py`: un guion pelado hace que «no hay regla para esto»
    (correcto) y «la regla no encontró su fuente» (un bug) se lean igual.
    """
    fin_, fci, otc, tk_regla = _reglas_job()
    indice_master = _indice_master(master)
    indice_1816 = _indice_1816(universo_1816)
    tickers_cedear = _tickers_cedears(cedears)
    out = []
    for f in filas:
        fila = {**f, "propuesto": "", "fuente": "", "nota": ""}

        ticker = (f.get("ticker") or "").strip().upper()
        if not ticker and tk_regla is not None:
            ticker = str((tk_regla(f) or {}).get("ticker") or "").strip().upper()

        if fin_ is not None:
            for regla in (fin_, fci, otc):
                if (v := (regla(f) or {}).get("cartera")):
                    fila["propuesto"], fila["fuente"] = v, REGLA
                    break

        doc = indice_master.get(ticker)
        if not fila["propuesto"] and doc:
            if (v := de_ejes(doc.get("moneda_eje") or "", doc.get("ajuste") or "")):
                fila["propuesto"], fila["fuente"] = v, CURVA

        inst = indice_1816.get(ticker)
        ejes_1816 = desde_1816(inst.get("_curva")) if inst is not None else None
        if not fila["propuesto"] and ejes_1816 is not None:
            if (v := de_ejes(ejes_1816.moneda, ejes_1816.ajuste)):
                fila["propuesto"], fila["fuente"] = v, MIL816

        # ── POR QUÉ NO, cuando no hay propuesta. Las razones se atienden
        #    distinto: una es cargar un valor, otra es completar los ejes de un
        #    bono, otra es que no hay regla y lo tiene que decidir la mesa.
        if not fila["propuesto"]:
            if fin_ is None:
                fila["nota"] = ("no pude leer las reglas del job "
                                "(`jobs/assets_autofill`) en esta corrida")
            elif doc is not None:
                fila["nota"] = (
                    f"está en mercado.curvas pero sus EJES no dan cartera "
                    f"(moneda «{(doc.get('moneda_eje') or '').strip() or '—'}», "
                    f"ajuste «{(doc.get('ajuste') or '').strip() or '—'}»): "
                    "completá los ejes del bono y sale sola")
            elif inst is not None:
                fila["nota"] = (
                    f"1816 lo pone en la curva «{inst.get('_curva') or '—'}», "
                    "que no traduce a ejes conocidos")
            elif ticker and ticker in tickers_cedear:
                fila["nota"] = ("es un CEDEAR (está en mercado.cedears): ninguna "
                                "regla dice con qué cartera los sigue la mesa")
            else:
                fila["nota"] = ("ninguna regla del job lo reconoce y no está ni "
                                "en mercado.curvas ni en el catálogo de 1816")

        # ⚠️ **LA LISTA CERRADA**, mismo invariante que `clase.py`/`emisor.py`,
        # tolerante a grafía (`en_lista_cerrada`).
        if fila["propuesto"]:
            grafia = en_lista_cerrada(fila["propuesto"], usadas or [])
            if grafia:
                fila["propuesto"] = grafia
            else:
                valor = fila["propuesto"]
                fila["propuesto"], fila["fuente"] = "", ""
                fila["nota"] = (f"la regla dice «{valor}», pero ese valor todavía "
                                "no existe en cartera: cargalo una vez a mano")
        out.append(fila)
    return out


def deterministas(filas_propuestas: list[dict]) -> list[dict]:
    """`[{"unidad", "valor"}]` de lo que se propuso Y superó la lista cerrada.
    Todo lo que sale de `proponer` es determinístico —no hay modelo en esta
    cadena—, así que alcanza con que haya `propuesto`."""
    return [{"unidad": f["unidad"], "valor": f["propuesto"]}
            for f in filas_propuestas if f["propuesto"]]

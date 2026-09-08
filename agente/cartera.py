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
"""
from __future__ import annotations

import logging

from agente.detectores.mercado import _es_pata_1816, _tk
from core.cartera import de_ejes
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


def proponer(filas: list[dict], master: list[dict] | None,
             universo_1816: dict | None, usadas: list[str]) -> list[dict]:
    """Cada fila con `propuesto`, `fuente` y `nota`. **PURA** salvo el import
    lazy de las reglas del job. `master` es `agente.fuentes.master()`;
    `universo_1816` es `agente.fuentes.universo_1816()`.
    """
    fin, fci, otc, tk_regla = _reglas_job()
    indice_master = _indice_master(master)
    indice_1816 = _indice_1816(universo_1816)
    permitidos = set(usadas or [])
    out = []
    for f in filas:
        fila = {**f, "propuesto": "", "fuente": "", "nota": ""}

        ticker = (f.get("ticker") or "").strip().upper()
        if not ticker and tk_regla is not None:
            ticker = str((tk_regla(f) or {}).get("ticker") or "").strip().upper()

        if fin is not None:
            for regla in (fin, fci, otc):
                if (v := (regla(f) or {}).get("cartera")):
                    fila["propuesto"], fila["fuente"] = v, REGLA
                    break

        if not fila["propuesto"]:
            doc = indice_master.get(ticker)
            if doc and (v := de_ejes(doc.get("moneda_eje") or "", doc.get("ajuste") or "")):
                fila["propuesto"], fila["fuente"] = v, CURVA

        if not fila["propuesto"]:
            inst = indice_1816.get(ticker)
            if inst is not None:
                ejes = desde_1816(inst.get("_curva"))
                if ejes is not None and (v := de_ejes(ejes.moneda, ejes.ajuste)):
                    fila["propuesto"], fila["fuente"] = v, MIL816

        # ⚠️ **LA LISTA CERRADA**, mismo invariante que `clase.py`/`emisor.py`.
        if fila["propuesto"] and fila["propuesto"] not in permitidos:
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

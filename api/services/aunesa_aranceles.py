"""Backfill de aranceles desde Aunesa /operaciones/informes a CashFlow.NegocioMovimientos.

Lógica core compartida entre:
  - `scripts/backfill_aranceles.py` (CLI manual, dry-run/apply, escribe logs/).
  - `POST /api/manager/aunesa/boletos/backfill` (vista UI, corre en background
    thread del proceso `api.service` y persiste progreso en `Manager.AranceelesJobRuns`
    para que el front polee).

Por cada cuenta:
  1. Pide a Aunesa /informes (en paralelo, ThreadPoolExecutor).
  2. Hace 1 sola query Mongo: comprobante → _id de los boletos de esa cuenta
     en `NegocioMovimientos`. Filtra futuros DLR (USDL) automáticamente.
  3. Para cada arancel devuelto por Aunesa, busca el _id en el dict en memoria
     y arma un `UpdateOne` que setea `aranceles` y `arancel` (atajo ARS).
  4. Flushea en lotes de _FLUSH (2000) para que el progreso parcial persista
     aunque se interrumpa.

Reintento automático: cuentas que fallan en el primer pase se reintentan con
menos workers (timeouts transitorios de Aunesa).

Idempotente: re-correrlo con el mismo rango no duplica, solo `$set`ea.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

from pymongo import UpdateOne

from api.services._negocio_arancelables import match_solo_arancelables
from api.services._negocio_futuros import match_no_futuros
from api.services.aunesa_informes import aranceles_por_boleto
from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)

_DB = "CashFlow"
_COL = "NegocioMovimientos"
# Margen para la ventana de LIQUIDACIÓN: un boleto concertado en `hasta` liquida
# días después (T+1/T+2/...) → ampliamos el techo para no perder esos boletos.
_MARGEN_LIQ_DIAS = 15
_FLUSH = 2000


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


# Tipo del callback: se invoca a cada cuenta procesada (incluyendo errores)
# con el diccionario completo de estado acumulado. El consumidor decide qué
# hacer — el CLI imprime cada 50, el endpoint hace `$set` en Mongo.
ProgressCb = Callable[[dict[str, Any]], None]


def resolver_cuentas(desde: date, hasta: date) -> list[str]:
    """Lista cuentas distintas con boletos en el rango. Ordenadas asc."""
    col = get_mongo_client()[_DB][_COL]
    raw = col.distinct(
        "id_cuenta",
        {"fecha": {"$gte": desde.isoformat(), "$lte": hasta.isoformat()}},
    )
    return sorted(str(c) for c in raw if c)


def run_backfill(
    *,
    desde: date,
    hasta: date,
    cuentas: list[str] | None = None,
    workers: int = 6,
    apply: bool = True,
    on_progress: ProgressCb | None = None,
    progress_every: int = 1,
) -> dict[str, Any]:
    """Ejecuta el backfill de aranceles. Devuelve el resumen final.

    Args:
        desde, hasta: rango de fechas de CONCERTACIÓN del boleto (YYYY-MM-DD
            en Mongo). El query a Aunesa usa LIQUIDACIÓN = concertación + margen
            (T+15 hábiles para no perder boletos que liquidan días después de
            `hasta`).
        cuentas: lista de id_cuenta a procesar. None → todas las de
            NegocioMovimientos en ese rango (default del CLI).
        workers: hilos paralelos contra Aunesa /informes.
        apply: True = escribe Mongo; False = dry-run (cuenta pero no toca DB).
        on_progress: callable opcional que recibe el dict de estado actualizado
            cada `progress_every` cuentas procesadas (y al final, sí o sí).
            Útil para que el endpoint persista progreso en `Manager.AranceelesJobRuns`.
        progress_every: 1 = callback cada cuenta procesada (lo que quiere la UI),
            50 = como el CLI (no llenar la salida estándar).

    Returns:
        Dict con `cuentas_total`, `cuentas_done`, `match`, `sin_match`,
        `escritos`, `errores` (lista) y `ejemplos` (≤5 muestras de lo que
        escribiría).
    """
    col = get_mongo_client()[_DB][_COL]
    cs = cuentas if cuentas is not None else resolver_cuentas(desde, hasta)
    liq_desde = _ddmmyyyy(desde)
    liq_hasta = _ddmmyyyy(hasta + timedelta(days=_MARGEN_LIQ_DIAS))

    state: dict[str, Any] = {
        "cuentas_total": len(cs),
        "cuentas_done":  0,
        "inf":           0,
        "match":         0,
        "sin_match":     0,
        "escritos":      0,
        "errores":       [],
        "ejemplos":      [],
    }
    pendientes: list[UpdateOne] = []

    def _emit() -> None:
        if on_progress:
            try:
                on_progress(state)
            except Exception:
                logger.exception("on_progress callback falló")

    def _flush(force: bool = False) -> None:
        if pendientes and (force or len(pendientes) >= _FLUSH):
            if apply:
                res = col.bulk_write(pendientes, ordered=False)
                state["escritos"] += res.modified_count
            pendientes.clear()

    def _procesar_db(cuenta: str, mapa: dict) -> None:
        if not mapa:
            return
        state["inf"] += len(mapa)
        # 1 sola query: comprobante → _id de los boletos de esta cuenta.
        # Excluye:
        #   - futuros DLR (unidad=USDL) — no arancelables del proyecto.
        #   - ops no arancelables (Interest payment, Cash dividend, cauciones,
        #     suscripciones FCI, etc) — el _id ni siquiera entra al lookup, así
        #     que cuando Aunesa no los devuelve no inflan el contador sin_match
        #     con false positives.
        nm_map = {
            d["comprobante"]: d["_id"]
            for d in col.find(
                {"id_cuenta": cuenta, **match_no_futuros(), **match_solo_arancelables()},
                {"_id": 1, "comprobante": 1},
            )
        }
        for boleto, aranceles in mapa.items():
            _id = nm_map.get(boleto)
            if _id is None:
                state["sin_match"] += 1
                continue
            state["match"] += 1
            pendientes.append(UpdateOne(
                {"_id": _id},
                {"$set": {
                    "aranceles": aranceles,
                    "arancel":   round(aranceles.get("ARS", 0.0), 2),
                }},
            ))
            if len(state["ejemplos"]) < 5:
                state["ejemplos"].append(
                    f"{boleto} (cuenta {cuenta}) → {aranceles}"
                )
        _flush()

    def _fetch_one(cuenta: str):
        try:
            return cuenta, aranceles_por_boleto(cuenta, liq_desde, liq_hasta), None
        except Exception as e:
            return cuenta, None, str(e)

    def _pass(subset: list[str], n_workers: int) -> list[dict[str, str]]:
        errs: list[dict[str, str]] = []
        if not subset:
            return errs
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = {ex.submit(_fetch_one, c): c for c in subset}
            for fut in as_completed(futs):
                cuenta, mapa, err = fut.result()
                state["cuentas_done"] += 1
                if err is not None:
                    errs.append({"cuenta": cuenta, "error": err})
                else:
                    _procesar_db(cuenta, mapa)
                if state["cuentas_done"] % progress_every == 0:
                    _emit()
        return errs

    errores = _pass(cs, workers)
    # Reintento: los timeouts de Aunesa suelen ser transitorios. Con menos
    # workers para no estresar más al endpoint.
    if errores:
        errores = _pass(
            [e["cuenta"] for e in errores],
            max(2, workers // 3),
        )
    _flush(force=True)
    state["errores"] = errores
    _emit()
    return state

"""Backfill de aranceles desde Aunesa /operaciones/informes a SQL
`operaciones.negocio_movimientos`.

Invocado por `POST /api/manager/aunesa/boletos/backfill` (vista UI, corre en
background thread del proceso `api.service` y persiste progreso para que el
front polee).

Procesa las cuentas en chunks de _CHUNK_CUENTAS:
  1. UNA query SQL por chunk: comprobantes arancelables de esas cuentas en
     `negocio_movimientos` (excluye futuros DLR/USDL y `op` no arancelables).
  2. Pide a Aunesa /informes por cuenta (en paralelo, ThreadPoolExecutor).
  3. Para cada arancel devuelto por Aunesa, si el comprobante existe arma un
     UPDATE que setea `aranceles` (jsonb por moneda) y `arancel` (atajo ARS).
  4. Flushea en lotes de _FLUSH (2000) para que el progreso parcial persista
     aunque se interrumpa.

Reintento automático: cuentas que fallan en el primer pase se reintentan con
menos workers (timeouts transitorios de Aunesa).

Idempotente: re-correrlo con el mismo rango no duplica, solo pisa arancel/aranceles.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

from psycopg.types.json import Json

from api.services._negocio_arancelables import OP_NO_ARANCELABLES
from api.services._negocio_futuros import EXCLUIR_UNIDADES_FUTUROS
from api.services.aunesa_informes import aranceles_por_boleto
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Margen para la ventana de LIQUIDACIÓN: un boleto concertado en `hasta` liquida
# días después (T+1/T+2/...) → ampliamos el techo para no perder esos boletos.
_MARGEN_LIQ_DIAS = 15
_FLUSH = 2000

# Filtro SQL: comprobantes arancelables de un CHUNK de cuentas (= match_no_futuros +
# match_solo_arancelables de Mongo). NULL-safe: en Mongo $nin matchea también
# los docs sin el campo → acá `IS NULL OR <> ALL`. Batcheado con = ANY: antes era
# 1 query por cuenta (N round-trips seriales); el chunk acota la memoria del preload.
_SQL_COMPROBANTES = (
    "SELECT id_cuenta, comprobante FROM negocio_movimientos "
    "WHERE id_cuenta = ANY(%(idcs)s) "
    "  AND (unidad IS NULL OR unidad <> ALL(%(futs)s)) "
    "  AND (op IS NULL OR op <> ALL(%(ops)s))"
)
_CHUNK_CUENTAS = 100
_SQL_UPDATE = (
    "UPDATE negocio_movimientos SET aranceles = %(aranceles)s, arancel = %(arancel)s "
    "WHERE comprobante = %(comprobante)s"
)


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


# Tipo del callback: se invoca a cada cuenta procesada (incluyendo errores)
# con el diccionario completo de estado acumulado. El consumidor decide qué
# hacer — el CLI imprime cada 50, el endpoint persiste el progreso.
ProgressCb = Callable[[dict[str, Any]], None]


def resolver_cuentas(desde: date, hasta: date) -> list[str]:
    """Lista cuentas distintas con boletos en el rango. Ordenadas asc."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT id_cuenta FROM negocio_movimientos "
            "WHERE fecha >= %(d)s AND fecha <= %(h)s AND id_cuenta IS NOT NULL",
            {"d": desde.isoformat(), "h": hasta.isoformat()})
        raw = [r[0] for r in cur.fetchall()]
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
            en SQL). El query a Aunesa usa LIQUIDACIÓN = concertación + margen
            (T+15 hábiles para no perder boletos que liquidan días después de
            `hasta`).
        cuentas: lista de id_cuenta a procesar. None → todas las de
            negocio_movimientos en ese rango (default del CLI).
        workers: hilos paralelos contra Aunesa /informes.
        apply: True = escribe SQL; False = dry-run (cuenta pero no toca DB).
        on_progress: callable opcional que recibe el dict de estado actualizado
            cada `progress_every` cuentas procesadas (y al final, sí o sí).
        progress_every: 1 = callback cada cuenta procesada (lo que quiere la UI),
            50 = como el CLI (no llenar la salida estándar).

    Returns:
        Dict con `cuentas_total`, `cuentas_done`, `match`, `sin_match`,
        `escritos`, `errores` (lista) y `ejemplos` (≤5 muestras de lo que
        escribiría).
    """
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
    pendientes: list[dict] = []

    def _emit() -> None:
        if on_progress:
            try:
                on_progress(state)
            except Exception:
                logger.exception("on_progress callback falló")

    def _flush(force: bool = False) -> None:
        if pendientes and (force or len(pendientes) >= _FLUSH):
            if apply:
                with get_pool().connection() as conn, conn.cursor() as cur:
                    cur.executemany(_SQL_UPDATE, pendientes)
                    rc = cur.rowcount
                    state["escritos"] += rc if rc and rc > 0 else len(pendientes)
                    conn.commit()
            pendientes.clear()

    def _comprobantes_chunk(cuentas_chunk: list[str]) -> dict[str, set]:
        """Comprobantes arancelables del chunk, agrupados por cuenta — UNA query
        (excluye futuros DLR/USDL y ops no arancelables, para que los boletos que
        Aunesa no devuelve no inflen sin_match con false positives)."""
        out: dict[str, set] = {}
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(_SQL_COMPROBANTES, {
                "idcs": cuentas_chunk,
                "futs": list(EXCLUIR_UNIDADES_FUTUROS),
                "ops":  list(OP_NO_ARANCELABLES),
            })
            for idc, comp in cur.fetchall():
                out.setdefault(str(idc), set()).add(comp)
        return out

    def _procesar_db(cuenta: str, mapa: dict, nm_set: set) -> None:
        if not mapa:
            return
        state["inf"] += len(mapa)
        for boleto, aranceles in mapa.items():
            if boleto not in nm_set:
                state["sin_match"] += 1
                continue
            state["match"] += 1
            pendientes.append({
                "comprobante": boleto,
                "aranceles":   Json(aranceles),
                "arancel":     round(aranceles.get("ARS", 0.0), 2),
            })
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
        # Chunks de _CHUNK_CUENTAS: el preload de comprobantes es 1 query por chunk
        # (vs 1 por cuenta) y la memoria queda acotada al chunk en curso.
        for i in range(0, len(subset), _CHUNK_CUENTAS):
            chunk = subset[i:i + _CHUNK_CUENTAS]
            comps = _comprobantes_chunk(chunk)
            with ThreadPoolExecutor(max_workers=n_workers) as ex:
                futs = {ex.submit(_fetch_one, c): c for c in chunk}
                for fut in as_completed(futs):
                    cuenta, mapa, err = fut.result()
                    state["cuentas_done"] += 1
                    if err is not None:
                        errs.append({"cuenta": cuenta, "error": err})
                    else:
                        _procesar_db(cuenta, mapa, comps.get(cuenta, set()))
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

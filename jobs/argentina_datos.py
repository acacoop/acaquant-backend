"""Cron: pega argentinadatos.com y persiste riesgo país / IPC en Mongo.

Escribe 3 series limpias en colecciones Mongo con shape estándar
`{fecha: 'YYYY-MM-DD', valor: float}` para que el fetcher genérico de
`services/macro._fetch_serie_macro` las consuma sin lógica adicional:

    Trading.RiesgoPais           ← /v1/finanzas/indices/riesgo-pais
    Trading.InflacionMensual     ← /v1/finanzas/indices/inflacion
    Trading.InflacionInteranual  ← /v1/finanzas/indices/inflacionInteranual

Cadencia: diaria (1 corrida/día vía cron). Riesgo país es la única que
cambia intradía pero con 1 update diario al cierre alcanza para la
foto que mostramos.

REM no se persiste todavía — requiere definir schema para manejar
múltiples indicadores por informe mensual. Roadmap aparte.

Uso:
    python -m jobs.argentina_datos                # todas las series
    python -m jobs.argentina_datos --only riesgo  # solo una
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import UpdateOne

from core.argentina_datos import (
    ArgDataError,
    get_inflacion_interanual,
    get_inflacion_mensual,
    get_riesgo_pais_serie,
)
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("argentina_datos")

# Sanity por serie — si algún punto viene fuera de rango lo descartamos.
# Misma filosofía que jobs/dolar_api: defensa contra agregador corrompido
# o cambio de schema.
SANITY = {
    "RiesgoPais":          (50,  15_000),   # bps; típico 500-3000, tope amplio
    "InflacionMensual":    (-10, 100),      # %, permite deflación leve
    "InflacionInteranual": (-50, 1_000),    # %; 2024 pasó por 300%
}


def _persistir_serie(
    coll, datos: list[dict], sanity_range: tuple[float, float], nombre: str,
) -> dict[str, Any]:
    """Escribe la serie como upsert por fecha. Devuelve stats del run."""
    lo, hi = sanity_range
    ops = []
    descartados = 0
    for d in datos:
        fecha = d.get("fecha")
        valor = d.get("valor")
        if not fecha or valor is None:
            continue
        try:
            v = float(valor)
        except (TypeError, ValueError):
            continue
        if not (lo <= v <= hi):
            logger.warning(
                "[%s] %s=%s fuera de [%s, %s] — descartado", nombre, fecha, v, lo, hi,
            )
            descartados += 1
            continue
        ops.append(UpdateOne(
            {"fecha": fecha[:10]},
            {"$set": {
                "fecha":      fecha[:10],
                "valor":      v,
                "fuente":     "argentinadatos.com",
                "updated_at": datetime.now(UTC),
            }},
            upsert=True,
        ))

    if not ops:
        return {"persistidos": 0, "descartados": descartados}

    coll.bulk_write(ops, ordered=False)
    return {"persistidos": len(ops), "descartados": descartados}


def run(solo: str | None = None) -> dict[str, Any]:
    """Fetchea y persiste las 3 series. `solo` = 'riesgo'|'ipc'|'ipcy'|None."""
    client = get_mongo_client()
    db = client["Trading"]

    resultado: dict[str, Any] = {"ok": True, "series": {}}

    # Riesgo país
    if solo in (None, "riesgo"):
        try:
            serie = get_riesgo_pais_serie()
            stats = _persistir_serie(db["RiesgoPais"], serie, SANITY["RiesgoPais"], "RiesgoPais")
            resultado["series"]["riesgo_pais"] = stats
            logger.info("RiesgoPais: %s", stats)
        except ArgDataError as e:
            resultado["ok"] = False
            resultado["series"]["riesgo_pais"] = {"error": str(e)}
            logger.error("RiesgoPais falló: %s", e)

    # Inflación mensual
    if solo in (None, "ipc"):
        try:
            serie = get_inflacion_mensual()
            stats = _persistir_serie(
                db["InflacionMensual"], serie, SANITY["InflacionMensual"], "InflacionMensual",
            )
            resultado["series"]["inflacion_mensual"] = stats
            logger.info("InflacionMensual: %s", stats)
        except ArgDataError as e:
            resultado["ok"] = False
            resultado["series"]["inflacion_mensual"] = {"error": str(e)}
            logger.error("InflacionMensual falló: %s", e)

    # Inflación interanual
    if solo in (None, "ipcy"):
        try:
            serie = get_inflacion_interanual()
            stats = _persistir_serie(
                db["InflacionInteranual"], serie,
                SANITY["InflacionInteranual"], "InflacionInteranual",
            )
            resultado["series"]["inflacion_interanual"] = stats
            logger.info("InflacionInteranual: %s", stats)
        except ArgDataError as e:
            resultado["ok"] = False
            resultado["series"]["inflacion_interanual"] = {"error": str(e)}
            logger.error("InflacionInteranual falló: %s", e)

    return resultado


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["riesgo", "ipc", "ipcy"])
    args = parser.parse_args()

    with JobRunLogger("argentina_datos") as jr:
        res = run(solo=args.only)
        jr.stats.update(res)
        if not res.get("ok"):
            jr.error("argentina_datos: al menos una serie falló")

    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
